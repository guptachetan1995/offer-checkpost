"""The command line.

    python -m offer_checkpost investigate <file> [--provider fake|replay] [--as human|agent]
    python -m offer_checkpost serve

``investigate`` does what a person does in the app, one ``invoke`` call at a time: it opens a
case from the offer message in <file>, confirms the claims as they were extracted,
investigates, and drafts the verdict. It prints the claims, every line of the trace with its
"because", the band with the searches spent and saved, the draft verdict and the activity
log. ``--as agent`` makes every call the agent's, as an MCP client's would be; the planner's
own searches are the agent's either way. Nothing is published: that is a person's click in
the app.

Without ``--provider``, the provider is the one ``OFFER_CHECKPOST_PROVIDER`` names in the
environment, else live when ``SERPAPI_KEY`` is set there and replay when it isn't. ``fake``
serves the synthetic test fixtures; ``replay`` serves recorded SerpApi responses and never
makes one up.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from offer_checkpost.invoke import invoke
from offer_checkpost.providers import FakeSearchProvider, ReplaySearchProvider
from offer_checkpost.rules import RULES
from offer_checkpost.store import Store

PROVIDERS = {"fake": FakeSearchProvider.from_fixtures, "replay": ReplaySearchProvider}
WIDTH = 99
_LABEL = {"skipped": "skip", "reordered": "reorder", "stopped": "stop", "reused": "reuse"}
_DETAIL = " " * 9


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        print(
            "offer_checkpost: serve is not built yet: the web UI arrives in the next slice",
            file=sys.stderr,
        )
        return 2
    try:
        text = args.file.read_text(encoding="utf-8")
    except OSError as e:
        parser.error(f"cannot read {args.file}: {e.strerror}")
    provider = PROVIDERS[args.provider]() if args.provider else None
    return investigate(text, Store.from_env(environ, provider), args.actor, source=args.file)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m offer_checkpost",
        description="Checks a job offer's claims against SerpApi results before anyone pays.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("serve", help="run the web app on 127.0.0.1 (arrives in the next slice)")
    run = commands.add_parser(
        "investigate",
        help="open, confirm, investigate and draft one offer message, printing the trace",
        description="Opens a case from the offer message in FILE, confirms its claims as "
        "extracted, investigates it and drafts the verdict, printing every step. Publishes "
        "nothing.",
    )
    run.add_argument("file", type=Path, help="a text file holding the offer message")
    run.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        help="fake: the synthetic test fixtures; replay: recorded SerpApi responses. Default: "
        "OFFER_CHECKPOST_PROVIDER, else live with SERPAPI_KEY set in the environment, else "
        "replay",
    )
    run.add_argument(
        "--as",
        dest="actor",
        choices=("human", "agent"),
        default="human",
        help="who makes the calls (default: human, a person at the keyboard)",
    )
    return parser


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
