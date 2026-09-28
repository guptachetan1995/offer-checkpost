"""The drafts a person reads, copies or publishes: the verdict summary, the verification reply
to the recruiter, the summary for the cybercrime helpline 1930, and, once a person has
published a verdict, its "Copy for WhatsApp" text and the downloadable Offer Board page.

Every draft is built from the case alone: its claims, its signals, and the trace that says
which step added each signal. A draft cites only the signals the decision table counts, so a
stale, superseded, replaced or set-aside signal is never cited, and every search-sourced
signal it cites must be named in a numbered trace step's ``signalsAdded``. A finding is worded
as its reader wrote it (``detail``) or quoted from the message itself, so no draft states more
than the evidence. Each draft names the case ``revision`` it was written from, so a draft
older than a correction or a check can be told apart from a current one.

A published post is a snapshot (``post``): the message's claims and the evidence cards as they
stood when a person published it. Its WhatsApp text and its place on the board page are built
from the post alone, never from the case, so nothing done to the case later changes what a
student was sent.

Nothing here sends, files or publishes anything, and no draft calls an offer genuine or safe.
"""

from __future__ import annotations

import copy
import html
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from offer_checkpost.checks import search_name
from offer_checkpost.domains import registrable_domain
from offer_checkpost.rules import _STRENGTH, RULES, TEXT_SOURCE, Decision, Signal, _sift, decide

Case = dict[str, Any]

BOARD_LABELS = {
    "likely_impersonation": "Likely impersonation",
    "pay_to_apply_red_flag": "Pay-to-apply red flag",
    "unverified_ask_questions": "Unverified: ask questions first",
    "no_contradictions_found": "No contradictions found",
}

# The 1930 summary names the band in words a helpline reader needs, never "genuine".
_BAND_WORDS = {
    "high_risk": "high risk",
    "unverified": "unverified",
    "consistent_with_genuine": "nothing found contradicts the offer",
}

_IST = timezone(timedelta(hours=5, minutes=30))

_RED_LABELS = frozenset({"likely_impersonation", "pay_to_apply_red_flag"})

# Each of these rules quotes a page, a website or an apply link on the official domain, by
# construction of its reader, so its evidence link names the official domain.
_ON_OFFICIAL = frozenset(
    {
        "sender_lookalike",
        "sender_free_mail",
        "sender_official",
        "domain_named_in_fraud_notice",
        "fee_contradicts_employer",
        "employer_fraud_notice_exists",
        "listing_match",
    }
)

_ORDER = {name: i for i, name in enumerate(RULES)}


# ---- evidence -----------------------------------------------------------------------------


def citations(case: Case, signal_ids: Iterable[str]) -> list[dict[str, Any]]:
    """One evidence card per signal id: the rule's label and grade, whether it quotes the
    message (``fromText``, from the signal's source), the numbered trace step that added the
    signal (``step`` and ``tool`` are None for a text rule), the finding in the reader's own
    words, and the evidence the signal carries.

    A search-sourced signal that no numbered trace step lists in ``signalsAdded`` raises
    ``KeyError``: a draft cites only what the trace holds, and a line that ran no search (a
    reuse) is never where a finding came from."""
    by_id = {s["id"]: s for s in case["signals"]}
    added_by = {
        sid: step
        for step in case.get("trace") or ()
        if step.get("step") is not None
        for sid in step.get("signalsAdded") or ()
    }
    cards = []
    for sid in signal_ids:
        signal = by_id[sid]
        rule = RULES[signal["rule"]]
        from_text = signal["source"] == TEXT_SOURCE
        step = None if from_text else added_by[sid]
        cards.append(
            {
                "signalId": sid,
                "rule": rule.name,
                "label": rule.label,
                "direction": rule.direction,
                "severity": rule.severity,
                "fromText": from_text,
                "step": None if step is None else step["step"],
                "tool": None if step is None else step["tool"],
                "finding": f'"{signal["evidence"]["quote"]}"' if from_text else signal["detail"],
                "evidence": signal["evidence"],
            }
        )
    return cards


def _source(evidence: dict[str, Any]) -> str:
    parts = [f'"{evidence[key]}"' for key in ("title", "snippet") if evidence[key]]
    parts.append(evidence["link"] or f'{evidence["engine"]} search "{evidence["query"]}"')
    if evidence["date"]:
        parts.append(f"dated {evidence['date']}")
    return f"Source: {' · '.join(parts)} (retrieved {_day(evidence['retrievedAt'])})"


def _line(card: dict[str, Any]) -> str:
    if card["fromText"]:
        return f"{card['label']} (the message): {card['finding']}"
    where = f"step {card['step']}, {card['tool']} on {card['evidence']['engine']}"
    return f"{card['label']} ({where}): {card['finding']}. {_source(card['evidence'])}"


def _plain_line(card: dict[str, Any]) -> str:
    link = card["evidence"].get("link")
    return f"{card['label']}: {card['finding']}" + (f" {link}" if link else "")


# ---- the case -----------------------------------------------------------------------------


def _decision(case: Case) -> Decision:
    return decide(case["signals"], company_claimed=case["claims"]["company"] is not None)


def _counted(case: Case) -> tuple[list[Signal], list[str]]:
    return _sift(case["signals"])


def _by_strength(signals: list[Signal]) -> list[tuple[str, list[Signal]]]:
    """Red signals grouped by rule, a rule counted once, strongest first, then table order."""
    groups: dict[str, list[Signal]] = {}
    for s in signals:
        if RULES[s["rule"]].direction == "red":
            groups.setdefault(s["rule"], []).append(s)
    ranked = sorted(groups, key=lambda r: (_STRENGTH[RULES[r].severity], _ORDER[r]))
    return [(rule, groups[rule]) for rule in ranked]


def _official_domain(counted: list[Signal]) -> str | None:
    return next(
        (
            registrable_domain(s["evidence"]["link"])
            for s in counted
            if s["rule"] in _ON_OFFICIAL and s["evidence"]["link"]
        ),
        None,
    )


def _value(claims: dict[str, Any], name: str, key: str = "value") -> str | None:
    claim = claims[name]
    return None if claim is None else claim[key]


def _offer_line(claims: dict[str, Any]) -> str | None:
    company = _value(claims, "company")
    pay, fee = _value(claims, "pay", "raw"), _value(claims, "fee", "raw")
    parts = [
        _value(claims, "role"),
        company and f"in the name of {company}",
        _value(claims, "city"),
        pay and f"pay {pay}",
        fee and f'fee asked: "{fee}"',
    ]
    return " · ".join(p for p in parts if p) or None


# How the last investigation ended, keyed by the planner's stoppedBecause.
STOPPED = {
    None: "",
    "decisive": ", {saved} not spent: the evidence was decisive",
    "done": ": every check that applied has run",
    "budget": ": the per-case budget of {maxSearches} searches ran out",
    "quota": "; the quota guard stopped the checks: too few searches are left this month",
    "search_error": "; a search failed, so the checks stopped and nothing was made up",
    "no_company": ": the message names no company to check on the web",
    "same_as": "",
}


def _searches(case: Case) -> str:
    budget = case["budget"]
    # sameAs only says the fingerprints match; the planner decides whether it reused the case.
    if budget["stoppedBecause"] == "same_as":
        return f"Same message as {case['sameAs']}: its checks were reused, 0 searches spent."
    spent, stopped = budget["spent"], budget["stoppedBecause"]
    # A local cache hit costs 0, so 0 spent after an investigation is not "no searches".
    if stopped in (None, "no_company") and not spent:
        line = "No searches run"
    else:
        line = f"Searches: {spent} spent"
    return line + STOPPED[stopped].format(**budget) + "."


def _day(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone(_IST)
    return f"{d.day} {d:%b %Y}"


def _moment(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone(_IST)
    return f"{d.day} {d:%b %Y, %H:%M} IST"


# ---- the draft verdict --------------------------------------------------------------------


def verdict(case: Case, *, drafted_at: str) -> dict[str, Any]:
    """``draftVerdict``: the band from the decision table and a summary that leads with the
    table's own one-line summary (so a text-only case leads with its text red flags), then
    one line per signal behind the band, then any other counted signal, then each signal the
    table set aside and why, then the searches spent and saved."""
    decision = _decision(case)
    counted, set_aside = _counted(case)
    cited = list(decision.evidence_ids)
    others = [s["id"] for s in counted if s["id"] not in cited]
    lines = [decision.summary + "."]
    if cited:
        lines += ["Evidence:"] + [f"- {_line(c)}" for c in citations(case, cited)]
    if others:
        lines += ["Also found:"] + [f"- {_line(c)}" for c in citations(case, others)]
    lines += [f"Not counted: {reason}." for reason in set_aside]
    lines.append(_searches(case))
    return {
        "band": decision.band,
        "summary": "\n".join(lines),
        "evidenceIds": cited,
        "revision": case["revision"],
        "draftedAt": drafted_at,
    }


# ---- the recruiter reply ------------------------------------------------------------------


@dataclass(frozen=True)
class _Offer:
    company: str | None
    role: str | None
    city: str | None
    pay: str | None
    fee: str | None
    fee_inr: float | None
    official: str | None

    @property
    def near(self) -> str:
        return f" near {self.city}" if self.city else ""

    @property
    def official_address(self) -> str:
        if self.official:
            return f"your @{self.official} address"
        return "the company's official email address"

    @property
    def about(self) -> str:
        if self.role and self.company:
            return f" about the {self.role} role at {self.company}"
        if self.role:
            return f" about the {self.role} role"
        if self.company:
            return f" about the offer from {self.company}"
        return ""


def _offer(case: Case, counted: list[Signal]) -> _Offer:
    claims = case["claims"]
    company = _value(claims, "company")
    return _Offer(
        company=company and search_name(company),
        role=_value(claims, "role"),
        city=_value(claims, "city"),
        pay=_value(claims, "pay", "raw"),
        fee=_value(claims, "fee", "raw"),
        fee_inr=_value(claims, "fee", "amountInr"),
        official=_official_domain(counted),
    )


def _quoted(evidence: dict[str, Any]) -> str:
    return f'"{evidence["snippet"] or evidence["title"]}" ({evidence["link"]})'


def _domains(found: list[Signal]) -> str:
    return " and ".join(dict.fromkeys(s["domain"] for s in found))


Question = Callable[[_Offer, list[Signal]], str]

QUESTIONS: dict[str, Question] = {
    "fee_requested": lambda o, found: (
        f'Your message asks for "{o.fee}". Who would receive this money: which bank account '
        "or UPI ID, and under what registered company name?"
    ),
    "task_scam_pattern": lambda o, found: (
        "Your message offers pay for online tasks. What is the job title, the employer's "
        "registered company name and its office address?"
    ),
    "sensitive_docs_early": lambda o, found: (
        "Why are identity or bank documents needed before any interview has taken place?"
    ),
    "chat_only_interview": lambda o, found: (
        "Can the interview be held on a video call or in person, rather than only in a chat?"
    ),
    "sender_lookalike": lambda o, found: (
        f"Please resend this offer from {o.official_address}. Your message uses {_domains(found)}."
    ),
    "domain_named_in_fraud_notice": lambda o, found: (
        f"{o.official}'s own site says: {_quoted(found[0]['evidence'])}. Please resend this "
        f"offer from {o.official_address}."
    ),
    "sender_free_mail": lambda o, found: (
        f"Please resend this offer from {o.official_address}, not from a {_domains(found)} "
        "address."
    ),
    "fee_contradicts_employer": lambda o, found: (
        f"{o.official}'s own site says: {_quoted(found[0]['evidence'])}. Why does this offer "
        f"ask for ₹{o.fee_inr:,.0f}?"
    ),
    "employer_fraud_notice_exists": lambda o, found: (
        f"{o.official} publishes a recruitment-fraud notice ({found[0]['evidence']['link']}). "
        f"Where on {o.official} is a fee of ₹{o.fee_inr:,.0f} for this role stated?"
    ),
    "no_web_footprint": lambda o, found: (
        f"I could not find a website for {o.company} in a web search. What is the company's "
        "website, and its registered office address?"
    ),
    "pay_outlier": lambda o, found: (
        f"The pay offered ({o.pay}) is far above comparable {o.role} listings{o.near}. Please "
        "send the written offer letter with the salary break-up."
    ),
    "no_listing_match": lambda o, found: (
        f"I could not find this {o.role} opening by {o.company}{o.near} on Google Jobs. Can "
        "you share a link to it on the company's own careers page?"
    ),
    "office_not_found": lambda o, found: (
        f"I could not find an office of {o.company} in {o.city} on Google Maps. What is its "
        "full address?"
    ),
    "reviews_mention_fees": lambda o, found: (
        f"Will any payment be asked for at the {o.company} office{o.near}, at any stage of hiring?"
    ),
    "impersonation_reports": lambda o, found: (
        f"News reports describe fake job offers made in {o.company}'s name. Can you confirm "
        f"that this offer comes from {o.company}'s own recruitment team?"
    ),
    "contact_reported": lambda o, found: (
        "The contact details in your message appear on pages that report scams. Please "
        f"confirm this offer from {o.official_address}."
    ),
    "recruiter_site_new": lambda o, found: (
        "The website linked in your message was first indexed by Google within the past year. "
        "Since when has the company used it?"
    ),
}


def recruiter_reply(case: Case, *, drafted_at: str) -> dict[str, Any]:
    """``draftReply``: a reply the person can send the recruiter themselves, with one
    verification question per red flag the decision table counts (a rule counted once,
    strongest first). Each question carries the ids of the signals behind it; the text sent
    to the recruiter carries none. With no red flag it asks only for the offer in writing."""
    counted, _ = _counted(case)
    offer = _offer(case, counted)
    questions = [
        {
            "rule": rule,
            "signalIds": [s["id"] for s in found],
            "question": QUESTIONS[rule](offer, found),
        }
        for rule, found in _by_strength(counted)
    ]
    lines = ["Hello,", "", f"Thank you for your message{offer.about}."]
    if questions:
        lines[-1] += " Before I go any further, please answer these questions in writing:"
        lines.append("")
        lines += [f"{n}. {q['question']}" for n, q in enumerate(questions, 1)]
        lines += ["", "I will go ahead once these are answered."]
    else:
        lines[-1] += (
            " Before I go any further, please send the offer in writing from "
            f"{offer.official_address}."
        )
    return {
        "text": "\n".join(lines),
        "questions": questions,
        "evidenceIds": [sid for q in questions for sid in q["signalIds"]],
        "revision": case["revision"],
        "draftedAt": drafted_at,
    }


# ---- the 1930 summary ---------------------------------------------------------------------


def cybercrime_report(case: Case, *, drafted_at: str) -> dict[str, Any]:
    """``draftReport``: a summary for helpline 1930 / cybercrime.gov.in that the person reviews,
    completes and reports themselves: what the message claims, what it asked for (money and
    the text red flags, quoted), when it was checked, the sender's contacts and links exactly
    as written, what web searches found with their sources, and the drafted band."""
    claims = case["claims"]
    counted, _ = _counted(case)
    cards = citations(case, [s["id"] for s in _red_first(counted)])
    from_text = [c for c in cards if c["fromText"]]
    red = [c for c in cards if not c["fromText"] and c["direction"] == "red"]
    green = [c for c in cards if not c["fromText"] and c["direction"] == "green"]
    fee = claims["fee"]
    if fee is None:
        money = "Money asked for: none named in the message."
    elif fee["amountInr"] is None:
        money = f'Money: the message mentions "{fee["raw"]}" but names no amount.'
    else:
        money = f'Money asked for: ₹{fee["amountInr"]:,.0f} ("{fee["raw"]}").'

    lines = [
        "DRAFT: summary for the National Cybercrime Helpline 1930 / cybercrime.gov.in",
        "Offer Checkpost drafted this and files nothing. Check every line, add what only you "
        "know, and report it yourself on 1930 or at cybercrime.gov.in.",
        "",
        f"What happened: a job offer arrived as a message. It was checked on "
        f"{_moment(case['createdAt'])} (case {case['id']}).",
        f"What the message claims: {_offer_line(claims) or 'no company, role or city'}.",
        money,
        "Red flags in the message itself:",
        *([f"- {_plain_line(c)}" for c in from_text] or ["- none"]),
        "The sender's contacts, exactly as written in the message:",
        *([f"- {c['kind']}: {c['value']}" for c in claims["contacts"]] or ["- none"]),
        "Links in the message:",
        *([f"- {link['value']}" for link in claims["links"]] or ["- none"]),
        "Red flags found by web searches:",
        *([f"- {_line(c)}" for c in red] or ["- none"]),
    ]
    if green:
        lines += ["Found consistent with the offer:", *[f"- {_line(c)}" for c in green]]
    lines += [
        _searches(case),
        f"Drafted verdict: {_BAND_WORDS[_decision(case).band]}. Software drafted this from the "
        "evidence above; it is not a finding by any authority.",
        "",
        "Add before reporting: when the message arrived, and on which app or number; any money "
        "paid (amount, date and time, the UPI ID or bank account it went to, the transaction "
        "ID); any documents or OTPs you shared.",
        "",
        "The message as pasted:",
        case["sourceText"],
    ]
    return {
        "text": "\n".join(lines),
        "evidenceIds": [c["signalId"] for c in cards],
        "revision": case["revision"],
        "draftedAt": drafted_at,
    }


def _red_first(counted: list[Signal]) -> list[Signal]:
    red = [s for rule, found in _by_strength(counted) for s in found]
    return red + [s for s in counted if RULES[s["rule"]].direction == "green"]


# ---- a published verdict: the post, "Copy for WhatsApp" and the board page ------------------


def label_misfit(label: str, band: str) -> str | None:
    """Why ``label`` would say more than a verdict drafted as ``band`` found, or None when it
    fits. "No contradictions found" is the best band's own wording, so it takes that band and
    no other; a red-flag label says the checks found something against the offer, so it never
    takes the best band. "Unverified: ask questions first" fits any band."""
    if label == "no_contradictions_found" and band != "consistent_with_genuine":
        return (
            f"the label '{BOARD_LABELS[label]}' is not what the checks found: the draft verdict "
            f"is {_BAND_WORDS[band]}"
        )
    if label in _RED_LABELS and band == "consistent_with_genuine":
        return (
            f"the label '{BOARD_LABELS[label]}' says more than the checks found: nothing they "
            "found contradicts the offer"
        )
    return None


def post(
    case: Case, *, label: str, note: str, published_at: str, by: str = "human"
) -> dict[str, Any]:
    """A board post for ``case``: the person's label and note, and a snapshot of the drafted
    band, the message's claims (``offer``) and the evidence cards behind the band, with its
    "Copy for WhatsApp" text built from them."""
    decision = _decision(case)
    published = {
        "caseId": case["id"],
        "label": label,
        "note": note,
        "band": decision.band,
        "offer": _offer_line(case["claims"]),
        "evidence": copy.deepcopy(citations(case, decision.evidence_ids)),
        "publishedAt": published_at,
        "by": by,
    }
    published["whatsapp"] = whatsapp_text(published)
    return published


def _footer(band: str) -> list[str]:
    lines = ["This describes this message, not the company named in it."]
    if band == "consistent_with_genuine":
        lines.append("Nothing the checks found contradicts it; that is not proof it is real.")
    return lines


def whatsapp_text(post: dict[str, Any]) -> str:
    """The "Copy for WhatsApp" text of one board post: the person's label and note, what the
    message claims, and the evidence behind the drafted band with its links. It names no
    recruiter phone number or email address."""
    offer = post["offer"]
    lines = [
        f"*Offer check: {BOARD_LABELS[post['label']]}*",
        f"The message: {offer}" if offer else "The message names no company, role or city.",
        "",
        "What the checks found:",
        *[f"• {_plain_line(c)}" for c in post["evidence"]],
    ]
    if post["note"]:
        lines += ["", f"Note: {post['note']}"]
    lines += ["", *_footer(post["band"])]
    lines.append(
        f"Published {_moment(post['publishedAt'])} by a person, from web search results "
        "checked with Offer Checkpost."
    )
    return "\n".join(lines)


_CSS = """
:root { color-scheme: light dark; --bg: #fbfaf7; --fg: #1d1d1b; --muted: #5d5b55;
  --card: #ffffff; --line: #e3e0d8; --red: #a3261b; --amber: #8a5a00; --green: #2f6b3a;
  --link: #1f5fa8; }
@media (prefers-color-scheme: dark) { :root { --bg: #141412; --fg: #ecebe6;
  --muted: #a9a79f; --card: #1d1d1a; --line: #33322e; --red: #f08a7e; --amber: #e8b75a;
  --green: #8fd19c; --link: #8ab8f0; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { margin: 0 0 4px; font-size: 1.6rem; }
h2 { margin: 4px 0 8px; font-size: 1.1rem; overflow-wrap: anywhere; }
h3 { margin: 12px 0 4px; font-size: 0.95rem; color: var(--muted); }
.lede, .meta, footer { color: var(--muted); }
.meta { font-size: 0.9rem; }
.post { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px; margin: 16px 0; }
.label { display: inline-block; margin: 0; font-weight: 600; font-size: 0.9rem; }
.label.likely_impersonation, .label.pay_to_apply_red_flag { color: var(--red); }
.label.unverified_ask_questions { color: var(--amber); }
.label.no_contradictions_found { color: var(--green); }
ul { padding-left: 20px; margin: 4px 0; }
li { margin: 6px 0; overflow-wrap: anywhere; }
a { color: var(--link); }
"""


def _link(url: str | None) -> str:
    # A search result's link is outside input; a downloaded page must never carry a
    # javascript: or data: link.
    if not url or not url.startswith(("https://", "http://")):
        return ""
    href = html.escape(url)
    return f' <a href="{href}" rel="noopener noreferrer nofollow">{href}</a>'


def _item_html(card: dict[str, Any]) -> str:
    evidence = card["evidence"]
    item = f"<strong>{html.escape(card['label'])}</strong>: {html.escape(card['finding'])}"
    item += _link(evidence.get("link"))
    if not card["fromText"]:
        item += f' <span class="meta">(retrieved {_day(evidence["retrievedAt"])})</span>'
    return f"<li>{item}</li>"


def _post_html(post: dict[str, Any]) -> str:
    e = html.escape
    offer = post["offer"] or "The message names no company, role or city."
    items = "".join(_item_html(c) for c in post["evidence"])
    note = f'<p class="note">{e(post["note"])}</p>' if post["note"] else ""
    footer = "".join(f'<p class="meta">{e(line)}</p>' for line in _footer(post["band"]))
    return (
        f'<article class="post" id="{e(post["caseId"])}">'
        f'<p class="label {e(post["label"])}">{e(BOARD_LABELS[post["label"]])}</p>'
        f"<h2>{e(offer)}</h2>{note}"
        f"<h3>What the checks found</h3><ul>{items}</ul>{footer}"
        f'<p class="meta">Published {e(_moment(post["publishedAt"]))} by a person '
        f"· {e(post['caseId'])}</p></article>"
    )


def board_html(board: list[dict[str, Any]], *, generated_at: str) -> str:
    """The downloadable Offer Board: one self-contained HTML page (no script, no external
    file) with every published post, each with its label, note, the message's claims and the
    evidence behind it, all from the post as it was published. A retracted post is no longer
    on the board, so it is not on the page."""
    posts = "".join(_post_html(post) for post in board)
    count = f"{len(board)} published verdict{'' if len(board) == 1 else 's'}"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Offer Board</title><style>{_CSS}</style></head><body><main>"
        "<h1>Offer Board</h1>"
        '<p class="lede">Job offers checked against web search results. A person published '
        "each verdict below, and each describes the message, not the company named in it.</p>"
        f'<p class="meta">Downloaded {html.escape(_moment(generated_at))} · {count}</p>'
        + (posts or '<p class="meta">No verdicts published yet.</p>')
        + "<footer><p>Made with Offer Checkpost. It drafts from search results; a person "
        "decides and publishes. Nothing here was sent or filed by the app.</p></footer>"
        "</main></body></html>\n"
    )
