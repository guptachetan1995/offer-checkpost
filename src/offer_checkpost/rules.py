"""The rule table and the decision table: every rule, which way it points and how hard, and
how the signals a case has gathered become one of three bands.

The table is the authority on direction and severity. ``decide`` looks each signal's rule up
here rather than trusting the signal's own fields, so no reader can promote its own finding.
A rule counts once however many signals fired it: a recruiter email and a job link both on
the official domain are one ``sender_official``, not two green signals, and two look-alike
domains are one ``sender_lookalike``.

Signals are never deleted. A ``superseded`` one is history, not evidence: a later run of the
same check, or a later investigation of the case, has replaced it. It is left out before
anything else, with no reason line, since the trace keeps the run that found it. Four more kinds
are set aside before counting, each with a reason:
a stale signal (the person corrected a claim it depended on); a ``sender_lookalike`` that a
confirming search replaced for the same domain, by ``domain_named_in_fraud_notice`` (the
employer names it as fake) or by ``sender_official`` (the official site vouches for it); a
fraud-notice rule whose required ``fee_requested`` is no longer there; and a signal its reader
marked ``setAside`` with a reason. The last is how a confirming search withdraws a look-alike
without vouching for the sender: the official site owns the recruiter's link domain, but the
recruiter writes from somewhere else, so the ``sender_official`` that replaces the look-alike
does not count as green.

The best band is ``consistent_with_genuine``: nothing found contradicts the offer. No band
certifies an offer as genuine or safe.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["red", "green"]
Severity = Literal["strong", "moderate", "weak"]
Band = Literal["high_risk", "consistent_with_genuine", "unverified"]
Signal = dict[str, Any]

# A text rule's signal names no engine: it came from the pasted message, not a search.
TEXT_SOURCE = "text"


@dataclass(frozen=True)
class Rule:
    name: str
    engine: str | None  # None for a text rule
    direction: Direction
    severity: Severity | None  # None for a green rule
    label: str  # a short phrase for summaries; never holds a comma
    because: str  # why it points this way at this strength
    requires: str | None = None
    replaces: tuple[str, ...] = ()
    stretch: bool = False

    @property
    def from_search(self) -> bool:
        return self.engine is not None


_TABLE = (
    Rule(
        "fee_requested", None, "red", "strong", "fee asked",
        "the message asks for money, and names an amount, before the job starts",
    ),
    Rule(
        "task_scam_pattern", None, "red", "strong", "task-scam pattern",
        "it offers pay per like, review, rating or subscription, prepaid or deposit tasks, "
        "or daily earnings for simple online tasks",
    ),
    Rule(
        "sensitive_docs_early", None, "red", "moderate",
        "documents asked for before any interview",
        "Aadhaar, PAN, bank details or an OTP are asked for before any interview",
    ),
    Rule(
        "chat_only_interview", None, "red", "weak", "chat-only interview",
        "the only interview is a WhatsApp or Telegram chat; weak on its own",
    ),
    Rule(
        "sender_lookalike", "google", "red", "moderate", "look-alike sender domain",
        "the recruiter's domain imitates the official one; moderate until a confirming "
        "search, because an employer can own a second official domain",
    ),
    Rule(
        "domain_named_in_fraud_notice", "google", "red", "strong",
        "sender domain named in the employer's fraud notice",
        "a page on the official domain names the recruiter's domain next to a fraud term",
        replaces=("sender_lookalike",),
    ),
    Rule(
        "sender_free_mail", "google", "red", "moderate", "free-mail sender",
        "the recruiter writes from a free-mail address for a company with an official domain",
    ),
    Rule(
        "fee_contradicts_employer", "google", "red", "strong",
        "employer says it charges no fee",
        "a fee was asked, and the employer's own recruitment-fraud notice says it charges none",
        requires="fee_requested",
    ),
    Rule(
        "employer_fraud_notice_exists", "google", "red", "weak",
        "employer has a recruitment-fraud notice",
        "a fee was asked and the employer warns of recruitment fraud, but its notice says "
        "nothing about fees",
        requires="fee_requested",
    ),
    Rule(
        "no_web_footprint", "google", "red", "moderate", "no web footprint",
        "no knowledge graph and no search result's title or domain matches the company",
    ),
    Rule(
        "pay_outlier", "google_jobs", "red", "moderate", "pay far above comparable listings",
        "the offered monthly pay is more than 2.0x the median of at least 3 comparable "
        "listings in the city",
    ),
    Rule(
        "no_listing_match", "google_jobs", "red", "weak", "no matching listing",
        "no listing by this company for this role near the city; absence is weak evidence, "
        "never proof",
    ),
    Rule(
        "office_not_found", "google_maps", "red", "moderate", "office not on Maps",
        "no place matches the company at the claimed city or address",
    ),
    Rule(
        "reviews_mention_fees", "google_maps_reviews", "red", "moderate",
        "office reviews mention fees or fraud",
        "a review at the matched office contains the fee or fraud term",
    ),
    Rule(
        "impersonation_reports", "google_news", "red", "weak",
        "news of fake offers in the company's name",
        "news reports fake offers made in this company's name; weak, because big brands are "
        "impersonated constantly",
    ),
    Rule(
        "contact_reported", "google", "red", "strong", "recruiter contact reported as a scam",
        "a recruiter-supplied phone or email appears on pages alongside scam or fraud terms",
    ),
    Rule(
        "recruiter_site_new", "google_about_this_result", "red", "moderate",
        "recruiter site indexed within the past year",
        "a recruiter-supplied link domain that is not official was first indexed by Google "
        "within the past year",
        stretch=True,
    ),
    Rule(
        "sender_official", "google", "green", None, "sender on the official domain",
        "the recruiter writes from the official domain or a subdomain of it, or from a domain "
        "the official site mentions with no fraud term",
        replaces=("sender_lookalike",),
    ),
    Rule(
        "listing_match", "google_jobs", "green", None, "listing applies on the official domain",
        "a listing by this company for this role near the city has an apply option on the "
        "official domain",
    ),
    Rule(
        "office_found", "google_maps", "green", None, "office found on Maps",
        "a place matching the company exists in the claimed city",
    ),
)  # fmt: skip

RULES: dict[str, Rule] = {rule.name: rule for rule in _TABLE}
REPLACEABLE = frozenset(target for rule in _TABLE for target in rule.replaces)

BANDS: tuple[Band, ...] = ("high_risk", "consistent_with_genuine", "unverified")
BAND_TITLES: dict[Band, str] = {
    "high_risk": "High risk",
    "consistent_with_genuine": "Consistent with a genuine offer",
    "unverified": "Unverified",
}

HIGH_RISK_MIN_STRONG = 2
HIGH_RISK_MIN_STRONG_FROM_SEARCH = 1
HIGH_RISK_MIN_SERIOUS = 3  # moderate or strong
HIGH_RISK_MIN_SERIOUS_FROM_SEARCH = 2
GENUINE_MIN_GREEN_FROM_SEARCH = 2

_STRENGTH = {"strong": 0, "moderate": 1, "weak": 2}


@dataclass(frozen=True)
class Decision:
    band: Band
    summary: str  # one line, leading the draft verdict
    evidence_ids: tuple[str, ...]  # the signals behind the band, in the order given
    reasons: tuple[str, ...]  # one line per counted rule, then one per signal set aside


def signal_for(rule: str, signal_id: str | None, evidence: dict[str, Any], **fields) -> Signal:
    """A signal of ``rule`` with the table's direction and severity. ``signal_id`` is None
    until the case numbers the signal. Pass ``domain=`` (the registrable domain) for
    ``sender_lookalike`` and the rules that replace it, which is how a replacement finds the
    look-alike it withdraws, and ``setAside=`` (a reason) for a replacement that must not count
    itself."""
    r = RULES[rule]
    return {
        "id": signal_id,
        "rule": rule,
        "direction": r.direction,
        "severity": r.severity,
        "source": r.engine or TEXT_SOURCE,
        "stale": False,
        "evidence": evidence,
        **fields,
    }


def text_signal(hit: dict[str, Any], signal_id: str | None) -> Signal:
    """The signal for one ``extract.text_rules`` hit, quoting the sentence it fired on."""
    return signal_for(hit["rule"], signal_id, {"quote": hit["quote"], "span": hit["span"]})


def decide(
    signals: Iterable[Signal] = (),
    text_hits: Iterable[dict[str, Any]] = (),
    *,
    company_claimed: bool = True,
) -> Decision:
    """The band for a case's signals plus any raw ``extract.text_rules`` hits, in order:

    1. ``high_risk``: at least 2 strong red signals, at least one of them from a search
       result; or at least 3 red signals of moderate or strong severity, at least 2 of them
       from search results.
    2. ``consistent_with_genuine``: no strong or moderate red signal, and at least 2 green
       signals from search results.
    3. ``unverified``: everything else. When only text rules fired the summary leads with
       them; ``company_claimed=False`` says there was nothing checkable to search.

    A green signal never overrides ``high_risk``, since that band is decided first.
    """
    every = [*signals, *(text_signal(hit, None) for hit in text_hits)]
    counted, set_aside = _sift(every)
    fired = {s["rule"] for s in counted}
    rules = [r for r in _TABLE if r.name in fired]
    reds = sorted((r for r in rules if r.direction == "red"), key=lambda r: _STRENGTH[r.severity])
    greens = [r for r in rules if r.direction == "green"]
    strong = [r for r in reds if r.severity == "strong"]
    serious = [r for r in reds if r.severity != "weak"]

    band: Band
    if (
        len(strong) >= HIGH_RISK_MIN_STRONG
        and _searched(strong) >= HIGH_RISK_MIN_STRONG_FROM_SEARCH
    ):
        band, cited = "high_risk", strong
        summary = f"{_count(strong, 'strong red signal')}, {_of_them(strong)}: {_labels(strong)}"
    elif (
        len(serious) >= HIGH_RISK_MIN_SERIOUS
        and _searched(serious) >= HIGH_RISK_MIN_SERIOUS_FROM_SEARCH
    ):
        band, cited = "high_risk", serious
        summary = (
            f"{_count(serious, 'red signal')} of moderate or strong severity, "
            f"{_of_them(serious)}: {_labels(serious)}"
        )
    elif not serious and _searched(greens) >= GENUINE_MIN_GREEN_FROM_SEARCH:
        band, cited = "consistent_with_genuine", greens
        summary = (
            f"{_count(greens, 'green signal')} from search results and no strong or moderate "
            f"red signal: {_labels(greens)}"
        )
        if reds:
            summary += f"; weak red signals noted: {_labels(reds)}"
    else:
        band, cited = "unverified", rules
        summary = _unverified_summary(reds, greens, company_claimed)

    names = {r.name for r in cited}
    return Decision(
        band=band,
        summary=summary,
        evidence_ids=tuple(s["id"] for s in counted if s["rule"] in names and s["id"]),
        reasons=tuple(_reason(r) for r in reds + greens) + tuple(set_aside),
    )


def is_decisive(
    signals: Iterable[Signal] = (),
    text_hits: Iterable[dict[str, Any]] = (),
    *,
    open_domains: Iterable[str] = (),
) -> bool:
    """True when the band is ``high_risk`` and no further search can change it.

    No green signal overrides ``high_risk``. The one thing a later search can take away is a
    ``sender_lookalike`` that ``confirm_sender_domain`` withdraws, so any look-alike whose
    domain is in ``open_domains`` (not yet confirmed) is left out before deciding.
    """
    still_open = set(open_domains)
    settled = [s for s in signals if not (s["rule"] in REPLACEABLE and s["domain"] in still_open)]
    return decide(settled, text_hits).band == "high_risk"


def _sift(signals: list[Signal]) -> tuple[list[Signal], list[str]]:
    set_aside = []
    live = []
    for s in signals:
        if s.get("superseded"):
            continue
        if s["stale"]:
            set_aside.append(f"{s['rule']} set aside: stale, a claim it depended on was corrected")
        else:
            live.append(s)

    replaced_by = {
        (target, s["domain"]): s["rule"] for s in live for target in RULES[s["rule"]].replaces
    }
    kept = []
    for s in live:
        by = replaced_by.get((s["rule"], s["domain"])) if s["rule"] in REPLACEABLE else None
        if by:
            set_aside.append(f"{s['rule']} set aside: replaced by {by} for {s['domain']}")
        else:
            kept.append(s)

    fired = {s["rule"] for s in kept if not s.get("setAside")}
    counted = []
    for s in kept:
        needs = RULES[s["rule"]].requires
        if s.get("setAside"):
            set_aside.append(f"{s['rule']} set aside: {s['setAside']}")
        elif needs and needs not in fired:
            set_aside.append(f"{s['rule']} set aside: it needs {needs}, which did not fire")
        else:
            counted.append(s)
    return counted, set_aside


def _searched(rules: list[Rule]) -> int:
    return sum(r.from_search for r in rules)


def _count(rules: list[Rule], noun: str) -> str:
    return f"{len(rules)} {noun}{'' if len(rules) == 1 else 's'}"


def _of_them(rules: list[Rule]) -> str:
    n = _searched(rules)
    return f"{n} of them from {'a search result' if n == 1 else 'search results'}"


def _labels(rules: list[Rule]) -> str:
    return ", ".join(r.label for r in rules)


def _reason(rule: Rule) -> str:
    grade = f"red · {rule.severity}" if rule.direction == "red" else "green"
    return f"{rule.name} ({grade}, {rule.engine or 'message text'}): {rule.because}"


def _unverified_summary(reds: list[Rule], greens: list[Rule], company_claimed: bool) -> str:
    nothing_checkable = "nothing checkable was claimed"
    if not reds and not greens:
        return "not enough data: no rule fired" + (
            "" if company_claimed else f"; {nothing_checkable}"
        )
    if not greens and not _searched(reds):
        tail = (
            "no search result confirms or contradicts the claims"
            if company_claimed
            else nothing_checkable
        )
        return f"text-only red flags: {_labels(reds)}; {tail}"
    parts = [f"red: {_labels(reds)}"] if reds else []
    parts += [f"green: {_labels(greens)}"] if greens else []
    return "not enough for another band; " + "; ".join(parts)
