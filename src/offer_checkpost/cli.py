"""The command line.

    python -m offer_checkpost serve [--port N] [--provider fake|replay]
    python -m offer_checkpost investigate <file> [--provider fake|replay] [--as human|agent]
    python -m offer_checkpost mcp [--url http://127.0.0.1:8741]

``serve`` runs the web app on 127.0.0.1 (port ``--port``, else ``PORT``, else 8741) until
Ctrl-C. It prints the address to open, with the link token that makes the browser opening it
the person, and the provider serving searches; then one line saying so. The address works
once: each time it is opened, ``serve`` prints the next one, which makes another browser the
person instead. Anything else that talks to the server is the agent.

``investigate`` does what a person does in the app, one ``invoke`` call at a time: it opens a
case from the offer message in <file>, confirms the claims as they were extracted,
investigates, and drafts the verdict. It prints the claims, every line of the trace with its
"because", the band with the searches spent and saved, the draft verdict and the activity
log. ``--as agent`` makes every call the agent's, as an MCP client's would be; the planner's
own searches are the agent's either way. Nothing is published: that is a person's click in
the app.

``mcp`` serves the agent's tools to an MCP client over stdio, from the app ``serve`` runs at
``--url`` (else on 127.0.0.1 at ``PORT``, else 8741); see ``mcp_server``. Every call it makes is
the agent's. It takes the app's bare address: the one with the token is the person's.

For ``serve`` and ``investigate``, without ``--provider``, the provider is the one
``OFFER_CHECKPOST_PROVIDER`` names in the environment, else live when ``SERPAPI_KEY`` is set
there and replay when it isn't. ``fake`` serves the synthetic test fixtures; ``replay`` serves
recorded SerpApi responses and never makes one up. Run as a program, the environment is the
process's own over the settings in ``.env`` in the working directory, so a key kept in ``.env``
is found without being exported.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from offer_checkpost import mcp_server
from offer_checkpost.invoke import invoke
from offer_checkpost.providers import FakeSearchProvider, ReplaySearchProvider, SearchError
from offer_checkpost.rules import RULES
from offer_checkpost.server import DEFAULT_PORT, HOST, make_server
from offer_checkpost.store import Store

PROVIDERS = {"fake": FakeSearchProvider.from_fixtures, "replay": ReplaySearchProvider}
WIDTH = 99
_LABEL = {"skipped": "skip", "reordered": "reorder", "stopped": "stop", "reused": "reuse"}
_DETAIL = " " * 9
SESSION_LINE = (
    "That address works once, and makes the browser that opens it the person; anything else "
    "talking to this server is the agent."
)
OPENED_LINE = (
    "The address was opened: that browser is the person. To make another browser the person "
    "instead, open {url}"
)
_PROVIDER_HELP = (
    "fake: the synthetic test fixtures; replay: recorded SerpApi responses. Default: "
    "OFFER_CHECKPOST_PROVIDER, else live with SERPAPI_KEY set in the environment or .env, else "
    "replay"
)


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        port = _port(parser, args.port, environ)
        return serve(_store(parser, args, environ), port)
    if args.command == "mcp":
        return mcp_server.run(_app_url(parser, args.url, environ))
    try:
        text = args.file.read_text(encoding="utf-8")
    except OSError as e:
        parser.error(f"cannot read {args.file}: {e.strerror}")
    return investigate(text, _store(parser, args, environ), args.actor, source=args.file)


def settings(environ: Mapping[str, str] = os.environ, path: Path = Path(".env")) -> dict[str, str]:
    """``environ`` over the ``NAME=value`` lines of ``path``, when there is such a file: a
    variable the environment sets, even to nothing, wins over the file. Blank lines and ``#``
    comments are skipped, an ``export`` prefix and quotes around a value are dropped, and nothing
    is expanded. The values are passed on, never printed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return dict(environ)
    found = {}
    for line in lines:
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or name.startswith("#"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        found[name] = value
    return {**found, **environ}


def _store(
    parser: argparse.ArgumentParser, args: argparse.Namespace, environ: Mapping[str, str]
) -> Store:
    """The store the settings describe, or a usage error saying which setting is wrong: a
    judge who sets the provider to live without a key gets one line, not a traceback."""
    for name in ("OFFER_CHECKPOST_MAX_SEARCHES", "OFFER_CHECKPOST_QUOTA_RESERVE"):
        raw = environ.get(name, "").strip()
        if raw and not raw.isdigit():
            parser.error(f"{name} must be a whole number, not {raw!r}")
    provider = PROVIDERS[args.provider]() if args.provider else None
    try:
        return Store.from_env(environ, provider)
    except SearchError as failure:
        parser.error(failure.message)
    except ValueError as failure:
        parser.error(str(failure))


def _port(parser: argparse.ArgumentParser, flag: int | None, environ: Mapping[str, str]) -> int:
    raw = str(flag) if flag is not None else environ.get("PORT", "").strip() or str(DEFAULT_PORT)
    try:
        port = int(raw)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        parser.error(f"the port must be a whole number from 0 to 65535, not {raw!r}")
    return port


def _app_url(parser: argparse.ArgumentParser, flag: str | None, environ: Mapping[str, str]) -> str:
    """The running app's address for ``mcp``: ``flag``, else 127.0.0.1 at ``PORT``, else 8741.
    Only the app's own bare address is taken: a path or query (the token address ``serve``
    prints is the person's, and the adapter is the agent) or another host is a usage error.
    ``localhost`` becomes 127.0.0.1: it can resolve to ::1 first, where the app doesn't listen
    and any other program may, and would then get the pasted messages and serve its own tool
    descriptions."""
    if flag is None:
        port = _port(parser, None, environ)
        if port == 0:
            parser.error(
                "PORT is 0, which is no app's port: give --url http://127.0.0.1:<port>, with the "
                "port in the address serve printed and without the token"
            )
        return f"http://{HOST}:{port}"
    parts = urlsplit(flag)
    try:
        port = parts.port
    except ValueError:
        port = None
    if (
        parts.scheme != "http"
        or parts.hostname not in (HOST, "localhost")
        or not port
        or parts.username is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        # The value isn't echoed: it may be the token address, which serve alone prints.
        parser.error(
            "--url must be the app's bare address, http://127.0.0.1:<port>, without the token: "
            "the adapter is the agent, never the person"
        )
    return f"http://{HOST}:{port}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m offer_checkpost",
        description="Checks a job offer's claims against SerpApi results before anyone pays.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    web = commands.add_parser(
        "serve",
        help="run the web app on 127.0.0.1 until Ctrl-C",
        description="Serves the web app on 127.0.0.1 only, until Ctrl-C. Run it in your own "
        "terminal and open the address it prints in a browser on this machine: the token in it "
        "makes that browser the person, once, and anything else that talks to the app is the "
        "agent. Whatever reads this command's output can act as the person, so never start it "
        "through an agent's shell.",
    )
    web.add_argument(
        "--port",
        type=int,
        help=f"the port to listen on; 0 picks a free one. Default: PORT, else {DEFAULT_PORT}",
    )
    web.add_argument("--provider", choices=sorted(PROVIDERS), help=_PROVIDER_HELP)
    run = commands.add_parser(
        "investigate",
        help="open, confirm, investigate and draft one offer message, printing the trace",
        description="Opens a case from the offer message in FILE, confirms its claims as "
        "extracted, investigates it and drafts the verdict, printing every step. Publishes "
        "nothing.",
    )
    run.add_argument("file", type=Path, help="a text file holding the offer message")
    run.add_argument("--provider", choices=sorted(PROVIDERS), help=_PROVIDER_HELP)
    run.add_argument(
        "--as",
        dest="actor",
        choices=("human", "agent"),
        default="human",
        help="who makes the calls (default: human, a person at the keyboard)",
    )
    adapter = commands.add_parser(
        "mcp",
        help="serve the agent's tools to an MCP client over stdio, from the running app",
        description="Speaks MCP (JSON-RPC 2.0, one message a line) on stdin and stdout, and "
        "passes each tool call to the app serve is running, as the agent. Start the app first.",
    )
    adapter.add_argument(
        "--url",
        help=f"the running app's address, without the token. Default: http://{HOST}:PORT, "
        f"else http://{HOST}:{DEFAULT_PORT}",
    )
    return parser


def serve(store: Store, port: int) -> int:
    """Serves the app for ``store`` on 127.0.0.1:``port`` until Ctrl-C, or until the server is
    shut down from another thread. Returns 0, or 1 when the port can't be listened on."""
    try:
        server = make_server(store, HOST, port, on_open=_opened)
    except OSError as e:
        print(f"offer_checkpost: cannot listen on {HOST}:{port}: {e.strerror}", file=sys.stderr)
        return 1
    print(f"Offer Checkpost on {server.url} · {_describe(store)}")
    print(SESSION_LINE, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _opened(url: str) -> None:
    print(OPENED_LINE.format(url=url), flush=True)


def investigate(text: str, store: Store, actor: str, *, source: Path | str = "the message") -> int:
    """Runs the four calls and prints what they did. Returns 0, or 1 when a call was refused
    or a search failed, so the investigation is incomplete."""
    print(f"Offer Checkpost · investigate {source} · {_describe(store)} · as {actor}")

    opened = invoke("open_case", {"text": text}, actor, store=store)
    if not opened["ok"]:
        return _refused("open_case", opened)
    case_id = opened["result"]["id"]
    confirmed = invoke("update_claims", {"case_id": case_id, "confirm": True}, actor, store=store)
    if not confirmed["ok"]:
        return _refused("update_claims", confirmed)
    print(f"\n{case_id}: claims confirmed as extracted")
    print("\n".join(claim_lines(confirmed["result"]["claims"])))

    investigated = invoke("investigate", {"case_id": case_id}, actor, store=store)
    if not investigated["ok"]:
        return _refused("investigate", investigated)
    print("\nTrace")
    print("\n".join(trace_lines(investigated["result"])))
    print()
    print(budget_line(investigated["result"]))

    drafted = invoke("draft_verdict", {"case_id": case_id}, actor, store=store)
    print("\nDraft verdict (a draft: only a person publishes)")
    print("\n".join(summary_lines(drafted["result"]["summary"])))

    print("\nActivity log")
    print("\n".join(activity_lines(store.state()["activityLog"])))

    if investigated["result"]["budget"]["stoppedBecause"] == "search_error":
        print("offer_checkpost: the investigation is incomplete: a search failed", file=sys.stderr)
        return 1
    return 0


def _describe(store: Store) -> str:
    inner = store.provider.inner
    if isinstance(inner, ReplaySearchProvider):
        dates = ", ".join(inner.recorded_dates) or "nothing recorded yet"
        return f"provider replay (recorded SerpApi responses: {dates}; not live)"
    return f"provider {inner.name}"


def _refused(tool: str, out: dict[str, Any]) -> int:
    print(f"offer_checkpost: {tool} {out['outcome']}: {out['error']}", file=sys.stderr)
    return 1


# ---- rendering -------------------------------------------------------------------------------


def claim_lines(claims: Mapping[str, Any]) -> list[str]:
    rows = [
        (name, claims[name][key])
        for name, key in (
            ("company", "value"),
            ("role", "value"),
            ("city", "value"),
            ("pay", "raw"),
            ("fee", "raw"),
        )
        if claims[name] is not None
    ]
    rows += [
        (c["kind"], c["value"] + (" (a placeholder: never searched)" if c["synthetic"] else ""))
        for c in claims["contacts"]
    ]
    rows += [("link", link["value"]) for link in claims["links"]]
    return [f"  {name:<8} {value}" for name, value in rows] or ["  (no claims extracted)"]


def trace_lines(investigation: Mapping[str, Any]) -> list[str]:
    """Each line of an investigation's trace (a case, or what ``investigate`` returned) as a
    heading (the step, or skip / reorder / stop / reuse), then its "because", the reader's
    finding, the signals it fired and the band after it."""
    signals = {s["id"]: s for s in investigation["signals"]}
    out = []
    for line in investigation["trace"]:
        label = _LABEL[line["action"]] if line["step"] is None else f"step {line['step']}"
        head = [line["tool"]]
        if line["action"] == "failed":
            head.append(f"{line['engine']} · FAILED")
        elif line["cache"] is not None:
            head.append(f"{line['engine']} ({line['provider']}, cache {line['cache']})")
            head.append(_searches(line["searchesSpent"]))
        head.append(line["actor"])
        if line["reusedFrom"]:
            head.append(f"reused from {line['reusedFrom']}")
        out.append(f"{label:<8} " + " · ".join(head))
        out += _detail("because", line["because"])
        if line["note"]:
            out += _detail("found", line["note"])
        for sid in line["signalsAdded"]:
            out += _detail("signal", f"{sid} {_rule(signals[sid]['rule'])}")
        if line["action"] == "ran":
            out += _detail("band", line["band"])
    return out


def budget_line(result: Mapping[str, Any]) -> str:
    budget = result["budget"]
    parts = [
        f"band {result['band']}",
        f"{_searches(budget['spent'])} spent of {budget['maxSearches']}",
    ]
    if budget["saved"]:
        parts.append(f"{budget['saved']} saved")
    parts.append(f"stopped: {budget['stoppedBecause']}")
    return " · ".join(parts)


def summary_lines(summary: str) -> list[str]:
    out = []
    for line in summary.split("\n"):
        hang = "    " if line.startswith("- ") else "  "
        out += textwrap.wrap(
            line,
            WIDTH,
            initial_indent="  ",
            subsequent_indent=hang,
            break_long_words=False,
            break_on_hyphens=False,
        )
    return out


def activity_lines(log: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        f"  {e['actor']:<6} {e['tool']:<24} {e['result']}"
        + (f": {e['reason']}" if e.get("reason") else "")
        for e in log
    ]


def _rule(name: str) -> str:
    rule = RULES[name]
    grade = rule.direction if rule.severity is None else f"{rule.direction}, {rule.severity}"
    return f"{name} ({grade})"


def _searches(n: int) -> str:
    return f"{n} search" if n == 1 else f"{n} searches"


def _detail(name: str, text: str) -> list[str]:
    return textwrap.wrap(
        text,
        WIDTH,
        initial_indent=f"{_DETAIL}{name:<8} ",
        subsequent_indent=_DETAIL + " " * 9,
        break_long_words=False,
        break_on_hyphens=False,
    )
