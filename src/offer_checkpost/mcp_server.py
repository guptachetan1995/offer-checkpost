"""The MCP adapter: the agent's tools, served to an MCP client over stdio from the running app.

    python -m offer_checkpost mcp [--url http://127.0.0.1:8741]

It is a thin client of the app ``serve`` runs, not a second copy of it: ``tools/list`` is the
app's ``GET /api/tools``, and ``tools/call`` is one ``POST /api/invoke``, the path the page's
own buttons take. So the agent and the person work on one store, and the person watches the
agent's calls land in the page's activity log as they are made.

Every call it makes is the agent's by construction: it never names an actor, and it never has
the session cookie that makes a browser the person, so the server counts it as the agent's.
The human-only verbs aren't in the tool list it serves; a client that calls one anyway gets the
app's refusal back as a tool error, and the refusal is in the activity log. The adapter keeps
no state of its own, so it has nothing to fall out of step with.

The transport is newline-delimited JSON-RPC 2.0: one message per line on stdin, one answer per
line on stdout. Nothing else is ever written to stdout, where a stray line would break the
client's reading of every answer after it; diagnostics go to stderr. Answers are ASCII-escaped
JSON, so no text a client sends can stop one being written. The calls carry pasted offer
messages, so they go to 127.0.0.1 directly and never through a proxy the environment names.
"""

from __future__ import annotations

import http.client
import json
import sys
import urllib.error
import urllib.request
from typing import Any, BinaryIO

from offer_checkpost import __version__

# Newest first: a client asking for another version is answered with the newest.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# JSON-RPC's range for an implementation's own server errors: here, no app to ask.
APP_UNAVAILABLE = -32000

# An investigation is one POST that can spend minutes of live searching.
TIMEOUT = 300

INSTRUCTIONS = (
    "Offer Checkpost checks a forwarded job offer's claims (company, role, city, pay, any fee, "
    "the recruiter's contacts) against SerpApi search results, and drafts a verdict that cites "
    "its evidence. These tools act on the Offer Checkpost app running on this machine, as the "
    "agent: open_case with the message, update_claims to correct the claims or confirm them "
    "with confirm: true, investigate, then draft_verdict, draft_recruiter_reply or "
    "draft_cybercrime_report. The person watches each call land in the app's activity log, "
    "and the app marks the claims the agent confirmed as the agent's, not the person's. "
    "Publishing a verdict to the Offer Board, retracting one, recording what the person decided "
    "and running checks past a decisive result are the person's, in the app: no tool here does "
    "them, and a call that tries is refused and logged."
)

# No proxy, and no cookie jar: the adapter holds no session, so it can only be the agent.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def not_running(url: str) -> str:
    return (
        f"Offer Checkpost is not running at {url}: start it with python -m offer_checkpost serve"
    )


class AppUnavailable(Exception):
    """No usable answer from the app. ``message`` says why, and what to do about it."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class _Invalid(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def run(url: str, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    """Answers the JSON-RPC messages on ``stdin``, one line each, until it closes. Returns 0."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    try:
        while line := stdin.readline():
            if not line.strip():
                continue
            answer = handle_line(url, line)
            if answer is not None:
                stdout.write(answer + b"\n")
                stdout.flush()
    except (KeyboardInterrupt, BrokenPipeError):
        # The client stopped the adapter, or went away: nobody is left to answer.
        pass
    return 0


def handle_line(url: str, line: bytes) -> bytes | None:
    """The answer to one line, as the bytes of one JSON-RPC message; None for a notification or
    a response."""
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        answer = _error(
            None, PARSE_ERROR, "the line is not JSON: send one JSON-RPC message a line"
        )
    else:
        answer = handle(url, message)
    return None if answer is None else json.dumps(answer, ensure_ascii=True).encode("ascii")


def handle(url: str, message: Any) -> dict[str, Any] | None:
    """The answer to one parsed message, or None when it gets none."""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "a message is one JSON-RPC 2.0 object, not a batch")
    if "method" not in message and ("result" in message or "error" in message):
        # A response, and this adapter sends no requests to answer.
        return None
    known = "id" in message
    ident = message.get("id")
    if known and not _is_id(ident):
        return _error(None, INVALID_REQUEST, "id must be a string or a whole number")
    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _error(ident, INVALID_REQUEST, 'a request needs "jsonrpc": "2.0" and a method')
    if not known:
        return None
    try:
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise _Invalid(INVALID_PARAMS, "params must be an object")
        if method not in _METHODS:
            raise _Invalid(METHOD_NOT_FOUND, f"method not found: {method[:64]!r}")
        return {"jsonrpc": "2.0", "id": ident, "result": _METHODS[method](url, params)}
    except _Invalid as invalid:
        return _error(ident, invalid.code, invalid.message)
    except AppUnavailable as unavailable:
        return _error(ident, APP_UNAVAILABLE, unavailable.message)
    except Exception as failure:
        # The type only, as the app's own server does; the client is told the call failed.
        print(f"offer_checkpost mcp: internal error: {type(failure).__name__}", file=sys.stderr)
        return _error(ident, INTERNAL_ERROR, "internal error")


def _is_id(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _error(ident: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


# ---- methods --------------------------------------------------------------------------------


def _initialize(url: str, params: dict[str, Any]) -> dict[str, Any]:
    asked = params.get("protocolVersion")
    if not isinstance(asked, str):
        raise _Invalid(INVALID_PARAMS, "initialize needs protocolVersion, as text")
    return {
        "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "offer-checkpost", "version": __version__},
        "instructions": INSTRUCTIONS,
    }


def _ping(url: str, params: dict[str, Any]) -> dict[str, Any]:
    return {}


def _tools_list(url: str, params: dict[str, Any]) -> dict[str, Any]:
    listed = _exchange(url, "/api/tools")
    if not isinstance(listed, list):
        raise AppUnavailable(f"what answers at {url} is not Offer Checkpost: it has no tool list")
    return {
        "tools": [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in listed
        ]
    }


def _tools_call(url: str, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str):
        raise _Invalid(INVALID_PARAMS, "tools/call needs the tool's name, as text")
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise _Invalid(INVALID_PARAMS, "tools/call arguments must be an object")
    try:
        # Only the tool and its arguments: who is calling is the server's to decide.
        envelope = _exchange(url, "/api/invoke", {"tool": name, "args": args})
    except AppUnavailable as unavailable:
        return _text(unavailable.message, error=True)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
        return _text(
            f"what answers at {url} is not Offer Checkpost: it did not run the call", True
        )
    return _text(json.dumps(envelope, indent=2, ensure_ascii=False), error=not envelope["ok"])


def _text(text: str, error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


_METHODS = {
    "initialize": _initialize,
    "ping": _ping,
    "tools/list": _tools_list,
    "tools/call": _tools_call,
}


# ---- the app --------------------------------------------------------------------------------


def _exchange(url: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """The app's answer to one GET (or, with ``body``, one POST) of ``path``, parsed. The app
    answers a call it refused before ``invoke`` (403, 400, 413, 500) with an envelope too, so
    that is returned like any other."""
    request = urllib.request.Request(url + path, headers={"Accept": "application/json"})
    if body is not None:
        request.data = json.dumps(body, ensure_ascii=True).encode("ascii")
        request.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as answer:
        with answer:
            raw = answer.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as failure:
        if isinstance(failure, TimeoutError) or isinstance(
            getattr(failure, "reason", None), TimeoutError
        ):
            raise AppUnavailable(
                f"Offer Checkpost at {url} did not answer within {TIMEOUT} seconds; a call may "
                "still have run, and the app's activity log shows what did"
            ) from None
        raise AppUnavailable(not_running(url)) from None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AppUnavailable(
            f"what answers at {url} is not Offer Checkpost: its answer is not JSON"
        ) from None
