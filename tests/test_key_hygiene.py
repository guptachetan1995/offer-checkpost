"""The SerpApi key reaches SerpApi and nothing else: not the params a caller holds, not a
result, an exception, a log line, a cache key or a cache file.

``serpapi`` 1.1.2 writes ``api_key`` into the params dict it is given, and ``requests`` puts
the full URL, key included, into the text of its HTTP and connection errors. Each failure test
first shows the raw exception carrying the key, then that the provider's error does not. The
key is a 64-hex value built at runtime (``serpapi_stub``), so the repository holds none."""

import json
import logging
import socket
import traceback

import pytest
import requests
import serpapi
import urllib3

from offer_checkpost.providers import SearchError, SerpApiSearchProvider, cache_key

PARAMS = {
    "engine": "google",
    "q": 'site:brand.example "never charge"',
    "gl": "in",
    "hl": "en",
    "google_domain": "google.co.in",
}


def live(stub, tmp_path, **kwargs):
    kwargs.setdefault("no_cache", False)
    return SerpApiSearchProvider(client=stub.client, cache_dir=tmp_path / "cache", **kwargs)


def raw_error(client):
    with pytest.raises(Exception) as caught:
        client.search(dict(PARAMS))
    return caught.value


def assert_keyless(error, key, caplog):
    assert isinstance(error, SearchError)
    rendered = [
        str(error),
        repr(error),
        error.message,
        repr(error.args),
        "".join(traceback.format_exception(error)),
    ]
    assert not [text for text in rendered if key in text]
    assert error.__context__ is None and error.__cause__ is None
    assert not [r for r in caplog.records if key in r.getMessage()]


def test_the_client_gets_a_fresh_copy_and_the_callers_params_never_gain_the_key(
    tmp_path, serpapi_stub
):
    key = serpapi_stub.api_key
    received = []
    search = serpapi_stub.client.search

    def spy(params=None, **kwargs):
        received.append(params)
        return search(params, **kwargs)

    serpapi_stub.client.search = spy
    serpapi_stub.reply({"organic_results": []}).reply({"organic_results": []})
    provider = live(serpapi_stub, tmp_path, no_cache=True)
    planner_params = dict(PARAMS)

    first = provider.search(planner_params)
    second = provider.search(planner_params)

    assert planner_params == PARAMS
    assert "api_key" not in first.params and "api_key" not in second.params
    assert received[0] is not planner_params and received[1] is not received[0]
    assert received[0]["api_key"] == key, "the client writes the key into the dict it gets"
    assert all(f"api_key={key}" in url for url in serpapi_stub.urls)
    assert key not in repr(first) + json.dumps(first.params) + json.dumps(first.data)


def test_a_401_never_carries_the_key(tmp_path, serpapi_stub, caplog):
    caplog.set_level(logging.DEBUG)
    key = serpapi_stub.api_key
    serpapi_stub.reply({"error": "Invalid API key."}, status=401)
    serpapi_stub.reply({"error": "Invalid API key."}, status=401)
    provider = live(serpapi_stub, tmp_path)

    assert key in str(raw_error(serpapi_stub.client))
    with pytest.raises(SearchError) as caught:
        provider.search(PARAMS)
    assert caught.value.message == "key rejected"
    assert_keyless(caught.value, key, caplog)


@pytest.mark.blocked_connection
def test_a_connection_error_never_carries_the_key(tmp_path, serpapi_stub, no_network, caplog):
    caplog.set_level(logging.DEBUG)
    key = serpapi_stub.api_key
    client = serpapi.Client(api_key=key, timeout=20)
    client.session.trust_env = False
    provider = SerpApiSearchProvider(client=client, cache_dir=tmp_path, no_cache=False)

    raw = raw_error(client)
    assert isinstance(raw, serpapi.HTTPConnectionError) and key in str(raw)
    with pytest.raises(SearchError) as caught:
        provider.search(PARAMS)
    assert caught.value.message == "could not reach SerpApi"
    assert_keyless(caught.value, key, caplog)
    assert no_network


def test_a_timeout_never_carries_the_key(tmp_path, serpapi_stub, caplog):
    caplog.set_level(logging.DEBUG)
    key = serpapi_stub.api_key

    def timeout(url):
        return requests.exceptions.ReadTimeout(f"Read timed out. (url: {url})")

    serpapi_stub.fail(timeout).fail(timeout)
    provider = live(serpapi_stub, tmp_path)

    assert key in str(raw_error(serpapi_stub.client))
    with pytest.raises(SearchError) as caught:
        provider.search(PARAMS)
    assert_keyless(caught.value, key, caplog)


def test_an_unwrapped_requests_failure_never_carries_the_key(tmp_path, serpapi_stub, caplog):
    caplog.set_level(logging.DEBUG)
    key = serpapi_stub.api_key
    serpapi_stub.fail(requests.exceptions.InvalidURL).fail(requests.exceptions.InvalidURL)
    provider = live(serpapi_stub, tmp_path)

    assert key in str(raw_error(serpapi_stub.client))
    with pytest.raises(SearchError) as caught:
        provider.search(PARAMS)
    assert caught.value.kind == "request_failed"
    assert_keyless(caught.value, key, caplog)


def test_serpapis_own_error_text_is_redacted_if_it_ever_echoes_the_key(
    tmp_path, serpapi_stub, caplog
):
    key = serpapi_stub.api_key
    serpapi_stub.reply({"error": f"Unsupported value {key}."}, status=400)
    with pytest.raises(SearchError) as caught:
        live(serpapi_stub, tmp_path).search(PARAMS)
    assert caught.value.message == "bad query: Unsupported value [key]."
    assert_keyless(caught.value, key, caplog)


def test_the_account_api_key_and_email_never_leave_the_provider(tmp_path, serpapi_stub):
    key = serpapi_stub.api_key
    serpapi_stub.reply(
        {
            "api_key": key,
            "account_email": "owner@mail.example",
            "plan_searches_left": 240,
            "searches_per_month": 250,
            "this_month_usage": 10,
            "this_hour_searches": 1,
            "account_rate_limit_per_hour": 50,
        }
    )
    counts = live(serpapi_stub, tmp_path).account()
    assert "api_key" not in counts and "account_email" not in counts
    assert key not in repr(counts)


@pytest.mark.parametrize("no_cache", [False, True])
def test_the_cache_key_and_cache_files_never_hold_the_key(tmp_path, serpapi_stub, no_cache):
    key = serpapi_stub.api_key
    serpapi_stub.reply({"organic_results": [{"title": "Beware of fake offers"}]})
    live(serpapi_stub, tmp_path, no_cache=no_cache).search(PARAMS)

    files = list((tmp_path / "cache").iterdir())
    assert [f.name for f in files] == [f"{cache_key(PARAMS)}.json"]
    for f in files:
        assert key not in f.name and key not in f.read_text()
        assert "api_key" not in json.loads(f.read_text())["params"]


def test_the_provider_keeps_no_readable_copy_of_the_key(tmp_path, serpapi_stub):
    provider = live(serpapi_stub, tmp_path)
    assert serpapi_stub.api_key not in repr(provider) + repr(vars(provider))


URLLIB3_LOGGERS = ("urllib3.connectionpool", "urllib3.connection", "urllib3.response")


def test_urllib3_logs_nothing_at_any_level(tmp_path, serpapi_stub, caplog):
    caplog.set_level(logging.DEBUG)
    live(serpapi_stub, tmp_path)
    for name in URLLIB3_LOGGERS:
        assert not logging.getLogger(name).isEnabledFor(logging.CRITICAL), name


def test_a_malformed_response_header_never_logs_the_key(
    tmp_path, serpapi_stub, monkeypatch, caplog
):
    """urllib3 logs the full request URL at WARNING when a response header line is
    malformed. The response is played over a local socket pair: nothing leaves the process."""
    key = serpapi_stub.api_key
    peers = []

    def local_socket(connection):
        ours, theirs = socket.socketpair()
        theirs.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nBadHeaderNoColon\r\n"
            b"Connection: close\r\n\r\n{}"
        )
        theirs.shutdown(socket.SHUT_WR)
        peers.append(theirs)
        return ours

    monkeypatch.setattr(urllib3.connection.HTTPConnection, "_new_conn", local_socket)
    client = serpapi.Client(api_key=key, timeout=20)
    client.session.trust_env = False
    client.BASE_DOMAIN = "http://serpapi.invalid"

    caplog.set_level(logging.WARNING, logger="urllib3")
    client.search(dict(PARAMS))
    assert [r for r in caplog.records if key in r.getMessage()], "urllib3 logs the key here"

    caplog.clear()
    caplog.set_level(logging.DEBUG)
    provider = SerpApiSearchProvider(client=client, cache_dir=tmp_path, no_cache=True)
    assert provider.search(PARAMS).cache == "miss"
    assert not [r for r in caplog.records if key in r.getMessage()]
    for peer in peers:
        peer.close()
