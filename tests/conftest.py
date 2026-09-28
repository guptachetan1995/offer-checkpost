import hashlib
import json
import socket
from http import HTTPStatus

import pytest
import requests
import serpapi


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "blocked_connection: provokes a connection the socket guard refuses"
    )
    config.addinivalue_line(
        "markers",
        "loopback: talks to the app's own server on 127.0.0.1; every other address stays refused",
    )
    # Here rather than in pyproject.toml: verify.sh reinstalls the virtualenv whenever
    # pyproject.toml changes, and a marker is no reason to.
    config.addinivalue_line(
        "markers",
        "e2e: drives the page in Google Chrome through Playwright; skips when either is missing",
    )


LOOPBACK = "127.0.0.1"


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Refuses every DNS lookup and socket connection in every test, lists the attempts, and
    fails the test at teardown if there was one, even when the code under test swallowed
    the refusal as an ordinary connection error.

    A test that provokes a connection error on purpose is marked ``blocked_connection`` and
    asserts the attempts itself. A test marked ``loopback`` may also reach 127.0.0.1, where it
    runs the app's own server. A test marked ``live`` talks to SerpApi and is left alone."""
    if request.node.get_closest_marker("live"):
        yield []
        return
    loopback = request.node.get_closest_marker("loopback") is not None
    attempts = []

    def allowed(address):
        # A host name, or an address whose first item is the host.
        return loopback and (address[0] if isinstance(address, tuple) else address) == LOOPBACK

    def refuse(args):
        attempts.append(args)
        raise OSError("network access is blocked in this test")

    def guard(real):
        def call(address, *rest, **kwargs):
            return real(address, *rest, **kwargs) if allowed(address) else refuse((address, *rest))

        return call

    def guard_method(real):
        def call(self, address, *rest):
            return real(self, address, *rest) if allowed(address) else refuse((address, *rest))

        return call

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, guard(getattr(socket, name)))
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, guard_method(getattr(socket.socket, name)))
    yield attempts
    if attempts and not request.node.get_closest_marker("blocked_connection"):
        pytest.fail(f"the test tried to reach the network: {attempts[0]!r}", pytrace=False)


class SerpApiStub(requests.adapters.BaseAdapter):
    """A real ``serpapi.Client`` whose HTTP is answered by queued canned replies.

    The client, ``requests`` and ``serpapi`` all run for real, so the client still writes
    ``api_key`` into its params and ``requests`` still builds the full URL with it; only the
    transport is replaced. ``urls`` lists every request URL, key included."""

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key
        self.client = serpapi.Client(api_key=api_key, timeout=20)
        self.client.session.trust_env = False
        self.client.session.mount("https://", self)
        self.client.session.mount("http://", self)
        self.urls: list[str] = []
        self._replies: list[tuple] = []

    def reply(self, body=None, *, status=200, content_type="application/json"):
        text = body if isinstance(body, str) else json.dumps(body if body is not None else {})
        self._replies.append(("reply", status, text, content_type))
        return self

    def fail(self, exception_for_url):
        """The next request raises ``exception_for_url(url)``."""
        self._replies.append(("fail", exception_for_url))
        return self

    def send(self, request, **kwargs):
        self.urls.append(request.url)
        reply = self._replies.pop(0)
        if reply[0] == "fail":
            raise reply[1](request.url)
        _, status, text, content_type = reply
        response = requests.Response()
        response.status_code = status
        response.reason = HTTPStatus(status).phrase
        response._content = text.encode()
        response.encoding = "utf-8"
        response.headers["Content-Type"] = content_type
        response.url = request.url
        response.request = request
        return response

    def close(self):
        pass


@pytest.fixture
def serpapi_stub():
    # Built at runtime: the repository must never hold a key-shaped literal.
    return SerpApiStub(hashlib.sha256(b"offer-checkpost test key").hexdigest())
