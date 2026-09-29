"""Everything the app knows, in memory: cases, the Offer Board, the activity log and the call
log. It resets when the process stops; nothing is written to disk.

Only ``invoke`` changes this state, through a tool's or a verb's handler, and it does so under
``Store.lock`` so two clicks can't interleave. The lock is re-entrant because an
investigation's own checks go through ``invoke`` again.

``Store.provider`` is the search provider as the rest of the app sees it: every search it
serves lands in the call log, its Account API counts come back reduced to the five the app
shows, and what it serves is made fit for JSON first (``well_formed``): a search response is
outside input, and one lone surrogate or infinite number kept from it would break every later
answer that carried the state. A handler that can't do what it was asked raises ``Refused``
before it changes anything; ``invoke`` logs the refusal and returns its message.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import math
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from offer_checkpost import planner
from offer_checkpost.checks import search_name
from offer_checkpost.providers import (
    ReplaySearchProvider,
    SearchProvider,
    SearchResult,
    provider_from_env,
)
from offer_checkpost.rules import Signal
from offer_checkpost.scrub import clean_text, scrub_account

IST = timezone(timedelta(hours=5, minutes=30), "IST")

ACTORS = ("human", "agent")
STATUSES = ("open", "investigated", "published", "retracted")
OUTCOMES = ("walked_away", "proceeding", "reported_1930")

# A full sha256 digest is 64 hex characters, the shape of a SerpApi key, which the secret
# scans look for and a viewer of the page or the demo video could take for one. The first 16
# are plenty to tell a few thousand pasted messages apart.
FINGERPRINT_HEX = 16

# Half of a UTF-16 surrogate pair on its own, such as an emoji cut in half when a message was
# copied: not a Unicode character, so no UTF-8 text, and no answer, can carry it.
LONE_SURROGATE = re.compile("[\ud800-\udfff]")


class Refused(Exception):
    """A call the app won't carry out. ``message`` says why in words a person can act on."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class CallLogged(SearchProvider):
    def __init__(self, inner: SearchProvider, log: Callable[[SearchResult], None]):
        self.inner = inner
        self.name = inner.name
        self._log = log

    def search(self, params: Mapping[str, Any]) -> SearchResult:
        result = self.inner.search(params)
        self._log(result)
        return dataclasses.replace(result, data=well_formed(result.data))

    def account(self) -> dict[str, int] | None:
        counts = self.inner.account()
        return None if counts is None else well_formed(scrub_account(counts))


class Store:
    def __init__(
        self,
        provider: SearchProvider,
        *,
        max_searches: int = planner.DEFAULT_MAX_SEARCHES,
        quota_reserve: int = planner.DEFAULT_QUOTA_RESERVE,
        key_set: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(IST),
    ):
        self.provider = CallLogged(provider, self._log_call)
        self.max_searches = max_searches
        self.quota_reserve = quota_reserve
        self.clock = clock
        self.cases: dict[str, dict[str, Any]] = {}
        self.board: list[dict[str, Any]] = []
        self.activity_log: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.server: dict[str, Any] = {
            "bind": None,
            "provider": provider.name,
            "key": "set" if key_set else "missing",
        }
        if isinstance(provider, ReplaySearchProvider):
            # The page says when the replayed responses were recorded: they are not today's.
            self.server["recorded"] = list(provider.recorded_dates)
        self.lock = threading.RLock()

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] = os.environ, provider: SearchProvider | None = None
    ) -> Store:
        """A store set up as the environment says; ``provider`` replaces the one it names."""
        return cls(
            provider or provider_from_env(environ),
            max_searches=planner.configured_max_searches(environ),
            quota_reserve=planner.configured_quota_reserve(environ),
            key_set=bool(environ.get("SERPAPI_KEY", "").strip()),
        )

    @property
    def planner_settings(self) -> dict[str, str]:
        """This store's budget and quota reserve, in the form the planner reads settings."""
        return {
            "OFFER_CHECKPOST_MAX_SEARCHES": str(self.max_searches),
            "OFFER_CHECKPOST_QUOTA_RESERVE": str(self.quota_reserve),
        }

    def now(self) -> str:
        return self.clock().astimezone(IST).isoformat(timespec="seconds")

    # ---- cases ---------------------------------------------------------------------------

    def case(self, case_id: str) -> dict[str, Any]:
        if case_id not in self.cases:
            raise Refused(f"there is no case {case_id}; list_cases shows the cases there are")
        return self.cases[case_id]

    def add_case(
        self, source_text: str, claims: dict[str, Any], signals: Iterable[Signal]
    ) -> dict[str, Any]:
        case_id = f"case_{len(self.cases) + 1:03d}"
        case: dict[str, Any] = {
            "id": case_id,
            "createdAt": self.now(),
            "sourceText": source_text,
            "fingerprint": None,
            "sameAs": None,
            "claims": claims,
            # Counts every change to the claims and the findings, so a draft, and a person's
            # publish click, can say which version of the case they were made from.
            "revision": 0,
            "signals": [],
            "trace": [],
            "budget": {
                "maxSearches": self.max_searches,
                "spent": 0,
                "saved": 0,
                "stoppedBecause": None,
            },
            "draftVerdict": None,
            "draftReply": None,
            "draftReport": None,
            "publishedVerdict": None,
            "outcome": None,
            "status": "open",
        }
        add_signals(case, signals)
        self.cases[case_id] = case
        self.refresh_fingerprint(case)
        return case

    def refresh_fingerprint(self, case: dict[str, Any]) -> None:
        """Sets ``fingerprint`` from the case's claims, and ``sameAs`` to ``earlier_copy``. A
        case that names no company has nothing to search, so it gets no fingerprint."""
        case["fingerprint"] = fingerprint(case["claims"])
        earlier = self.earlier_copy(case)
        case["sameAs"] = earlier["id"] if earlier else None

    def earlier_copy(self, case: dict[str, Any]) -> dict[str, Any] | None:
        """The first case opened before this one, with the same fingerprint, whose
        investigation finished: the same bulk-sent message, forwarded again. A copy whose
        investigation stopped short (the quota guard, a failed search) is passed over, so a
        later copy that finished is found instead."""
        for other in self.cases.values():
            if other is case:
                return None
            if planner.reusable(case, other):
                return other
        return None

    # ---- logs and snapshot ---------------------------------------------------------------

    def _log_call(self, result: SearchResult) -> None:
        self.calls.append(
            {
                "ts": self.now(),
                "provider": result.provider,
                "engine": result.engine,
                "cache": result.cache,
                "ms": result.ms,
            }
        )

    def log(
        self,
        actor: Any,
        tool: Any,
        args: Any,
        result: str,
        reason: str | None = None,
        *,
        planner: bool = False,
    ) -> dict[str, Any]:
        """Appends an activity-log entry and returns it, so a call logged as it starts can be
        ``settle``d when it ends and still sit before the calls it made. A check the planner
        made inside another call is marked ``planner``: it was asked for by whoever made that
        call, so the page tells the agent's own calls from the checks a person's click ran."""
        entry = {
            "ts": self.now(),
            "actor": redact(actor),
            "tool": redact(tool),
            "args": redact(args),
        }
        if planner:
            entry["planner"] = True
        self.settle(entry, result, reason)
        self.activity_log.append(entry)
        return entry

    def settle(self, entry: dict[str, Any], result: str, reason: str | None = None) -> None:
        entry["result"] = result
        if reason is not None:
            # A refusal can quote the caller's own input back, which may hold anything.
            entry["reason"] = clean_text(reason)

    def state(self) -> dict[str, Any]:
        """A deep copy of everything, in the shape ``GET /api/state`` serves."""
        with self.lock:
            return copy.deepcopy(
                {
                    "cases": list(self.cases.values()),
                    "board": self.board,
                    "activityLog": self.activity_log,
                    "calls": self.calls,
                    "server": self.server,
                }
            )


def add_signals(case: dict[str, Any], signals: Iterable[Signal]) -> list[Signal]:
    """Numbers ``signals`` after the case's last one (``sig_1``, ``sig_2``, …), appends them
    and returns them as stored."""
    start = len(case["signals"])
    added = [{**s, "id": f"sig_{start + i}"} for i, s in enumerate(signals, 1)]
    case["signals"].extend(added)
    return added


def fingerprint(claims: Mapping[str, Any]) -> str | None:
    """The sha256 of the normalised company, role, city, monthly pay, fee and contacts, so the
    same bulk-sent message forwarded with other spacing, case or a legal suffix gets the same
    fingerprint. Pay and fee are in it because checks read them (the pay benchmark, the fee
    rules): an offer at another pay or fee is another message. None when no company is
    claimed: such a case has nothing to search or reuse."""
    if claims["company"] is None:
        return None
    parts = (
        _norm(search_name(claims["company"]["value"])),
        _norm(_value(claims, "role")),
        _norm(_value(claims, "city")),
        _amount(claims, "pay", "monthlyInr"),
        _amount(claims, "fee", "amountInr"),
        ",".join(sorted(_contact_key(c) for c in claims["contacts"])),
    )
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return "sha256:" + digest[:FINGERPRINT_HEX]


def _value(claims: Mapping[str, Any], name: str) -> str:
    claim = claims[name]
    return claim["value"] if claim else ""


def _amount(claims: Mapping[str, Any], name: str, key: str) -> str:
    claim = claims[name]
    if claim is None:
        return ""
    # A fee the message asks for without naming an amount still differs from no fee.
    return "asked" if claim[key] is None else str(claim[key])


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[\w@.+-]+", text.casefold()))


def _contact_key(contact: Mapping[str, Any]) -> str:
    value = contact["value"].casefold()
    return re.sub(r"[^0-9x]", "", value) if contact["kind"] == "phone" else value


_SUMMARISE_OVER = 200


def well_formed(value: Any) -> Any:
    """``value`` with each lone surrogate in its text replaced by U+FFFD and each infinite or
    NaN number by None: what JSON can carry to the page."""
    if isinstance(value, str):
        return LONE_SURROGATE.sub("\ufffd", value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {well_formed(k): well_formed(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [well_formed(v) for v in value]
    return value


def redact(value: Any) -> Any:
    """``value`` as the activity log may keep it: emails and phone numbers masked, key-shaped
    strings removed, and a long text such as a pasted message reduced to its length."""
    if isinstance(value, str):
        return f"[{len(value)} characters]" if len(value) > _SUMMARISE_OVER else clean_text(value)
    if isinstance(value, Mapping):
        return {redact(str(k)): redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value
