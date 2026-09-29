"""Three search providers behind one interface: fake (tests), live (SerpApi), replay (recorded).

Every provider takes the params a check built and returns a ``SearchResult``. The params never
hold the API key: only the live provider knows it, and it hands SerpApi's client a fresh copy
each time, because ``serpapi`` 1.1.2 writes ``api_key`` into the dict it is given. The result's
``params``, the cache key and the cache file are all built from the key-less dict.

- ``FakeSearchProvider`` serves synthetic SerpApi-shaped JSON from ``tests/fixtures/serp/``,
  looked up by normalised params. A query it has no fixture for is an error.
- ``SerpApiSearchProvider`` calls SerpApi through the official client, with a 24-hour disk cache
  under ``.cache/serpapi/``. ``OFFER_CHECKPOST_NO_CACHE=1`` skips the cache and sends
  ``no_cache=true``, so every search is a fresh, counted SerpApi search. Its ``account()`` reads
  the free Account API.
- ``ReplaySearchProvider`` serves responses recorded from real SerpApi searches, scrubbed and
  dated, from ``recordings/<sample>/``. A query that was never recorded is an error; nothing is
  ever made up.

Errors reach the caller as ``SearchError`` with a fixed message chosen from the HTTP status,
never from the exception text: ``requests`` puts the full request URL, key included, into the
text of its HTTP and connection errors.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import serpapi

from offer_checkpost.scrub import scrub, scrub_account

ProviderName = Literal["fake", "live", "replay"]
CacheState = Literal["hit", "miss", "replay"]

ENTRY_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ENTRY_ROOT / ".cache" / "serpapi"
RECORDINGS_DIR = ENTRY_ROOT / "recordings"
FIXTURES_DIR = ENTRY_ROOT / "tests" / "fixtures" / "serp"
ROUTES_FILE = "routes.json"

CACHE_TTL_SECONDS = 24 * 60 * 60
# A site: search took 62-66 s on 29 Sep 2026 and plain lookups up to 23 s. SerpApi charges a
# search the client gave up on, so a shorter wait spends the search and gets nothing.
CLIENT_TIMEOUT_SECONDS = 90

# Params that change how a search is served, not what it asks.
_NOT_PART_OF_THE_QUERY = frozenset({"no_cache"})

FAKE_ACCOUNT = {
    "plan_searches_left": 250,
    "searches_per_month": 250,
    "this_month_usage": 0,
    "this_hour_searches": 0,
    "account_rate_limit_per_hour": 50,
}


class SearchError(Exception):
    """A search that could not be served. ``kind`` is one of ``key_missing``,
    ``key_rejected``, ``rate_limit``, ``bad_query``, ``http_error``, ``timeout``,
    ``connection``, ``request_failed``, ``bad_response``, ``no_fixture`` or ``not_recorded``.
    ``message`` is fixed text that never carries the key."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


Route = str | Mapping[str, Any] | SearchError


@dataclass(frozen=True)
class SearchResult:
    """One served search. ``params`` is exactly what was asked, without the key (with
    ``no_cache`` when it was sent). ``searches_spent`` is what the call costs the case's
    budget: 0 for a local cache hit, else 1, replay included, so a replayed investigation
    spends and saves what the recorded one did. ``retrieved_at`` is when SerpApi produced
    ``data`` (ISO 8601), the evidence's ``retrievedAt``: the stored time for a cache hit and
    the recording's date for replay, so old evidence never passes for fresh."""

    engine: str
    params: dict[str, Any]
    data: dict[str, Any]
    provider: ProviderName
    cache: CacheState
    ms: int
    searches_spent: int
    retrieved_at: str


def keyless(params: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``params`` without None values. Params that carry ``api_key`` are refused:
    the key is added by the live provider's client, and nowhere else."""
    if "api_key" in params:
        raise ValueError("search params must never carry api_key")
    return {name: value for name, value in params.items() if value is not None}


def _normalised(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).split())


def params_key(params: Mapping[str, Any]) -> str:
    """The canonical text of a query: key-less, ``no_cache`` dropped, values as strings with
    whitespace collapsed, names sorted. Two params that ask SerpApi the same thing share it."""
    query = {
        name: _normalised(value)
        for name, value in keyless(params).items()
        if name not in _NOT_PART_OF_THE_QUERY
    }
    return json.dumps(query, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def cache_key(params: Mapping[str, Any]) -> str:
    """The sha256 of ``params_key``: the live cache's file name."""
    return hashlib.sha256(params_key(params).encode()).hexdigest()


def _no_cache_set(environ: Mapping[str, str]) -> bool:
    return environ.get("OFFER_CHECKPOST_NO_CACHE", "").strip().lower() in ("1", "true")


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _write_json(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as tmp:
        json.dump(record, tmp, indent=2, ensure_ascii=False)
        tmp.write("\n")
    os.replace(tmp.name, path)


class SearchProvider(ABC):
    name: ProviderName

    @abstractmethod
    def search(self, params: Mapping[str, Any]) -> SearchResult:
        """Serve one search. Raises ``SearchError`` when it can't."""

    @abstractmethod
    def account(self) -> dict[str, int] | None:
        """The Account API's five counts, or None when no SerpApi account is behind the
        provider (replay)."""


class FakeSearchProvider(SearchProvider):
    """Synthetic responses for tests. ``routes`` pairs params with a fixture file name under
    ``fixtures_dir``, an inline response, or a ``SearchError`` to raise. Each search served
    spends one of the fake account's searches, so a quota guard can be tested, and is dated
    by ``clock`` as if it had just been retrieved."""

    name: ProviderName = "fake"

    def __init__(
        self,
        routes: Iterable[tuple[Mapping[str, Any], Route]] = (),
        *,
        fixtures_dir: Path = FIXTURES_DIR,
        account: Mapping[str, int] = FAKE_ACCOUNT,
        clock: Callable[[], float] = time.time,
    ):
        self._routes = {params_key(params): response for params, response in routes}
        self._fixtures_dir = fixtures_dir
        self._account = dict(account)
        self._clock = clock
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_fixtures(cls, fixtures_dir: Path = FIXTURES_DIR, **kwargs: Any) -> FakeSearchProvider:
        """Routes read from ``routes.json`` in ``fixtures_dir``: a list of
        ``{"params": {...}, "fixture": "<path under fixtures_dir>"}``. Any other key in a
        route (``sample``, ``check``) only labels it for a person reading the file."""
        routes = json.loads((fixtures_dir / ROUTES_FILE).read_text(encoding="utf-8"))
        return cls(
            ((r["params"], r["fixture"]) for r in routes), fixtures_dir=fixtures_dir, **kwargs
        )

    def search(self, params: Mapping[str, Any]) -> SearchResult:
        sent = keyless(params)
        self.calls.append(dict(sent))
        key = params_key(sent)
        if key not in self._routes:
            raise SearchError("no_fixture", f"no fake fixture for {key}")
        route = self._routes[key]
        if isinstance(route, SearchError):
            raise route
        if isinstance(route, str):
            data = json.loads((self._fixtures_dir / route).read_text(encoding="utf-8"))
        else:
            data = copy.deepcopy(dict(route))
        self._account["plan_searches_left"] -= 1
        self._account["this_month_usage"] += 1
        self._account["this_hour_searches"] += 1
        return SearchResult(
            engine=sent["engine"],
            params=sent,
            data=data,
            provider=self.name,
            cache="miss",
            ms=0,
            searches_spent=1,
            retrieved_at=_iso(self._clock()),
        )

    def account(self) -> dict[str, int]:
        return dict(self._account)


class SerpApiSearchProvider(SearchProvider):
    """Live SerpApi searches through the official client, cached on disk for 24 hours.

    ``no_cache`` (default: ``OFFER_CHECKPOST_NO_CACHE=1``) skips reading the cache and sends
    ``no_cache=true``, so SerpApi's own one-hour cache is skipped too and the search is counted;
    the fresh response still refreshes the local cache. A ``client`` is passed only by tests.
    """

    name: ProviderName = "live"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: serpapi.Client | None = None,
        cache_dir: Path = CACHE_DIR,
        no_cache: bool | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if client is None:
            key = (os.environ.get("SERPAPI_KEY", "") if api_key is None else api_key).strip()
            if not key:
                raise SearchError(
                    "key_missing", "SERPAPI_KEY is not set: add your key to .env, or use replay"
                )
            client = serpapi.Client(api_key=key, timeout=CLIENT_TIMEOUT_SECONDS)
        if no_cache is None:
            no_cache = _no_cache_set(os.environ)
        # urllib3 logs the request URL, api_key included, at DEBUG for every request and at
        # WARNING when a response header is malformed. Nothing here may log it, whatever
        # level the app's logging is set to, so urllib3 logs nothing at all.
        logging.getLogger("urllib3").setLevel(logging.CRITICAL + 1)
        self._client = client
        self._cache_dir = cache_dir
        self._no_cache = no_cache
        self._clock = clock

    def search(self, params: Mapping[str, Any]) -> SearchResult:
        started = time.perf_counter()
        sent = keyless(params)
        if self._no_cache:
            sent["no_cache"] = "true"
        path = self._cache_dir / f"{cache_key(sent)}.json"
        if not self._no_cache and (cached := self._fresh(path)) is not None:
            return self._result(sent, cached["response"], "hit", started, cached["storedAt"])
        data = self._call(lambda: self._client.search(dict(sent)))
        if isinstance(data, str):
            raise SearchError("bad_response", "SerpApi answered with something other than JSON")
        data = dict(data)
        stored_at = _iso(self._clock())
        _write_json(path, {"storedAt": stored_at, "params": sent, "response": data})
        return self._result(sent, data, "miss", started, stored_at)

    def account(self) -> dict[str, int]:
        return scrub_account(self._call(self._client.account))

    def _fresh(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        stored = datetime.fromisoformat(record["storedAt"]).timestamp()
        return record if self._clock() - stored < CACHE_TTL_SECONDS else None

    def _result(
        self,
        sent: dict[str, Any],
        data: dict[str, Any],
        cache: CacheState,
        started: float,
        retrieved_at: str,
    ) -> SearchResult:
        return SearchResult(
            engine=sent["engine"],
            params=sent,
            data=data,
            provider=self.name,
            cache=cache,
            ms=_elapsed_ms(started),
            searches_spent=0 if cache == "hit" else 1,
            retrieved_at=retrieved_at,
        )

    def _call(self, request: Callable[[], Any]) -> Any:
        # The SearchError is raised after the except block, so it carries no __context__: a
        # traceback of it can't print the original exception and the URL inside it.
        try:
            return request()
        except serpapi.HTTPConnectionError:
            error = SearchError("connection", "could not reach SerpApi")
        except serpapi.TimeoutError:
            error = SearchError("timeout", "SerpApi did not answer in time")
        except serpapi.HTTPError as e:
            error = self._http_error(e.status_code, e.error)
        except Exception:
            error = SearchError("request_failed", "the request to SerpApi failed")
        raise error

    def _http_error(self, status: int, detail: Any) -> SearchError:
        if status == 401:
            return SearchError("key_rejected", "key rejected")
        if status == 429:
            return SearchError("rate_limit", "rate limit")
        if status == 400:
            # SerpApi's own error text: redacted all the same, since it answers the request.
            text = str(detail).replace(self._client.api_key, "[key]") if detail else ""
            return SearchError("bad_query", f"bad query: {text}" if text else "bad query")
        return SearchError("http_error", f"SerpApi answered HTTP {status}")


class ReplaySearchProvider(SearchProvider):
    """Real SerpApi responses recorded earlier, served by params. Every recording under
    ``recordings_dir/<sample>/`` is loaded; when two answer the same params, the later
    recording wins. ``recorded_dates`` feeds the "recorded on <date>" banner."""

    name: ProviderName = "replay"

    def __init__(self, recordings_dir: Path = RECORDINGS_DIR):
        self._recordings: dict[str, dict[str, Any]] = {}
        for path in sorted(recordings_dir.glob("*/*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            key = params_key(record["params"])
            if key not in self._recordings or (
                record["recordedAt"] > self._recordings[key]["recordedAt"]
            ):
                self._recordings[key] = record

    @property
    def recorded_dates(self) -> tuple[str, ...]:
        """The distinct dates (YYYY-MM-DD) the loaded recordings were made on, oldest first."""
        return tuple(sorted({r["recordedAt"][:10] for r in self._recordings.values()}))

    def search(self, params: Mapping[str, Any]) -> SearchResult:
        started = time.perf_counter()
        sent = keyless(params)
        record = self._recordings.get(params_key(sent))
        if record is None:
            raise SearchError(
                "not_recorded",
                f"no recorded {sent['engine']} response for this query; replay never makes one up",
            )
        return SearchResult(
            engine=sent["engine"],
            params=sent,
            data=copy.deepcopy(record["response"]),
            provider=self.name,
            cache="replay",
            ms=_elapsed_ms(started),
            searches_spent=1,
            retrieved_at=record["recordedAt"],
        )

    def account(self) -> None:
        return None


def write_recording(
    sample: str,
    params: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    recorded_at: str,
    recordings_dir: Path = RECORDINGS_DIR,
) -> Path:
    """Scrubs a live response and writes it where ``ReplaySearchProvider`` reads it:
    ``recordings_dir/<sample>/<engine>-<short hash>.json``, with its params and date."""
    query = {
        name: value
        for name, value in keyless(params).items()
        if name not in _NOT_PART_OF_THE_QUERY
    }
    engine = query["engine"]
    path = recordings_dir / sample / f"{engine}-{cache_key(query)[:12]}.json"
    record = {
        "recordedAt": recorded_at,
        "engine": engine,
        "params": query,
        "response": scrub(engine, response),
    }
    _write_json(path, record)
    return path


def provider_from_env(environ: Mapping[str, str] = os.environ) -> SearchProvider:
    """The provider ``OFFER_CHECKPOST_PROVIDER`` names; without it, live when ``SERPAPI_KEY``
    is set and replay when it isn't."""
    key = environ.get("SERPAPI_KEY", "").strip()
    name = environ.get("OFFER_CHECKPOST_PROVIDER", "").strip() or ("live" if key else "replay")
    if name == "live":
        return SerpApiSearchProvider(key, no_cache=_no_cache_set(environ))
    if name == "replay":
        return ReplaySearchProvider()
    if name == "fake":
        return FakeSearchProvider.from_fixtures()
    raise ValueError(f"OFFER_CHECKPOST_PROVIDER must be live, replay or fake, not {name!r}")
