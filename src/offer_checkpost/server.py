"""The local web server: the app's page from ``web/``, and a JSON API whose one way to change
anything is ``invoke``.

It binds 127.0.0.1 and nothing else, because ``actor`` is a field of the request body: the
human-only gate means something only while every request comes from this machine. Loopback
alone doesn't stop a web page open in the same browser from sending a request to 127.0.0.1, so
every request must also:

- name this server in its Host header, which a DNS-rebinding page can't (it names its own);
- carry no Origin but this server's, and no Sec-Fetch-Site but ``same-origin`` or ``none``;
- for ``POST /api/invoke``, be ``application/json``, which a page of another origin can send
  only after a CORS preflight. This server approves none: it sends no ``Access-Control-Allow-*``
  header, and answers ``OPTIONS`` with 405.

A request that fails any of these gets 403 and never reaches ``invoke``.

``GET /api/calls`` reads the call log without the store's lock, so the page's call-log strip
grows while an investigation's POST holds the lock: a call-log entry is appended whole and
never changed after. Every other read takes the lock, for a consistent snapshot.

A body is taken in only when everything in it can go back out as JSON: no lone surrogate (half
of an emoji, cut when a message was copied), no number too large to be finite, and nesting no
deeper than a call needs. Each of these, once kept in the state or the activity log, would
break every later answer that carried it, until a restart that loses everything in memory.

No answer carries an exception's text, which could hold a request URL with the key in it, and
the server prints nothing per request.
"""

from __future__ import annotations

import copy
import json
import math
import socketserver
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from offer_checkpost import drafts, tools
from offer_checkpost.invoke import invoke
from offer_checkpost.store import LONE_SURROGATE, Store

HOST = "127.0.0.1"
DEFAULT_PORT = 8741

_ENTRY = Path(__file__).resolve().parents[2]
WEB_DIR = _ENTRY / "web"
SAMPLES_DIR = _ENTRY / "samples" / "offers"

# The demo samples the page's "Try a sample" menu offers, each described without a verdict.
SAMPLES = (
    ("a", "Work-from-home data entry offer"),
    ("b", "Graduate engineer trainee interview invitation"),
    ("c", "Customer support offer for freshers"),
)

# open_case takes up to this many characters. A JSON encoder that escapes everything outside
# ASCII writes up to 12 bytes for one (a surrogate pair), so the cap stays above that and the
# tool's own length check, in words, is what refuses a message that is too long.
_TEXT_MAX = tools.TOOLS["open_case"].schema["properties"]["text"]["maxLength"]
MAX_BODY = 12 * _TEXT_MAX + 64 * 1024
# A refused body up to this size is read and dropped before the answer, so the client reads
# the answer instead of a reset connection; a larger one is not read at all.
_DRAIN_MAX = 4 * MAX_BODY
# The deepest a call nests is three levels (the call, its args, update_claims's fields).
MAX_DEPTH = 32

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)

_JSON = "application/json; charset=utf-8"
_HTML = "text/html; charset=utf-8"
_STATIC = {
    "/": ("index.html", _HTML),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
_API_GET = {
    "/api/state": "_state",
    "/api/calls": "_calls",
    "/api/tools": "_tools",
    "/api/samples": "_samples",
    "/api/board.html": "_board",
}
_API_POST = "/api/invoke"


@dataclass(frozen=True)
class _Answer:
    status: int
    body: bytes
    content_type: str = _JSON
    headers: tuple[tuple[str, str], ...] = ()


def _json(status: int, value: Any, *headers: tuple[str, str]) -> _Answer:
    # ASCII escapes, so text that isn't UTF-8 can't stop an answer being written.
    body = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
    return _Answer(status, body, _JSON, headers)


def _problem(status: int, outcome: str, error: str, *headers: tuple[str, str]) -> _Answer:
    return _json(status, {"ok": False, "outcome": outcome, "error": error}, *headers)


class OfferServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second program bind the same port and take its requests.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def __init__(self, store: Store, port: int, web_dir: Path):
        self.store = store
        self.web_dir = web_dir
        super().__init__((HOST, port), _Handler)

    def server_bind(self) -> None:
        # HTTPServer's own also looks the host's name up (getfqdn), which 127.0.0.1 doesn't need.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    @property
    def port(self) -> int:
        return self.server_address[1]


def make_server(
    store: Store, host: str = HOST, port: int = DEFAULT_PORT, *, web_dir: Path | None = None
) -> OfferServer:
    """A server for ``store`` on 127.0.0.1:``port`` (0 picks a free port), bound but not yet
    serving; ``serve_forever`` serves it. Any other host raises ValueError before anything is
    bound. Records the bound address in ``store.server["bind"]``. ``web_dir`` holds the page,
    ``web/`` beside ``src/`` unless given."""
    if host != HOST:
        raise ValueError(
            f"Offer Checkpost listens on {HOST} only, not {host!r}: the caller is a field of "
            "each request, so only this machine may send one"
        )
    server = OfferServer(store, port, web_dir or WEB_DIR)
    with store.lock:
        store.server["bind"] = f"{HOST}:{server.port}"
    return server


class _Handler(BaseHTTPRequestHandler):
    server: OfferServer
    # A client that stops sending mid-request frees its thread after this many seconds.
    timeout = 30

    def do_GET(self) -> None:
        self._handle()

    do_POST = do_HEAD = do_OPTIONS = do_PUT = do_PATCH = do_DELETE = do_GET

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        self._unread = self._content_length()
        try:
            answer = self._answer(path)
        except Exception as failure:
            where = path if path in _STATIC or path in _API_GET or path == _API_POST else "?"
            # The type only: an exception's text can carry a request URL with the key in it.
            print(
                f"offer_checkpost: internal error on {self.command} {where}: "
                f"{type(failure).__name__}",
                file=sys.stderr,
            )
            answer = _problem(HTTPStatus.INTERNAL_SERVER_ERROR, "error", "internal error")
        try:
            self._drain()
            self._send(answer)
        except OSError:
            # The client went away before the answer: nobody is left to tell.
            self.close_connection = True

    def _answer(self, path: str) -> _Answer:
        if refusal := self._foreign():
            return _problem(HTTPStatus.FORBIDDEN, "refused", refusal)
        if path in _STATIC or path in _API_GET:
            allowed = "GET"
        elif path == _API_POST:
            allowed = "POST"
        else:
            return _problem(HTTPStatus.NOT_FOUND, "error", "not found")
        if self.command != allowed:
            return _problem(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "error",
                f"{path} takes {allowed} only",
                ("Allow", allowed),
            )
        if allowed == "POST":
            return self._invoke()
        if path in _STATIC:
            return self._static(*_STATIC[path])
        return getattr(self, _API_GET[path])()

    def _foreign(self) -> str | None:
        """Why this request may not come from this app's own page, or None when it may."""
        port = self.server.port
        hosts = (f"{HOST}:{port}", f"localhost:{port}")
        sent = self.headers.get_all("Host") or []
        if len(sent) != 1 or sent[0].strip().lower() not in hosts:
            return f"this server answers only requests addressed to {HOST}:{port}"
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in [f"http://{h}" for h in hosts]:
            return "a request from another site's page is refused"
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site.strip().lower() not in ("same-origin", "none"):
            return "a request from another site's page is refused"
        return None

    # ---- the one route that changes anything --------------------------------------------

    def _invoke(self) -> _Answer:
        kind = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if kind != "application/json":
            return _problem(
                HTTPStatus.FORBIDDEN, "refused", "a call must be sent as application/json"
            )
        length = self._unread
        if length is None:
            return _problem(HTTPStatus.BAD_REQUEST, "error", "the Content-Length is not a number")
        if length > MAX_BODY:
            return _problem(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "error",
                f"the request is larger than {MAX_BODY} bytes",
            )
        body = self.rfile.read(length)
        self._unread = 0
        try:
            call = json.loads(
                body.decode("utf-8"), parse_constant=_no_constant, parse_float=_finite
            )
        except _NotJSON as refusal:
            return _problem(HTTPStatus.BAD_REQUEST, "error", str(refusal))
        except (UnicodeDecodeError, ValueError, RecursionError):
            call = None
        if not isinstance(call, dict):
            return _problem(
                HTTPStatus.BAD_REQUEST,
                "error",
                'the body must be a JSON object: {"tool": ..., "args": {...}, "actor": ...}',
            )
        if unfit := _unfit(call):
            return _problem(HTTPStatus.BAD_REQUEST, "error", unfit)
        # A missing or forged actor goes through too: invoke refuses it and logs the refusal.
        return _json(
            HTTPStatus.OK,
            invoke(call.get("tool"), call.get("args"), call.get("actor"), store=self.server.store),
        )

    # ---- reads ----------------------------------------------------------------------------

    def _static(self, name: str, content_type: str) -> _Answer:
        try:
            body = (self.server.web_dir / name).read_bytes()
        except FileNotFoundError:
            return _problem(HTTPStatus.NOT_FOUND, "error", "not found")
        return _Answer(HTTPStatus.OK, body, content_type)

    def _state(self) -> _Answer:
        return _json(HTTPStatus.OK, self.server.store.state())

    def _calls(self) -> _Answer:
        store = self.server.store
        # No lock (see the module docstring): the copy of the list is taken in one step, and
        # the entries in it are never changed after they are appended.
        calls = list(store.calls)
        return _json(
            HTTPStatus.OK, {"server": copy.deepcopy(store.server), "calls": copy.deepcopy(calls)}
        )

    def _tools(self) -> _Answer:
        return _json(HTTPStatus.OK, tools.listing())

    def _samples(self) -> _Answer:
        samples = [
            {"id": sid, "label": label, "text": (SAMPLES_DIR / f"{sid}.txt").read_text("utf-8")}
            for sid, label in SAMPLES
        ]
        return _json(HTTPStatus.OK, {"samples": samples})

    def _board(self) -> _Answer:
        store = self.server.store
        with store.lock:
            page = drafts.board_html(copy.deepcopy(store.board), generated_at=store.now())
        return _Answer(
            HTTPStatus.OK,
            page.encode(),
            _HTML,
            (("Content-Disposition", 'attachment; filename="offer-board.html"'),),
        )

    # ---- the wire -------------------------------------------------------------------------

    def _content_length(self) -> int | None:
        """The body's declared length: 0 when none is declared, None when it isn't a
        non-negative whole number."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            length = int(raw)
        except ValueError:
            return None
        return length if length >= 0 else None

    def _drain(self) -> None:
        if self._unread is None or self._unread > _DRAIN_MAX:
            self.close_connection = True
            return
        while self._unread > 0:
            chunk = self.rfile.read(min(self._unread, 64 * 1024))
            if not chunk:
                break
            self._unread -= len(chunk)

    def _send(self, answer: _Answer) -> None:
        if self.request_version == "HTTP/0.9":
            # Left so by a request line that could not be parsed; an HTTP/0.9 answer would
            # drop the status line and every header.
            self.request_version = "HTTP/1.0"
        self.send_response(answer.status)
        self.send_header("Content-Type", answer.content_type)
        self.send_header("Content-Length", str(len(answer.body)))
        for name, value in _SECURITY_HEADERS + answer.headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(answer.body)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None):
        # Reached only for a request the standard library could not parse (a malformed request
        # line, an unknown method): the same JSON and headers as every other answer, without
        # echoing any of the request back.
        self.close_connection = True
        self._unread = None
        try:
            self._send(_problem(code, "error", HTTPStatus(code).phrase.lower()))
        except OSError:
            pass

    def version_string(self) -> str:
        return "OfferCheckpost"

    def log_message(self, format: str, *args: Any) -> None:
        # Quiet: a request line can carry a pasted message's text in a query string.
        pass


class _NotJSON(ValueError):
    """A number Python's parser takes that JSON, and the page, can't carry back out."""


def _no_constant(name: str) -> Any:
    raise _NotJSON(f"the body holds {name}, which is not a JSON number")


def _finite(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise _NotJSON("the body holds a number too large to be finite")
    return number


def _unfit(value: Any, depth: int = 0) -> str | None:
    """Why a parsed body can't be taken in (see the module docstring), or None."""
    if isinstance(value, str):
        if LONE_SURROGATE.search(value):
            return (
                "the body holds text that is not valid Unicode: half of a character, such as "
                "an emoji cut in two when the message was copied"
            )
        return None
    if isinstance(value, dict | list):
        if depth == MAX_DEPTH:
            return f"the body is nested more than {MAX_DEPTH} levels deep"
        children = [*value, *value.values()] if isinstance(value, dict) else value
        return next(filter(None, (_unfit(child, depth + 1) for child in children)), None)
    return None
