"""The investigation planner: which check runs next, why it runs, and when to stop.

Text rules cost no search, so step 0 always reads them. Step 1 is ``lookup_official_site``,
and only when a company is claimed; with none, the planner stops, since nothing is checkable on
the web. After step 1 the checks run in this base order, each only while its condition holds:

    a  find_fraud_notice        an official domain is known, and a fee was asked, sensitive
                                documents were asked for, or the recruiter's email or link is
                                off the official domain; a search that returns no page from
                                that domain is inconclusive, and runs once more (R3)
    b  confirm_sender_domain    sender_lookalike fired; one search per look-alike, at most 2
    c  check_job_listings       a role is claimed
    d  check_office             a city is claimed
    e  scan_office_reviews      check_office found a place_id, and fee_requested or
                                no_listing_match fired; term "fee" when a fee was asked, else
                                "fraud"
    f  check_scam_reports       a company is claimed
    g  check_contact_footprint  a recruiter phone or email that isn't a synthetic placeholder

Two reorder rules read step 1's result. R1, unknown firm: no_web_footprint fired or no official
domain was found, so check_office runs before check_job_listings. R2, official sender:
sender_official fired and no fee was asked, so find_fraud_notice is skipped and
check_job_listings runs first.

R3, inconclusive notice search: Google sometimes drops ``site:`` for a narrow query
and answers with pages of other sites, so a first ``find_fraud_notice`` that returned no page from
the official domain is not "no notice". While its own conditions still hold and the budget lasts,
the planner runs it once more with ``wording: "broad"`` (the notice's usual titles). One retry, no
more: a second search with no page from the domain stays inconclusive, with no signal and the band
unchanged; a first search that did return pages from the domain but no notice is a finding and is
not retried. The retry is a numbered search like any other and counts against the budget.

Before every search the planner stops at the first of: the band is already ``high_risk`` and no
search left can change it (decisive, judged from step 1 on); the case's search budget is
spent; the Account API's ``plan_searches_left`` is below the reserve (the quota guard, with no
fallback to other data). Only a person can spend searches past a decisive stop, through
``run_remaining_checks``. A check tool called on its own (``check_directly``) keeps the same
rules: it refuses once the budget is spent or the quota guard trips, and refuses the agent
once the evidence is decisive.

Investigating a case again starts afresh: every search finding already on the case is
superseded, and step 0 and step 1 run again, so the band is only ever decided from one
investigation's findings. A check run again supersedes what its earlier run found, so a
finding is never counted or cited twice.

A case whose message was checked before (``sameAs``: same ``fingerprint``, and that case's
investigation finished) reuses that case's findings, with the trace lines that found them, at
0 searches. The fingerprint itself is set when the case is opened.

Every run, skip, reorder and stop is one line of ``case["trace"]``, and every line has the
same keys:

    step           the search's number (0 for the text rules); None on a line that ran none
    tool           the check, "text_rules", or the verb that stopped ("investigate",
                   "run_remaining_checks")
    args           the check's tool args (case_id and any domain, term, contact_index or
                   wording), or None
    actor          "agent" for the planner's own calls, "human" for checks a person asked for
    action         "ran" | "failed" | "skipped" | "reordered" | "stopped" | "reused"
    because        why, in one line a person can read
    engine, params, provider, cache, ms
                   the search: its engine, its key-less params, which provider served it,
                   "hit" / "miss" / "replay", and how long it took
    searchesSpent  what the line cost the budget (a local cache hit costs 0)
    searchesSaved  on a decisive stop, the budget left unspent; on a reuse, what the earlier
                   case spent
    signalsAdded   the ids of the signals the line fired
    band           the band after the line
    note           the reader's one-line finding, or the error of a failed search
    facts          what the reader found that later checks need (the official domain, a
                   place_id, the pay benchmark)
    reusedFrom     the case a reused line or signal was copied from

``case["budget"]`` is ``{"maxSearches", "spent", "saved", "stoppedBecause"}``, where
``stoppedBecause`` is one of ``STOP_REASONS``.
"""

from __future__ import annotations

import contextlib
import copy
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

from offer_checkpost import checks
from offer_checkpost.domains import registrable_domain
from offer_checkpost.extract import text_rules
from offer_checkpost.providers import SearchError, SearchResult, keyless
from offer_checkpost.rules import TEXT_SOURCE, Band, decide, is_decisive, text_signal

DEFAULT_MAX_SEARCHES = 6
DEFAULT_QUOTA_RESERVE = 20
MAX_CONFIRMATIONS = 2
REVIEW_TERMS = ("fee", "fraud")

BASE_ORDER = (
    "find_fraud_notice",
    "confirm_sender_domain",
    "check_job_listings",
    "check_office",
    "scan_office_reviews",
    "check_scam_reports",
    "check_contact_footprint",
)
CHECK_TOOLS = ("lookup_official_site", *BASE_ORDER)
ENGINES = {
    "lookup_official_site": "google",
    "find_fraud_notice": "google",
    "confirm_sender_domain": "google",
    "check_job_listings": "google_jobs",
    "check_office": "google_maps",
    "scan_office_reviews": "google_maps_reviews",
    "check_scam_reports": "google_news",
    "check_contact_footprint": "google",
}
STOP_REASONS = ("decisive", "budget", "quota", "done", "no_company", "search_error", "same_as")
TRACE_KEYS = (
    "step",
    "tool",
    "args",
    "actor",
    "action",
    "because",
    "engine",
    "params",
    "provider",
    "cache",
    "ms",
    "searchesSpent",
    "searchesSaved",
    "signalsAdded",
    "band",
    "note",
    "facts",
    "reusedFrom",
)

ON_REQUEST = "run on request after a decisive result"
NO_COMPANY = "no company named: nothing to check on the web"
UNCONFIRMED = (
    "the claims are not confirmed yet: confirm or correct them (update_claims) before anything "
    "is searched"
)
R1 = "R1, unknown firm"
R2 = "R2, official sender"
R3_RETRY = (
    "R3, inconclusive notice search: the first search returned no page from {domain}, so try "
    "the notice's usual titles"
)
R2_SKIP = (
    f"{R2}: the sender is on the official domain and asks for no fee, so there is nothing for "
    "a fraud notice to contradict"
)
# Only a failure to finish is worth searching again; any other stop is a finding.
_NOT_REUSABLE = frozenset({"quota", "search_error"})
_CONFIRMABLE = ("company", "role", "city", "pay", "fee")

Case = dict[str, Any]
Line = dict[str, Any]
# runner(tool, args) runs one check tool on the very case dict the planner holds, which
# appends one trace line, a failed search included (the runner then returns normally); the
# planner then writes its "because" onto that line. The default calls run_check. The one path
# in passes a call through itself, so every search the planner makes is a tool call like any
# other.
Runner = Callable[[str, dict[str, Any]], object]


class Searcher(Protocol):
    """A search provider, or anything that wraps one (a store that logs each call)."""

    def search(self, params: Mapping[str, Any]) -> SearchResult: ...

    def account(self) -> dict[str, int] | None: ...


class PlanRefused(Exception):
    """A check or a planner verb that can't run on this case as it stands. ``reason`` says
    why, for a reader who can't see the screen."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---- settings and confirmation ------------------------------------------------------------


def configured_max_searches(environ: Mapping[str, str] = os.environ) -> int:
    return int(environ.get("OFFER_CHECKPOST_MAX_SEARCHES") or DEFAULT_MAX_SEARCHES)


def configured_quota_reserve(environ: Mapping[str, str] = os.environ) -> int:
    return int(environ.get("OFFER_CHECKPOST_QUOTA_RESERVE") or DEFAULT_QUOTA_RESERVE)


def claims_confirmed(claims: Mapping[str, Any]) -> bool:
    """True when every claim the message carries (company, role, city, pay, fee) is
    confirmed. Contacts and links are read from the text as written, so they need none."""
    return all(claims.get(k) is None or claims[k].get("confirmed") for k in _CONFIRMABLE)


# ---- one check -----------------------------------------------------------------------------


def run_check(
    case: Case,
    tool: str,
    args: Mapping[str, Any],
    *,
    provider: Searcher,
    actor: str,
    because: str | None = None,
) -> Line:
    """Runs one check tool on ``case``: its one search, its reader, the signals it fires
    numbered into ``case["signals"]``, its cost added to ``case["budget"]``, and one trace
    line, which it returns. The signals an earlier run of the same check (same tool, same
    args) fired are superseded by this run's. A search the provider can't serve is recorded as
    a ``failed`` line with the provider's fixed message, and its ``SearchError`` is raised
    again, so a caller hears of it; nothing is made up in its place, and the earlier run's
    findings stand. A run whose reading is inconclusive says as little as a failed one, so it
    supersedes nothing either. Raises ``PlanRefused`` when the check can't run yet
    (``find_fraud_notice`` before an official domain is known, ``scan_office_reviews`` without
    a place_id) or would search something the offer didn't supply (a domain not extracted
    from it, a synthetic contact). It applies no stop rule: the planner does that between
    searches, and ``check_directly`` for a check called on its own."""
    params, read = _PREPARE[tool](case, args)
    step = _next_step(case)
    because = because or f"called directly by the {actor}"
    try:
        result = provider.search(params)
    except SearchError as e:
        _line(
            case,
            step=step,
            tool=tool,
            args=dict(args),
            actor=actor,
            action="failed",
            because=because,
            engine=params["engine"],
            params=keyless(params),
            note=e.message,
        )
        raise
    reading = read(result.data, params=result.params, retrieved_at=result.retrieved_at)
    if not reading.facts.get("inconclusive"):
        _supersede(_found_by(case, tool, args), f"step {step} ran {tool} again")
    added = _add(case, reading.signals)
    budget = _budget(case)
    budget["spent"] += result.searches_spent
    if budget["stoppedBecause"] == "decisive":
        # A search a person spends past a decisive stop comes out of what the stop saved.
        budget["saved"] -= result.searches_spent
    return _line(
        case,
        step=step,
        tool=tool,
        args=dict(args),
        actor=actor,
        action="ran",
        because=because,
        engine=result.engine,
        params=result.params,
        provider=result.provider,
        cache=result.cache,
        ms=result.ms,
        spent=result.searches_spent,
        added=added,
        note=reading.note,
        facts=reading.facts,
    )


def check_directly(
    case: Case,
    tool: str,
    args: Mapping[str, Any],
    *,
    provider: Searcher,
    actor: str,
    reserve: int,
) -> Line:
    """A check tool called on its own, by a person or the agent, rather than by the planner:
    ``run_check`` under the planner's own rules. Before anything is searched it refuses
    (``PlanRefused``) when the claims are not confirmed, when the case's search budget is
    spent, when the evidence is already decisive and the caller is the agent (only a person
    spends searches that can't change the band), and when ``plan_searches_left`` is below
    ``reserve`` (the quota guard)."""
    if not claims_confirmed(case["claims"]):
        raise PlanRefused(UNCONFIRMED)
    budget = _budget(case)
    if budget["spent"] >= budget["maxSearches"]:
        raise PlanRefused(
            f"the case's search budget is spent: {budget['spent']} of {budget['maxSearches']}; "
            "investigating it again starts a fresh budget"
        )
    if actor == "agent" and is_decisive(case["signals"], open_domains=_open(case, None)):
        raise PlanRefused(
            "the evidence is already decisive: the band is high_risk and no search can change "
            "it. The agent never spends searches that can't change the band; a person can run "
            "the remaining checks"
        )
    account = provider.account()
    if account is not None and account["plan_searches_left"] < reserve:
        raise PlanRefused(
            f"quota guard: {_searches(account['plan_searches_left'])} left this month, below "
            f"the reserve of {reserve}; nothing was searched"
        )
    return run_check(case, tool, args, provider=provider, actor=actor)


def _prepare_lookup(case: Case, args: Mapping[str, Any]):
    claims = case["claims"]
    company = _need(claims, "company", "lookup_official_site")
    return checks.lookup_official_site_params(company, _value(claims, "city")), partial(
        checks.read_official_site,
        company=company,
        contacts=claims["contacts"],
        links=claims["links"],
    )


def _prepare_fraud_notice(case: Case, args: Mapping[str, Any]):
    official = _official_or_refuse(case, "find_fraud_notice")
    params = checks.find_fraud_notice_params(
        official, _value(case["claims"], "city"), args.get("wording", "specific")
    )
    return params, partial(
        checks.read_fraud_notice,
        official_domain=official,
        fee_requested=_fired(case, "fee_requested"),
    )


def _prepare_confirm(case: Case, args: Mapping[str, Any]):
    claims = case["claims"]
    official = _official_or_refuse(case, "confirm_sender_domain")
    domain = registrable_domain(args["domain"])
    if domain is None or domain not in _offer_domains(claims):
        raise PlanRefused(
            f"confirm_sender_domain checks only a recruiter domain extracted from the offer, "
            f"and {args['domain']} is not one"
        )
    return checks.confirm_sender_domain_params(official, domain, _value(claims, "city")), partial(
        checks.read_confirm_sender_domain,
        official_domain=official,
        candidate=domain,
        contacts=claims["contacts"],
    )


def _prepare_listings(case: Case, args: Mapping[str, Any]):
    claims = case["claims"]
    company = _need(claims, "company", "check_job_listings")
    role = _need(claims, "role", "check_job_listings")
    city = _value(claims, "city")
    official = _official(case)
    pay = claims.get("pay")
    params = checks.check_job_listings_params(role, city, company if official else None)
    return params, partial(
        checks.read_job_listings,
        company=company,
        role=role,
        city=city,
        official_domain=official,
        offered_monthly_inr=pay["monthlyInr"] if pay else None,
    )


def _prepare_office(case: Case, args: Mapping[str, Any]):
    claims = case["claims"]
    company = _need(claims, "company", "check_office")
    city = _need(claims, "city", "check_office")
    return checks.check_office_params(company, city), partial(
        checks.read_office, company=company, city=city
    )


def _prepare_reviews(case: Case, args: Mapping[str, Any]):
    term = args["term"]
    if term not in REVIEW_TERMS:
        raise PlanRefused(f"scan_office_reviews scans for 'fee' or 'fraud' only, not {term!r}")
    office = _latest(case, "check_office")
    place_id = office["facts"]["place_id"] if office else None
    if place_id is None:
        raise PlanRefused("scan_office_reviews needs a place_id that check_office found")
    return checks.scan_office_reviews_params(place_id, term), partial(
        checks.read_office_reviews, term=term
    )


def _prepare_reports(case: Case, args: Mapping[str, Any]):
    company = _need(case["claims"], "company", "check_scam_reports")
    return checks.check_scam_reports_params(company), partial(
        checks.read_scam_reports, company=company
    )


def _prepare_footprint(case: Case, args: Mapping[str, Any]):
    claims = case["claims"]
    index = args["contact_index"]
    if not 0 <= index < len(claims["contacts"]):
        raise PlanRefused(
            f"check_contact_footprint searches only a contact extracted from the offer; "
            f"there is no contact {index}"
        )
    contact = claims["contacts"][index]
    if contact["synthetic"]:
        raise PlanRefused(
            f"contact {index} is a synthetic placeholder, and a placeholder is never searched"
        )
    return checks.check_contact_footprint_params(contact, _value(claims, "city")), partial(
        checks.read_contact_footprint, contact=contact
    )


_PREPARE = {
    "lookup_official_site": _prepare_lookup,
    "find_fraud_notice": _prepare_fraud_notice,
    "confirm_sender_domain": _prepare_confirm,
    "check_job_listings": _prepare_listings,
    "check_office": _prepare_office,
    "scan_office_reviews": _prepare_reviews,
    "check_scam_reports": _prepare_reports,
    "check_contact_footprint": _prepare_footprint,
}


# ---- the planner ---------------------------------------------------------------------------


def investigate(
    case: Case,
    provider: Searcher,
    *,
    max_searches: int | None = None,
    same_as: Mapping[str, Any] | None = None,
    runner: Runner | None = None,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, Any]:
    """Runs the policy on a case with confirmed claims and returns its band, budget, trace and
    signals. ``max_searches`` can lower the configured budget (``OFFER_CHECKPOST_MAX_SEARCHES``)
    but never raise it. ``same_as`` is the earlier case ``case["sameAs"]`` names: when the two
    ``fingerprint`` fields still match and it finished, its findings are reused at 0 searches.
    Each investigation starts afresh, with a fresh budget: the search findings already on the
    case are superseded, and only this investigation's count. Never drafts, publishes, closes
    anything or changes the case's status."""
    if not claims_confirmed(case["claims"]):
        raise PlanRefused(UNCONFIRMED)
    cap = configured_max_searches(environ)
    limit = cap if max_searches is None else min(max_searches, cap)
    case["budget"] = {"maxSearches": limit, "spent": 0, "saved": 0, "stoppedBecause": None}
    _supersede(
        (s for s in case["signals"] if s["source"] != TEXT_SOURCE),
        "found before the latest investigation, which checked again",
    )
    _text_step(case)
    if same_as is not None and reusable(case, same_as):
        _reuse(case, same_as)
    else:
        run = _Run(case, provider, "agent", "investigate", runner, environ, decisive=True)
        _finish(run, _investigate(run))
    return _outcome(case)


def run_remaining_checks(
    case: Case,
    provider: Searcher,
    *,
    actor: str,
    runner: Runner | None = None,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, Any]:
    """The human verb: runs the checks a decisive stop skipped, in the planner's order and
    under the same conditions, within the case's budget and the quota guard. Each run is
    labelled "run on request after a decisive result". Refuses any actor but ``human``; a
    case whose last investigation did not stop at a decisive result; claims that are not
    confirmed; and a case where a correction made one of that investigation's signals stale,
    since its decisive result, and step 1's reading of the claims, no longer stand."""
    if actor != "human":
        raise PlanRefused(
            "run_remaining_checks is for a person only: the agent never spends searches past "
            "a decisive result"
        )
    if (case.get("budget") or {}).get("stoppedBecause") != "decisive":
        raise PlanRefused(
            "no checks are waiting: the last investigation did not stop at a decisive result"
        )
    if not claims_confirmed(case["claims"]):
        raise PlanRefused(UNCONFIRMED)
    found = {sid for line in _current(case) for sid in line["signalsAdded"]}
    if any(s["stale"] for s in case["signals"] if s["id"] in found):
        raise PlanRefused(
            "a claim was corrected since the decisive stop, so that result no longer stands: "
            "investigate the case again"
        )
    run = _Run(case, provider, actor, "run_remaining_checks", runner, environ, decisive=False)
    stop = _read_quota(run) or _walk(run, _context(case, _current_step1(case)))
    _finish(run, stop)
    return _outcome(case)


Stop = tuple[str, str, int]  # (stoppedBecause, because, searchesSaved)


class _Run:
    def __init__(
        self,
        case: Case,
        provider: Searcher,
        actor: str,
        verb: str,
        runner: Runner | None,
        environ: Mapping[str, str],
        *,
        decisive: bool,
    ):
        self.case = case
        self.provider = provider
        self.actor = actor
        self.verb = verb
        self.runner = runner or self._run_check
        self.reserve = configured_quota_reserve(environ)
        self.decisive = decisive
        self.left: int | None = None

    def _run_check(self, tool: str, args: dict[str, Any]) -> None:
        # The failed line is on the trace, and the plan reads it from there.
        with contextlib.suppress(SearchError):
            run_check(self.case, tool, args, provider=self.provider, actor=self.actor)

    def call(self, tool: str, extra: Mapping[str, Any], because: str) -> Line:
        before = len(self.case["trace"])
        self.runner(tool, {"case_id": self.case["id"], **extra})
        [line] = self.case["trace"][before:]
        line["because"] = because if self.decisive else f"{ON_REQUEST}: {because}"
        if self.left is not None:
            self.left -= line["searchesSpent"]
        return line


@dataclass(frozen=True)
class _Context:
    """What step 1 decided for the rest of the plan."""

    step: int
    official: str | None
    reorder: str | None  # "R1", "R2" or None
    lookalikes: tuple[str, ...]


def _investigate(run: _Run) -> Stop:
    case = run.case
    company = _value(case["claims"], "company")
    if company is None:
        return "no_company", NO_COMPANY, 0
    if stop := _read_quota(run) or _guard(run, None):
        return stop
    because = (
        f"a company is claimed ({checks.search_name(company)}), so step 1 looks up its "
        "official domain and classifies the recruiter's domains"
    )
    line = run.call("lookup_official_site", {}, because)
    if line["action"] == "failed":
        return _failed(line)
    ctx = _context(case, line)
    if ctx.reorder == "R1":
        found = (
            "no_web_footprint fired"
            if "no_web_footprint" in _rules(case, line)
            else f"step {ctx.step} found no official domain"
        )
        _reorder(
            run,
            "check_office",
            f"{R1}: {found}, so check_office moves ahead of check_job_listings: there's no "
            "domain to anchor a listing match, and an unknown firm's office is the cheapest "
            "claim to disprove",
        )
    elif ctx.reorder == "R2":
        _reorder(
            run,
            "check_job_listings",
            f"{R2}: the sender is on the official domain and asks for no fee, so "
            "check_job_listings runs first: a listing with an apply option on the official "
            "domain is the strongest green signal",
        )
    return _walk(run, ctx)


def _walk(run: _Run, ctx: _Context) -> Stop:
    case = run.case
    done = {_identity(line["tool"], line["args"]) for line in _current(case) if line["args"]}
    for tool, extra in _items(case, ctx):
        if _identity(tool, extra) in done:
            continue
        because, runs = _consider(case, ctx, tool, extra)
        if not runs:
            _line(
                case,
                tool=tool,
                args={"case_id": case["id"], **extra},
                actor=run.actor,
                action="skipped",
                because=because,
                engine=ENGINES[tool],
            )
            continue
        if stop := _guard(run, ctx):
            return stop
        line = run.call(tool, extra, because)
        if line["action"] == "failed":
            return _failed(line)
        if tool == "find_fraud_notice" and _inconclusive(line):
            if stop := _guard(run, ctx):
                return stop
            retry = run.call(tool, {"wording": "broad"}, R3_RETRY.format(domain=ctx.official))
            if retry["action"] == "failed":
                return _failed(retry)
    if run.decisive:
        return "done", "every check has run or been skipped", 0
    return "done", "every check the decisive stop skipped has run or been skipped", 0


def _items(case: Case, ctx: _Context) -> list[tuple[str, dict[str, Any]]]:
    order = list(BASE_ORDER)
    if ctx.reorder == "R1":
        order.remove("check_office")
        order.insert(order.index("check_job_listings"), "check_office")
    elif ctx.reorder == "R2":
        order.remove("check_job_listings")
        order.insert(order.index("find_fraud_notice") + 1, "check_job_listings")
    contacts = case["claims"]["contacts"]
    items: list[tuple[str, dict[str, Any]]] = []
    for tool in order:
        if tool == "confirm_sender_domain":
            items += [(tool, {"domain": d}) for d in ctx.lookalikes] or [(tool, {})]
        elif tool == "check_contact_footprint":
            items += [(tool, {"contact_index": i}) for i in range(len(contacts))] or [(tool, {})]
        elif tool == "scan_office_reviews":
            items.append((tool, {"term": "fee" if _fee_asked(case) else "fraud"}))
        else:
            items.append((tool, {}))
    return items


def _consider(case: Case, ctx: _Context, tool: str, extra: Mapping[str, Any]) -> tuple[str, bool]:
    """(because, runs) for one planned check, from what the case holds now."""
    claims = case["claims"]
    company = checks.search_name(_value(claims, "company"))
    role, city = _value(claims, "role"), _value(claims, "city")

    if tool == "find_fraud_notice":
        if ctx.official is None:
            return (
                f"step {ctx.step} found no official domain, so there is no employer site to "
                "search for a fraud notice",
                False,
            )
        if ctx.reorder == "R2":
            return R2_SKIP, False
        reasons = []
        if _fee_asked(case):
            reasons.append("a fee was asked")
        if _fired(case, "sensitive_docs_early"):
            reasons.append("sensitive documents were asked for")
        if _sender_off_official(case, ctx.official):
            reasons.append("the recruiter's email or link is not on the official domain")
        if not reasons:
            return (
                "no fee or sensitive documents were asked for, and no recruiter email or link "
                f"is off {ctx.official}",
                False,
            )
        return f"{reasons[0]} and step {ctx.step} named the official domain {ctx.official}", True

    if tool == "confirm_sender_domain":
        if not extra:
            return "sender_lookalike did not fire: there is no look-alike domain to confirm", False
        domain = extra["domain"]
        if ctx.lookalikes.index(domain) >= MAX_CONFIRMATIONS:
            return (
                f"at most {MAX_CONFIRMATIONS} look-alike domains are confirmed, so {domain} "
                "stays a moderate look-alike",
                False,
            )
        return (
            f"sender_lookalike fired for {domain}: a search of {ctx.official} tells an "
            "impostor from the employer's own second domain",
            True,
        )

    if tool == "check_job_listings":
        if role is None:
            return "no role is claimed, so there is no listing to look for", False
        because = f"a role is claimed ({role})"
        if ctx.reorder == "R2":
            because += ", and R2 checks the listings first"
        return because, True

    if tool == "check_office":
        if city is None:
            return "no city or address is claimed, so there is no office to look for", False
        because = f"a city is claimed ({city})"
        if ctx.reorder == "R1":
            because += ", and R1 checks the office before the job listings"
        return because, True

    if tool == "scan_office_reviews":
        office = _latest_current(case, "check_office")
        if office is None or office["facts"]["place_id"] is None:
            return "there is no place from check_office, so there are no reviews to scan", False
        if _fired(case, "fee_requested"):
            trigger = "a fee was asked"
        elif _fired(case, "no_listing_match"):
            trigger = f"no listing by {company} matched"
        else:
            return (
                "no fee was asked and no_listing_match did not fire, so the office's reviews "
                "are not scanned",
                False,
            )
        return (
            f"{trigger} and check_office found {office['facts']['title']}: its reviews are "
            f"scanned for '{extra['term']}'",
            True,
        )

    if tool == "check_scam_reports":
        return f"a company is claimed ({company}): news reports of fake offers in its name", True

    # check_contact_footprint
    if not extra:
        return "no recruiter phone or email was extracted", False
    contact = claims["contacts"][extra["contact_index"]]
    if contact["synthetic"]:
        return (
            f"the recruiter's {contact['kind']} is a synthetic placeholder, so it is never "
            "searched",
            False,
        )
    return (
        f"the offer gives a recruiter {contact['kind']}: whether it already appears next to "
        "scam reports",
        True,
    )


def _guard(run: _Run, ctx: _Context | None) -> Stop | None:
    """The stop rules, checked before each search: after every search, whenever another one
    is due. Decisiveness is judged only from step 1 on (``ctx``): before it, this
    investigation has found nothing yet, and only step 1 says which look-alikes are open."""
    case = run.case
    budget = case["budget"]
    spent, limit = budget["spent"], budget["maxSearches"]
    if (
        run.decisive
        and ctx is not None
        and is_decisive(case["signals"], open_domains=_open(case, ctx))
    ):
        saved = limit - spent
        return (
            "decisive",
            f"decisive: the band is high_risk and no search left can change it, so "
            f"{_searches(saved)} {'was' if saved == 1 else 'were'} not spent",
            saved,
        )
    if spent >= limit:
        return "budget", f"the search budget is spent: {spent} of {limit}", 0
    if run.left is not None and run.left < run.reserve:
        return (
            "quota",
            f"quota guard: {_searches(run.left)} left this month, below the reserve of "
            f"{run.reserve}; stopped, with no fallback to other data",
            0,
        )
    return None


def _read_quota(run: _Run) -> Stop | None:
    try:
        account = run.provider.account()
    except SearchError as e:
        return "search_error", f"the Account API failed: {e.message}; nothing was searched", 0
    run.left = account["plan_searches_left"] if account else None
    return None


def _failed(line: Line) -> Stop:
    return "search_error", f"{line['tool']} failed: {line['note']}; nothing was made up", 0


def _finish(run: _Run, stop: Stop) -> None:
    reason, because, saved = stop
    run.case["budget"].update(saved=saved, stoppedBecause=reason)
    _line(
        run.case,
        tool=run.verb,
        actor=run.actor,
        action="stopped",
        because=because,
        saved=saved,
    )


def _reorder(run: _Run, tool: str, because: str) -> None:
    _line(run.case, tool=tool, actor=run.actor, action="reordered", because=because)


def _context(case: Case, step1: Line) -> _Context:
    fired = _rules(case, step1)
    official = step1["facts"]["officialDomain"]
    if "no_web_footprint" in fired or official is None:
        reorder = "R1"
    elif "sender_official" in fired and not _fee_asked(case):
        reorder = "R2"
    else:
        reorder = None
    by_id = {s["id"]: s for s in case["signals"]}
    lookalikes = dict.fromkeys(
        by_id[i]["domain"] for i in step1["signalsAdded"] if by_id[i]["rule"] == "sender_lookalike"
    )
    return _Context(step1["step"], official, reorder, tuple(lookalikes))


def _open(case: Case, ctx: _Context | None) -> list[str]:
    """Look-alike domains a confirming search may still withdraw: step 1's, up to the
    planner's limit, during a plan; every look-alike standing on the case for a check called
    on its own (``ctx`` None)."""
    if ctx is None:
        candidates = [
            s["domain"] for s in case["signals"] if s["rule"] == "sender_lookalike" and _live(s)
        ]
    else:
        candidates = list(ctx.lookalikes[:MAX_CONFIRMATIONS])
    confirmed = {
        line["args"]["domain"]
        for line in _current(case)
        if line["tool"] == "confirm_sender_domain" and line["action"] == "ran"
    }
    return [d for d in candidates if d not in confirmed]


# ---- step 0 and reuse ----------------------------------------------------------------------


def _text_step(case: Case) -> None:
    # open_case may have fired the text rules already, and a rule never fires twice while its
    # signal stands. A rule fires only while the confirmed claims support it (fee_requested
    # needs a fee claim), so one a correction made stale fires again, replacing it, only once
    # a person says a fee is asked after all.
    text = [s for s in case["signals"] if s["source"] == TEXT_SOURCE]
    standing = {s["rule"] for s in text if _live(s)}
    hits = [
        h
        for h in text_rules(case["sourceText"])
        if h["rule"] not in standing and (h["rule"] != "fee_requested" or _fee_asked(case))
    ]
    fired = {h["rule"] for h in hits}
    _supersede((s for s in text if s["rule"] in fired), "fired again at step 0")
    _add(case, [text_signal(hit, None) for hit in hits])
    live = [s for s in case["signals"] if s["source"] == TEXT_SOURCE and _live(s)]
    rules = [s["rule"] for s in live]
    _line(
        case,
        step=0,
        tool="text_rules",
        actor="agent",
        action="ran",
        because="text rules read the message itself and cost no search",
        added=[s["id"] for s in live],
        note=f"fired: {', '.join(rules)}" if rules else "no text rule fired",
    )


def reusable(case: Mapping[str, Any], earlier: Mapping[str, Any]) -> bool:
    """Whether ``earlier`` answers ``case`` at 0 searches: the same fingerprint, and an
    investigation that finished."""
    budget = earlier.get("budget") or {}
    return (
        budget.get("stoppedBecause") not in _NOT_REUSABLE | {None}
        and case.get("fingerprint") is not None
        and case["fingerprint"] == earlier.get("fingerprint")
    )


def _reuse(case: Case, earlier: Mapping[str, Any]) -> None:
    # Only the signals the copied lines added are copied, so every one keeps the step that
    # found it.
    lines = _current(earlier)[1:]
    by_id = {s["id"]: s for s in earlier["signals"]}
    ids: dict[str, str] = {}
    for line in lines:
        for sid in line["signalsAdded"]:
            s = by_id[sid]
            if sid in ids or s["source"] == TEXT_SOURCE or not _live(s):
                continue
            (ids[sid],) = _add(case, [{**copy.deepcopy(s), "reusedFrom": earlier["id"]}])
    saved = earlier["budget"]["spent"]
    _line(
        case,
        tool="investigate",
        actor="agent",
        action="reused",
        because=f"same message as {earlier['id']}: reused, 0 searches",
        saved=saved,
        added=list(ids.values()),
        reused_from=earlier["id"],
    )
    for line in lines:
        case["trace"].append(
            {
                **copy.deepcopy(line),
                "signalsAdded": [ids[i] for i in line["signalsAdded"] if i in ids],
                "reusedFrom": earlier["id"],
            }
        )
    case["budget"].update(saved=saved, stoppedBecause="same_as")


# ---- the case ------------------------------------------------------------------------------


def _line(
    case: Case,
    *,
    tool: str,
    actor: str,
    action: str,
    because: str,
    step: int | None = None,
    args: dict[str, Any] | None = None,
    engine: str | None = None,
    params: dict[str, Any] | None = None,
    provider: str | None = None,
    cache: str | None = None,
    ms: int | None = None,
    spent: int = 0,
    saved: int = 0,
    added: Iterable[str] = (),
    note: str | None = None,
    facts: dict[str, Any] | None = None,
    reused_from: str | None = None,
) -> Line:
    line = {
        "step": step,
        "tool": tool,
        "args": args,
        "actor": actor,
        "action": action,
        "because": because,
        "engine": engine,
        "params": params,
        "provider": provider,
        "cache": cache,
        "ms": ms,
        "searchesSpent": spent,
        "searchesSaved": saved,
        "signalsAdded": list(added),
        "band": _band(case),
        "note": note,
        "facts": facts,
        "reusedFrom": reused_from,
    }
    case["trace"].append(line)
    return line


def _add(case: Case, signals: Iterable[dict[str, Any]]) -> list[str]:
    ids = []
    for s in signals:
        sid = f"sig_{len(case['signals']) + 1}"
        case["signals"].append({**s, "id": sid})
        ids.append(sid)
    return ids


def _band(case: Case) -> Band:
    return decide(case["signals"], company_claimed=case["claims"].get("company") is not None).band


def _budget(case: Case) -> dict[str, Any]:
    if case.get("budget") is None:
        case["budget"] = {
            "maxSearches": configured_max_searches(),
            "spent": 0,
            "saved": 0,
            "stoppedBecause": None,
        }
    return case["budget"]


def _outcome(case: Case) -> dict[str, Any]:
    return {
        "caseId": case["id"],
        "band": _band(case),
        "budget": dict(case["budget"]),
        "trace": case["trace"],
        "signals": case["signals"],
    }


def _next_step(case: Case) -> int:
    return max((line["step"] for line in case["trace"] if line["step"] is not None), default=0) + 1


def _current(case: Mapping[str, Any]) -> list[Line]:
    """The lines of the latest investigation, from its step 0 on."""
    trace = case["trace"]
    starts = [i for i, line in enumerate(trace) if line["tool"] == "text_rules"]
    return trace[starts[-1] :] if starts else []


def _current_step1(case: Case) -> Line:
    return next(
        line
        for line in _current(case)
        if line["tool"] == "lookup_official_site" and line["action"] == "ran"
    )


def _latest(case: Case, tool: str) -> Line | None:
    return next(
        (
            line
            for line in reversed(case["trace"])
            if line["tool"] == tool and line["action"] == "ran"
        ),
        None,
    )


def _latest_current(case: Case, tool: str) -> Line | None:
    return next(
        (
            line
            for line in reversed(_current(case))
            if line["tool"] == tool and line["action"] == "ran"
        ),
        None,
    )


def _identity(tool: str, args: Mapping[str, Any]) -> tuple:
    # A wording is another way to ask the same check, so a run in either wording is the same
    # check: it supersedes the other's findings and counts as done.
    return tool, tuple(sorted((k, v) for k, v in args.items() if k not in ("case_id", "wording")))


def _inconclusive(line: Line) -> bool:
    return line["action"] == "ran" and bool((line["facts"] or {}).get("inconclusive"))


def inconclusive_notice_search(case: Mapping[str, Any]) -> dict[str, Any] | None:
    """The fraud-notice search of the latest investigation, when its last run returned no page
    from the official domain: ``{"steps", "official"}`` with every such run's step number, or
    None. A drafted verdict says so instead of leaving the search out, since no signal marks
    it."""
    runs = [
        line
        for line in _current(case)
        if line["tool"] == "find_fraud_notice" and line["action"] == "ran"
    ]
    if not runs or not _inconclusive(runs[-1]):
        return None
    official = _official(case)
    return {"steps": [line["step"] for line in runs if _inconclusive(line)], "official": official}


def _rules(case: Case, line: Line) -> set[str]:
    wanted = set(line["signalsAdded"])
    return {s["rule"] for s in case["signals"] if s["id"] in wanted}


def _live(signal: Mapping[str, Any]) -> bool:
    return not signal["stale"] and not signal.get("superseded")


def _supersede(signals: Iterable[dict[str, Any]], why: str) -> None:
    for s in signals:
        if not s.get("superseded"):
            s["superseded"] = why


def _found_by(case: Case, tool: str, args: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The signals earlier runs of this check, with these args, fired."""
    ids = {
        sid
        for line in case["trace"]
        if line["tool"] == tool
        and line["action"] == "ran"
        and _identity(tool, line["args"]) == _identity(tool, args)
        for sid in line["signalsAdded"]
    }
    return [s for s in case["signals"] if s["id"] in ids]


def _fired(case: Case, rule: str) -> bool:
    return any(s["rule"] == rule and _live(s) and not s.get("setAside") for s in case["signals"])


def _fee_asked(case: Case) -> bool:
    return case["claims"].get("fee") is not None


def _sender_off_official(case: Case, official: str) -> bool:
    # A recruiter email or link on another registrable domain, unless sender_official vouched
    # for the sender anyway (the employer's own second domain). With no recruiter email or
    # link there is no sender domain to call unofficial. sender_official alone can't decide
    # it: with no knowledge graph it never fires, even for a link on the official domain.
    off = _offer_domains(case["claims"]) - {official}
    return bool(off) and not _fired(case, "sender_official")


def _offer_domains(claims: Mapping[str, Any]) -> set[str]:
    values = [c["value"] for c in claims["contacts"] if c["kind"] == "email"]
    values += [link["value"] for link in claims["links"]]
    return {d for v in values if (d := registrable_domain(v))}


def _official(case: Case) -> str | None:
    lookup = _latest(case, "lookup_official_site")
    return lookup["facts"]["officialDomain"] if lookup else None


def _official_or_refuse(case: Case, tool: str) -> str:
    official = _official(case)
    if official is None:
        raise PlanRefused(
            f"{tool} needs the employer's official domain, and lookup_official_site has not "
            "found one"
        )
    return official


def _value(claims: Mapping[str, Any], name: str) -> str | None:
    claim = claims.get(name)
    return claim["value"] if claim else None


def _need(claims: Mapping[str, Any], name: str, tool: str) -> str:
    value = _value(claims, name)
    if value is None:
        raise PlanRefused(f"{tool} needs a claimed {name}, and the offer names none")
    return value


def _searches(n: int) -> str:
    return f"{n} search" if n == 1 else f"{n} searches"
