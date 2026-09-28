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


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Refuses every DNS lookup and socket connection in every test, lists the attempts, and
    fails the test at teardown if there was one, even when the code under test swallowed
    the refusal as an ordinary connection error.

    A test that provokes a connection error on purpose is marked ``blocked_connection`` and
    asserts the attempts itself. A test marked ``live`` talks to SerpApi and is left alone."""
    if request.node.get_closest_marker("live"):
        yield []
        return
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise OSError("network access is blocked in this test")

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a: refuse(*a))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, *a: refuse(*a))
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
