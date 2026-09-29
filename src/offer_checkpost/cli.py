"""The command line.

    python -m offer_checkpost serve [--port N] [--provider fake|replay]
    python -m offer_checkpost investigate <file> [--provider fake|replay] [--as human|agent]
    python -m offer_checkpost mcp [--url http://127.0.0.1:8741]
    python -m offer_checkpost record <sample>=<file> [<sample>=<file> ...] [--max-total N]

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

``record`` runs each sample offer once on live SerpApi, the way a person runs it in the app, and
writes every response it gets, scrubbed and dated, to ``recordings/<sample>/``, where replay
finds it by the same key-less params the planner sends. It needs the live provider: a key in
the environment or ``.env``, and ``OFFER_CHECKPOST_PROVIDER`` unset or ``live``. Before any
search it reads the free Account API, prints its five counts, and refuses to start when the
worst case (the per-case budget for each sample, and again for the remaining checks of ``a``
and ``c``) would take this month's usage past ``--max-total`` (default 40), leave fewer
searches than the quota reserve, or pass the hourly limit. Each sample is opened, confirmed,
investigated and its verdict drafted, every call a person's through ``invoke``; for ``a`` and
``c``, after a decisive stop, the remaining checks run and the verdict is drafted again; then
the recruiter reply and the 1930 summary are drafted, which search nothing. A response the
local cache serves is recorded too, dated when SerpApi returned it, and charged to the case's
budget as replay charges it, 1 search, so the recording run stops where replay will. It prints
each sample's trace, band, searches spent and saved and the files written, the Account API
counts again and the searches SerpApi counted, then plays every sample again from the
recordings alone, with no key and no network, and says whether each gives the same trace and
band. It writes ``recordings/NOTICE.md`` when it is missing.

For ``serve`` and ``investigate``, without ``--provider``, the provider is the one
``OFFER_CHECKPOST_PROVIDER`` names in the environment, else live when ``SERPAPI_KEY`` is set
there and replay when it isn't. ``fake`` serves the synthetic test fixtures; ``replay`` serves
recorded SerpApi responses and never makes one up. Run as a program, the environment is the
process's own over the settings in ``.env`` in the working directory, so a key kept in ``.env``
is found without being exported.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from offer_checkpost import mcp_server
from offer_checkpost.invoke import invoke
from offer_checkpost.providers import (
    RECORDINGS_DIR,
    CacheState,
    FakeSearchProvider,
    ReplaySearchProvider,
    SearchError,
    SearchProvider,
    SearchResult,
    provider_from_env,
    write_recording,
)
from offer_checkpost.rules import RULES
from offer_checkpost.scrub import clean_text
from offer_checkpost.server import DEFAULT_PORT, HOST, make_server
from offer_checkpost.store import IST, Store, well_formed

PROVIDERS = {"fake": FakeSearchProvider.from_fixtures, "replay": ReplaySearchProvider}
DEFAULT_MAX_TOTAL = 40
# The demo samples whose decisive stop a person follows with the remaining checks, so replay can
# answer that button with no key: the impersonation (a) and the unknown firm (c).
REMAINING_CHECKS_SAMPLES = ("a", "c")
NOTICE_FILE = "NOTICE.md"
_SAMPLE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
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
    if args.command == "record":
        samples = [
            Sample(name, path, _read(parser, path))
            for name, path in _distinct(parser, args.samples)
        ]
        _check_settings(parser, environ)
        if why := _not_live(environ):
            return _cannot_record(why)
        live = provider_from_env({**environ, "OFFER_CHECKPOST_PROVIDER": "live"})
        return record(
            samples,
            live,
            environ=environ,
            max_total=args.max_total,
            recordings_dir=RECORDINGS_DIR,
        )
    text = _read(parser, args.file)
    return investigate(text, _store(parser, args, environ), args.actor, source=args.file)


def _read(parser: argparse.ArgumentParser, path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        parser.error(f"cannot read {path}: {e.strerror}")


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
    _check_settings(parser, environ)
    provider = PROVIDERS[args.provider]() if args.provider else None
    try:
        return Store.from_env(environ, provider)
    except SearchError as failure:
        parser.error(failure.message)
    except ValueError as failure:
        parser.error(str(failure))


def _check_settings(parser: argparse.ArgumentParser, environ: Mapping[str, str]) -> None:
    for name in ("OFFER_CHECKPOST_MAX_SEARCHES", "OFFER_CHECKPOST_QUOTA_RESERVE"):
        raw = environ.get(name, "").strip()
        if raw and not raw.isdigit():
            parser.error(f"{name} must be a whole number, not {raw!r}")


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
    recorder = commands.add_parser(
        "record",
        help="run sample offers on live SerpApi once and record the responses for replay",
        description="Spends live SerpApi searches with SERPAPI_KEY. Runs each sample offer as a "
        "person runs it in the app (samples a and c also get the remaining checks) and writes "
        "every response, scrubbed and dated, to recordings/<sample>/ for replay mode. Reads the "
        "free Account API first and refuses to start when the worst case would take this month's "
        "usage past --max-total, leave fewer searches than the quota reserve, or pass the hourly "
        "limit. Then replays every sample from the recordings alone to show it gives the same "
        "trace and band.",
    )
    recorder.add_argument(
        "samples",
        nargs="+",
        type=_sample_arg,
        metavar="SAMPLE=FILE",
        help="a sample's name (its folder under recordings/: lowercase letters, digits, - and _) "
        "and the text file holding its offer message, e.g. a=samples/offers/a.txt",
    )
    recorder.add_argument(
        "--max-total",
        type=_positive,
        default=DEFAULT_MAX_TOTAL,
        metavar="N",
        help="refuse to start when this month's usage plus the worst case would pass N "
        f"(default: {DEFAULT_MAX_TOTAL})",
    )
    return parser


def _sample_arg(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep or not path:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not SAMPLE=FILE, e.g. a=samples/offers/a.txt"
        )
    if not _SAMPLE_NAME.fullmatch(name):
        raise argparse.ArgumentTypeError(
            f"{name!r} is not a sample name: lowercase letters, digits, - and _, at most 40"
        )
    return name, Path(path)


def _positive(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number of 1 or more")
    return int(value)


def _distinct(
    parser: argparse.ArgumentParser, samples: Sequence[tuple[str, Path]]
) -> Sequence[tuple[str, Path]]:
    names = [name for name, _ in samples]
    if repeated := sorted({name for name in names if names.count(name) > 1}):
        parser.error(f"each sample is named once: {', '.join(repeated)} is named twice")
    return samples


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


# ---- record ----------------------------------------------------------------------------------

NOTICE = """\
# Recorded search content

Each JSON file in this folder, `<sample>/<engine>-<hash>.json`, is a response SerpApi returned,
with a real SerpApi key, to a Google search (`google`, `google_jobs`, `google_maps`,
`google_news` or `google_maps_reviews`) made with exactly the params the app sends, and written
by `python -m offer_checkpost record`. A response SerpApi had returned for the same search in
the previous 24 hours comes from the local cache, dated when SerpApi returned it.

- **Third-party material.** The search content is Google's results as SerpApi returned them.
  It is not covered by this repository's MIT licence, which covers the project's own code and
  documentation only.
- **Trimmed and scrubbed.** Each response keeps only the fields the app reads. Results that
  tend to name people are dropped: LinkedIn profiles; everything on Facebook, Instagram,
  X/Twitter and Truecaller, brand pages included; everything on a video site (YouTube, Vimeo,
  Dailymotion), whose descriptions name the speakers; and every news headline without both a
  fraud term and a job word, which the app never reads. No reviewer's name or profile is kept,
  phone numbers and email addresses are masked, and anything key-shaped is removed.
- **Dated.** `recordedAt` is when SerpApi returned the response, in IST. Search results change:
  a recording shows what a search found on that date, not today.
- **Why it is here.** Only so the app can replay the demo without a SerpApi key (replay mode,
  which says it is not live), and for the tests. Replay never makes a response up: a search
  that was not recorded fails.
"""


@dataclass(frozen=True)
class Sample:
    name: str
    source: Path
    text: str


@dataclass(frozen=True)
class Written:
    sample: str
    path: Path
    engine: str
    cache: CacheState
    recorded_at: str
    spent: int


class RecordingProvider(SearchProvider):
    """The live provider, with every response it serves also written, scrubbed and dated, to
    ``recordings_dir/<sample>/`` for the sample being played (``write_recording``), which is
    where replay looks it up by the same key-less params. A response the local cache serves is
    written too: SerpApi returned it for the same key within 24 hours, and it is dated when it
    did. Every response costs the case's budget 1 search, a cache hit included, as it costs
    replay: charged 0, a cache hit would let the recording run go further than replay can, and
    record a response replay never reaches. What SerpApi counted is ``Written.spent``."""

    def __init__(self, live: SearchProvider, recordings_dir: Path):
        if live.name != "live":
            raise ValueError(
                "only live SerpApi responses are recorded, never fake or replayed ones"
            )
        self.inner = live
        self.name = live.name
        self.recordings_dir = recordings_dir
        self.sample = ""
        self.written: list[Written] = []

    def search(self, params: Mapping[str, Any]) -> SearchResult:
        result = self.inner.search(params)
        recorded_at = (
            datetime.fromisoformat(result.retrieved_at)
            .astimezone(IST)
            .isoformat(timespec="seconds")
        )
        # well_formed, as the store serves it: a lone surrogate can't be written as UTF-8.
        path = write_recording(
            self.sample,
            result.params,
            well_formed(result.data),
            recorded_at=recorded_at,
            recordings_dir=self.recordings_dir,
        )
        self.written.append(
            Written(
                self.sample, path, result.engine, result.cache, recorded_at, result.searches_spent
            )
        )
        return replace(result, searches_spent=1)

    def account(self) -> dict[str, int] | None:
        return self.inner.account()


@dataclass
class Played:
    """What one sample's calls did: its case, and each planner verb's outcome in order."""

    case_id: str | None = None
    outcomes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    error: str | None = None


class _Halt(Exception):
    pass


def play(store: Store, text: str, *, remaining: bool) -> Played:
    """One offer through ``invoke`` as a person runs it in the app, every call the person's:
    open_case, update_claims (confirm), investigate and draft_verdict; with ``remaining`` and a
    decisive stop, run_remaining_checks and draft_verdict again; then draft_recruiter_reply and
    draft_cybercrime_report. The planner's own searches are the agent's, as in the app. Stops at
    the first call refused or search failed, and says which in ``error``."""
    played = Played()

    def call(tool: str, args: dict[str, Any]) -> Any:
        out = invoke(tool, args, "human", store=store)
        if not out["ok"]:
            raise _Halt(f"{tool} {out['outcome']}: {out['error']}")
        return out["result"]

    def planned(verb: str, case: dict[str, str]) -> str:
        outcome = call(verb, case)
        played.outcomes.append((verb, outcome))
        if outcome["budget"]["stoppedBecause"] == "search_error":
            raise _Halt(f"{verb} stopped: {outcome['trace'][-1]['because']}")
        return outcome["budget"]["stoppedBecause"]

    try:
        played.case_id = call("open_case", {"text": text})["id"]
        case = {"case_id": played.case_id}
        call("update_claims", {**case, "confirm": True})
        stopped = planned("investigate", case)
        call("draft_verdict", case)
        if remaining and stopped == "decisive":
            planned("run_remaining_checks", case)
            call("draft_verdict", case)
        call("draft_recruiter_reply", case)
        call("draft_cybercrime_report", case)
    except _Halt as halt:
        played.error = str(halt)
    return played


def record(
    samples: Sequence[Sample],
    live: SearchProvider,
    *,
    environ: Mapping[str, str],
    max_total: int = DEFAULT_MAX_TOTAL,
    recordings_dir: Path = RECORDINGS_DIR,
) -> int:
    """Records ``samples`` from ``live`` into ``recordings_dir``, as the module's docstring
    says. Returns 0 when every sample was recorded and replays to the same trace and band; 1
    when it refused to start, a call was refused, a search failed, or a replay differs."""
    recorder = RecordingProvider(live, recordings_dir)
    store = Store.from_env(environ, recorder)
    names = [sample.name for sample in samples]
    print(
        f"Offer Checkpost · record {', '.join(names)} · provider live · "
        f"into {_shown(recordings_dir, recordings_dir)}/"
    )
    try:
        before = store.provider.account()
    except SearchError as failure:
        return _cannot_record(f"the Account API failed: {failure.message}")
    print(f"Account API: {_counts(before)}")
    budget = store.max_searches
    extra = [name for name in names if name in REMAINING_CHECKS_SAMPLES]
    worst = budget * (len(samples) + len(extra))
    parts = f"{budget} for each of {len(samples)} sample{'s' if len(samples) > 1 else ''}"
    if extra:
        parts += f", and {budget} more for the remaining checks of each of {', '.join(extra)}"
    print(f"Worst case: {_searches(worst)} ({parts})")
    if why := _over_budget(before, worst, max_total, store.quota_reserve):
        return _cannot_record(why)

    notice = recordings_dir / NOTICE_FILE
    if not notice.exists():
        notice.parent.mkdir(parents=True, exist_ok=True)
        notice.write_text(NOTICE, encoding="utf-8")
        print(f"Wrote {_shown(notice, recordings_dir)}")

    recorded: list[tuple[Sample, Played]] = []
    failed = None
    for sample in samples:
        recorder.sample = sample.name
        first = len(recorder.written)
        played = play(store, sample.text, remaining=sample.name in REMAINING_CHECKS_SAMPLES)
        _print_played(sample, played, recorder.written[first:], recordings_dir)
        if played.error:
            failed = f"{sample.name}: {played.error}"
            break
        recorded.append((sample, played))

    spent = sum(w.spent for w in recorder.written)
    hits = sum(w.cache == "hit" for w in recorder.written)
    print(
        f"\nSpent: {_searches(spent)}"
        + (f", and {hits} more served from the local cache at no cost" if hits else "")
    )
    try:
        after = store.provider.account()
        print(f"Account API: {_counts(after)}")
    except SearchError as failure:
        print(f"Account API: failed: {failure.message}")
    if failed:
        print(f"offer_checkpost: recording stopped at {failed}", file=sys.stderr)
        return 1
    return _replay_check(recorded, store, recordings_dir)


def _not_live(environ: Mapping[str, str]) -> str | None:
    named = environ.get("OFFER_CHECKPOST_PROVIDER", "").strip()
    if named and named != "live":
        return (
            f"record searches SerpApi live, and OFFER_CHECKPOST_PROVIDER is {named!r}: unset it, "
            "or set it to live"
        )
    if not environ.get("SERPAPI_KEY", "").strip():
        return "record searches SerpApi live and needs SERPAPI_KEY, in the environment or .env"
    return None


def _cannot_record(why: str) -> int:
    print(f"offer_checkpost: record refused: {why}; nothing was searched", file=sys.stderr)
    return 1


def _over_budget(
    counts: Mapping[str, int], worst: int, max_total: int, reserve: int
) -> str | None:
    usage, left = counts["this_month_usage"], counts["plan_searches_left"]
    hour, per_hour = counts["this_hour_searches"], counts["account_rate_limit_per_hour"]
    if usage + worst > max_total:
        return (
            f"this month's usage is {usage}, and the worst case of {worst} more would bring it to "
            f"{usage + worst}, past --max-total {max_total}"
        )
    if left - worst < reserve:
        return (
            f"{left} searches are left this month, and the worst case of {worst} would leave "
            f"{left - worst}, under the quota reserve of {reserve}"
        )
    if hour + worst > per_hour:
        return (
            f"{hour} searches were made this hour, and the worst case of {worst} more would pass "
            f"the limit of {per_hour} an hour"
        )
    return None


def _print_played(
    sample: Sample, played: Played, written: Sequence[Written], recordings_dir: Path
) -> None:
    print(f"\n== {sample.name} · {sample.source} · {played.case_id or 'no case opened'}")
    if played.outcomes:
        print("Trace")
        print("\n".join(trace_lines(played.outcomes[-1][1])))
        print()
        for verb, outcome in played.outcomes:
            print(f"{verb}: {budget_line(outcome)}")
    if played.error:
        print(f"Stopped: {played.error}")
    print("Recorded" if written else "Recorded: nothing, since no search ran")
    for w in written:
        print(
            f"  {_shown(w.path, recordings_dir)} · {w.engine} · cache {w.cache} · {w.recorded_at}"
        )


def _replay_check(
    recorded: Sequence[tuple[Sample, Played]], store: Store, recordings_dir: Path
) -> int:
    """Plays each recorded sample again on a fresh store that replays ``recordings_dir`` alone,
    and compares what it did with the recording run. Returns 1 when any differs."""
    replay = Store(
        ReplaySearchProvider(recordings_dir),
        max_searches=store.max_searches,
        quota_reserve=store.quota_reserve,
    )
    print(f"\nReplay from {_shown(recordings_dir, recordings_dir)}/ alone (no key, no network)")
    code = 0
    for sample, played in recorded:
        again = play(replay, sample.text, remaining=sample.name in REMAINING_CHECKS_SAMPLES)
        problem = again.error or _difference(played.outcomes, again.outcomes)
        if problem:
            print(f"  {sample.name}: DIFFERS: {problem}")
            code = 1
        else:
            print(f"  {sample.name}: the same trace and band ({again.outcomes[-1][1]['band']})")
    return code


def _difference(
    recorded: Sequence[tuple[str, dict[str, Any]]], replayed: Sequence[tuple[str, dict[str, Any]]]
) -> str | None:
    """Where a replay first parts from the recording run, or None. Each verb's band and stop,
    and each trace line's heading, finding, signals and band are compared; the provider, cache
    and timing are not. The cost is the same on both sides: the recording run charges its
    budget 1 search for a cache hit too, as replay does (``RecordingProvider``)."""
    ran = [(verb, o["band"], o["budget"]["stoppedBecause"]) for verb, o in recorded]
    again = [(verb, o["band"], o["budget"]["stoppedBecause"]) for verb, o in replayed]
    if ran != again:
        return f"recorded {_verbs(ran)}; replayed {_verbs(again)}"
    lines = zip_longest(_comparable(recorded[-1][1]), _comparable(replayed[-1][1]))
    for n, (a, b) in enumerate(lines, 1):
        if a == b:
            continue
        if a is not None and b is not None and a[:-1] == b[:-1]:
            return (
                f"trace line {n}, {a[1]}: what it found differs: recorded "
                f"{json.dumps(a[-1], ensure_ascii=False)}; replayed "
                f"{json.dumps(b[-1], ensure_ascii=False)}"
            )
        return f"trace line {n}: recorded {_brief(a)}; replayed {_brief(b)}"
    return None


def _comparable(outcome: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """Each trace line's heading, signals, band, finding and facts, the text masked as a
    recording's is: a phone number or email the live response showed and the recording masks
    is no difference."""
    rules = {s["id"]: s["rule"] for s in outcome["signals"]}
    return [
        (
            line["step"],
            line["tool"],
            line["action"],
            line["engine"],
            tuple(rules[sid] for sid in line["signalsAdded"]),
            line["band"],
            _masked(line["note"]),
            _masked(line["facts"]),
        )
        for line in outcome["trace"]
    ]


def _masked(value: Any) -> Any:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list | tuple):
        return [_masked(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _masked(v) for k, v in value.items()}
    return value


def _brief(line: tuple[Any, ...] | None) -> str:
    if line is None:
        return "no line"
    _, tool, action, _, signals, band, note, _ = line
    found = f": {note}" if note else ""
    fired = f", fired {', '.join(signals)}" if signals else ""
    return f"{tool} {action}{found}{fired}, band {band}"


def _verbs(outcomes: Sequence[tuple[str, str, str]]) -> str:
    return ", ".join(f"{verb} (band {band}, stopped: {stop})" for verb, band, stop in outcomes)


def _counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{name} {value}" for name, value in counts.items())


def _shown(path: Path, recordings_dir: Path) -> str:
    return path.relative_to(recordings_dir.parent).as_posix()


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
