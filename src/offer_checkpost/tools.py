"""The agent's tools: each one's name, argument schema, description and handler.

This registry is everything an agent can call: the planner inside ``investigate``, the CLI's
``--as agent`` mode, or an MCP client. A person's click in the UI calls the same tools through
the same ``invoke``. The human-only verbs live in ``verbs.py`` and are never registered here,
so no tool list the agent reads ever shows them.

A description is the only thing a model reads before it calls a tool, so each one is written
for a reader who can't see the screen, and each one says what the tool does not do.

A handler takes the ``Call`` (the store, who is calling, and whether the planner made the call)
and ``args`` already checked against its schema.

A case whose verdict is on the Offer Board stays as it was published: nothing corrects its
claims or runs a check on it until a person retracts the verdict (``changeable``). The post
keeps its own snapshot all the same, so what students were sent never changes after the click.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from offer_checkpost import drafts, planner
from offer_checkpost.extract import extract_claims, text_rules
from offer_checkpost.rules import RULES, text_signal
from offer_checkpost.store import STATUSES, Refused, Store


@dataclass(frozen=True)
class Call:
    store: Store
    actor: str
    # A check the planner makes inside investigate or run_remaining_checks. The planner applies
    # its stop rules between searches itself, from one Account API read per run.
    by_planner: bool = False


Handler = Callable[[Call, dict[str, Any]], Any]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Handler

    def listing(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


def obj(properties: dict[str, Any], *required: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


CASE_ID = {
    "type": "string",
    "pattern": r"^case_[0-9]{3,}$",
    "description": "The case's id, as open_case or list_cases gave it, e.g. case_001.",
}

# The claims a person or the agent may correct. Contacts and links stay as extracted.
CLAIM_FIELDS = ("company", "role", "city", "pay", "fee")

_SEARCH_RULES = frozenset(name for name, rule in RULES.items() if rule.from_search)
_JOB_RULES = frozenset({"listing_match", "no_listing_match", "pay_outlier"})
_OFFICE_RULES = frozenset({"office_found", "office_not_found", "reviews_mention_fees"})
# Correcting a claim sets aside the signals that read it. Every search reads the company. The
# fee rules read only whether a fee is asked, never its amount, so a new amount stales nothing.
STALE_WHEN_CORRECTED = {
    "company": _SEARCH_RULES,
    "role": _JOB_RULES,
    "city": _JOB_RULES | _OFFICE_RULES,
    "pay": frozenset({"pay_outlier"}),
    "fee": frozenset(
        {
            "fee_requested",
            "fee_contradicts_employer",
            "employer_fraud_notice_exists",
            "reviews_mention_fees",
        }
    ),
}


# ---- handlers that only touch the store ---------------------------------------------------


def changeable(store: Store, case_id: str) -> dict[str, Any]:
    """The case, for a call that would change its claims or its findings; refused while its
    verdict is on the Offer Board."""
    case = store.case(case_id)
    if case["publishedVerdict"] is not None:
        raise Refused(
            f"{case_id}'s verdict is on the Offer Board, and the case stays as it was "
            "published: a person retracts the verdict first, then the case can be corrected or "
            "checked again"
        )
    return case


def _open_case(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    text = args["text"]
    return call.store.add_case(
        text, extract_claims(text), (text_signal(hit, None) for hit in text_rules(text))
    )


def _corrected(field: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    claim: dict[str, Any] = {"span": None, "confirmed": False, "corrected": True}
    if field == "pay":
        return {"raw": f"₹{value}/month", "monthlyInr": value, **claim}
    if field == "fee":
        return {"raw": f"₹{value}", "amountInr": value, **claim}
    return {"value": value, **claim}


def _claim_value(field: str, claim: dict[str, Any] | None) -> Any:
    if claim is None:
        return None
    return claim[{"pay": "monthlyInr", "fee": "amountInr"}.get(field, "value")]


def _update_claims(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    case = changeable(call.store, args["case_id"])
    fields = args.get("fields", {})
    confirm = args.get("confirm", False)
    if not fields and not confirm:
        raise Refused("nothing to update: give fields to correct, confirm: true, or both")
    claims = case["claims"]
    changed = [f for f, v in fields.items() if v != _claim_value(f, claims[f])]
    stale_rules = frozenset().union(
        *(
            STALE_WHEN_CORRECTED[f]
            for f in changed
            if f != "fee" or (claims[f] is None) != (fields[f] is None)
        )
    )
    for field in changed:
        claims[field] = _corrected(field, fields[field])
    staled = []
    for signal in case["signals"]:
        if signal["rule"] in stale_rules and not signal["stale"] and not signal.get("superseded"):
            signal["stale"] = True
            staled.append(signal["id"])
    if confirm:
        for field in CLAIM_FIELDS:
            claim = claims[field]
            # Who confirmed is kept, so the page never says a person checked what the agent
            # confirmed. A person's confirmation stands when the agent confirms again.
            if claim is not None and (not claim["confirmed"] or call.actor == "human"):
                claim["confirmed"] = True
                claim["confirmedBy"] = call.actor
    if changed:
        case["revision"] += 1
        call.store.refresh_fingerprint(case)
    return {"claims": claims, "corrected": changed, "staleSignals": staled}


def _get_case(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    return call.store.case(args["case_id"])


def _list_cases(call: Call, args: dict[str, Any]) -> list[dict[str, Any]]:
    status = args.get("status")
    return [c for c in call.store.cases.values() if status is None or c["status"] == status]


def _search_budget(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    provider = call.store.provider
    return {"provider": provider.name, "account": provider.account()}


# ---- handlers that run the planner or the drafts ------------------------------------------


def runner(store: Store, actor: str) -> planner.Runner:
    """The planner's way to run a check: back through ``invoke`` as ``actor``, so every search
    it makes is a tool call like any other, checked, logged and attributed. The check writes
    its trace line on the case itself, and the planner adds its "because" to that line. A
    search that failed comes back as an error with its ``failed`` line already on the trace,
    which is how the planner learns of it; only a refusal stops the plan here."""
    # Imported here: invoke imports this module.
    from offer_checkpost.invoke import invoke

    def run(tool: str, args: dict[str, Any]) -> None:
        out = invoke(tool, args, actor, store=store, by_planner=True)
        if out.get("outcome") == "refused":
            raise Refused(f"{tool} could not run: {out['error']}")

    return run


def _investigate(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    store = call.store
    case = changeable(store, args["case_id"])
    max_searches = args.get("max_searches", store.max_searches)
    if max_searches > store.max_searches:
        raise Refused(
            f"max_searches {max_searches} is above this app's per-case budget of "
            f"{store.max_searches} searches"
        )
    # Looked up now: an earlier copy may have been investigated since this one was opened.
    earlier = store.earlier_copy(case)
    result = planner.investigate(
        case,
        store.provider,
        max_searches=max_searches,
        same_as=earlier,
        # The planner's own checks are the agent's, even when a person pressed Investigate.
        runner=runner(store, "agent"),
        environ=store.planner_settings,
    )
    case["sameAs"] = earlier["id"] if earlier else None
    case["revision"] += 1
    # A case taken off the board keeps saying so.
    if case["status"] == "open":
        case["status"] = "investigated"
    return result


def _search_check(tool: str) -> Handler:
    def handler(call: Call, args: dict[str, Any]) -> dict[str, Any]:
        store = call.store
        case = changeable(store, args["case_id"])
        if call.by_planner:
            step = planner.run_check(case, tool, args, provider=store.provider, actor=call.actor)
        else:
            step = planner.check_directly(
                case,
                tool,
                args,
                provider=store.provider,
                actor=call.actor,
                reserve=store.quota_reserve,
            )
        case["revision"] += 1
        added = set(step["signalsAdded"])
        return {"step": step, "signals": [s for s in case["signals"] if s["id"] in added]}

    return handler


def _draft(field: str, write: Callable[..., dict[str, Any]]) -> Handler:
    def handler(call: Call, args: dict[str, Any]) -> dict[str, Any]:
        case = call.store.case(args["case_id"])
        # The drafts get a copy, so the one field a draft tool writes is this one.
        case[field] = write(copy.deepcopy(case), drafted_at=call.store.now())
        return case[field]

    return handler


# ---- the registry -------------------------------------------------------------------------

_CASE_ONLY = obj({"case_id": CASE_ID}, "case_id")

_CHECK_LIMITS = (
    " It does NOT run (it refuses, spending nothing) on claims not yet confirmed, on a case "
    "whose verdict is on the Offer Board, past the case's search budget or the monthly quota "
    "reserve, or, for the agent, once the evidence is already decisive: only a person spends "
    "searches that can't change the band. Run again, it replaces its earlier finding instead "
    "of adding a second one."
)

_TOOLS = (
    Tool(
        "open_case",
        "Opens a new case from a job-offer message a student forwarded, pasted as it arrived "
        "on WhatsApp, Telegram or email. Keeps the text in this app's memory; extracts the "
        "checkable claims (company, role, city, monthly pay, any fee asked, and the "
        "recruiter's phone numbers, emails and links), each with the exact character span of "
        "the message it came from; runs the four text rules (a fee asked with an amount, a "
        "task-scam pattern, identity or bank documents asked for before any interview, a "
        "chat-only interview); and fingerprints the claims so the same message forwarded "
        "again is recognised. Returns the new case with its id (case_001, case_002, ...), "
        "which every other tool takes. Claims start unconfirmed: check them, then call "
        "update_claims with confirm: true before investigate. Does NOT search the web, spend "
        "a search, contact anyone, or keep the text anywhere but this app's memory.",
        obj(
            {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 20000,
                    "description": "The offer message exactly as received.",
                }
            },
            "text",
        ),
        _open_case,
    ),
    Tool(
        "update_claims",
        "Corrects or confirms the claims extracted from a case's message. `fields` replaces "
        "a claim: company, role or city as text, pay as rupees a month, fee as rupees; null "
        "removes a claim the extraction got wrong. Signals that relied on a corrected claim "
        "are marked stale, so the verdict sets them aside, and the case's fingerprint is "
        "recomputed; a new fee amount stales nothing, since the rules read only whether a fee "
        "is asked. A corrected claim is unconfirmed again. `confirm: true` marks every "
        "claim the case has as confirmed, which investigate requires, and records who "
        "confirmed it (confirmedBy): the app shows the person which claims the agent "
        "confirmed, and the agent confirming again never replaces a person's confirmation. "
        "Returns the claims, the corrected fields and the ids of the signals made stale. Does "
        "NOT delete, re-fire or re-score any signal (investigate again for fresh ones), change "
        "the recruiter's contacts or links, or touch the drafts, the Offer Board or the case's "
        "status. It refuses a case whose verdict is on the Offer Board (a person retracts it "
        "first), and a field other than company, role, city, pay and fee; each refusal is "
        "logged.",
        obj(
            {
                "case_id": CASE_ID,
                "fields": {
                    "type": "object",
                    "properties": {
                        "company": {"type": ["string", "null"], "minLength": 1, "maxLength": 200},
                        "role": {"type": ["string", "null"], "minLength": 1, "maxLength": 200},
                        "city": {"type": ["string", "null"], "minLength": 1, "maxLength": 100},
                        "pay": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "description": "The offered pay in rupees a month.",
                        },
                        "fee": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "description": "The fee asked of the candidate, in rupees.",
                        },
                    },
                    "additionalProperties": False,
                    "minProperties": 1,
                },
                "confirm": {"type": "boolean", "enum": [True]},
            },
            "case_id",
        ),
        _update_claims,
    ),
    Tool(
        "lookup_official_site",
        "Spends one `google` search on the claimed company and finds its official domain: the "
        "knowledge graph's website, or else the top result whose domain is the company's "
        "name. Classifies each recruiter email and link domain in the message as official, a "
        "subdomain of it, a look-alike candidate or free mail, firing sender_official, "
        "sender_lookalike (moderate), sender_free_mail or, when nothing about the company "
        "turns up, no_web_footprint. Adds a step to the case's trace and returns it with the "
        "signals it added. Does NOT treat the official site as proof the offer is genuine, "
        "open or fetch any website, or rate a look-alike above moderate (only "
        "confirm_sender_domain can do that)." + _CHECK_LIMITS,
        _CASE_ONLY,
        _search_check("lookup_official_site"),
    ),
    Tool(
        "find_fraud_notice",
        "Spends one `google` search limited to the employer's official domain (site:<domain>) "
        "with recruitment-fraud terms, and quotes a notice found there, saying whether its "
        "title or snippet carries a fee phrase such as 'never charge' or 'no fee'. When the "
        "message asked for a fee, a notice with a fee phrase fires fee_contradicts_employer "
        "(strong) and one without fires employer_fraud_notice_exists (weak). Adds a trace "
        "step and returns it with the signals it added. Does NOT run before "
        "lookup_official_site has found an official domain (it refuses), or read any page "
        "outside the official domain as the employer's own statement." + _CHECK_LIMITS,
        _CASE_ONLY,
        _search_check("find_fraud_notice"),
    ),
    Tool(
        "confirm_sender_domain",
        'Spends one `google` search, site:<official domain> "<domain>", for one recruiter '
        "domain that lookup_official_site called a look-alike candidate. If the official site "
        "names that domain next to a fraud term, fires domain_named_in_fraud_notice (strong); "
        "if it mentions the domain otherwise, the domain is the employer's own second domain "
        "and sender_lookalike is withdrawn; with no mention, sender_lookalike stays moderate. "
        "Adds a trace step and returns it with the signals it added. Does NOT open either "
        "site, run before an official domain is known, or check a domain that is not in the "
        "offer's own recruiter emails and links (it refuses)." + _CHECK_LIMITS,
        obj(
            {
                "case_id": CASE_ID,
                "domain": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 253,
                    "pattern": r"^[A-Za-z0-9.-]+$",
                    "description": "A recruiter domain from this offer, e.g. brnad.example.",
                },
            },
            "case_id",
            "domain",
        ),
        _search_check("confirm_sender_domain"),
    ),
    Tool(
        "check_job_listings",
        "Spends one `google_jobs` search for the claimed role in the claimed city. Looks for a "
        "listing by this company for this role with an apply option on the official domain "
        "(listing_match, green; none found is no_listing_match, weak), and benchmarks the "
        "offered pay against comparable listings that show pay, reporting 'pay found in n of "
        "m listings' and firing pay_outlier (moderate) when the offer is more than 2x the "
        "median of at least 3. Adds a trace step and returns it with the signals it added. "
        "Does NOT apply to any job, or treat a missing listing as more than weak evidence."
        + _CHECK_LIMITS,
        _CASE_ONLY,
        _search_check("check_job_listings"),
    ),
    Tool(
        "check_office",
        "Spends one `google_maps` search for the company in the claimed city, and reports "
        "whether a matching place exists (office_found, green; office_not_found, moderate) "
        "with its type, rating, review count and place_id. Adds a trace step and returns it "
        "with the signals it added. Does NOT judge a business by its rating, visit or call "
        "it, or read its reviews (scan_office_reviews does that)." + _CHECK_LIMITS,
        _CASE_ONLY,
        _search_check("check_office"),
    ),
    Tool(
        "scan_office_reviews",
        "Spends one `google_maps_reviews` search at the office check_office matched, keeping "
        "only reviews that contain `term` ('fee' or 'fraud'). Quotes each matching review's "
        "text, rating, date and link, and fires reviews_mention_fees (moderate) when one "
        "matches. Adds a trace step and returns it with the signals it added. Does NOT run "
        "without a place_id from check_office (it refuses), fetch or show any reviewer's name "
        "or profile, or turn a review into an accusation against anyone." + _CHECK_LIMITS,
        obj(
            {
                "case_id": CASE_ID,
                "term": {
                    "type": "string",
                    "enum": ["fee", "fraud"],
                    "description": "'fee' when the message asked for a fee, else 'fraud'.",
                },
            },
            "case_id",
            "term",
        ),
        _search_check("scan_office_reviews"),
    ),
    Tool(
        "check_scam_reports",
        "Spends one `google_news` search for news of fake job offers made in the company's "
        "name, and fires impersonation_reports (weak) when a headline names the company and "
        "reports fake offers. Adds a trace step and returns it with the signals it added. "
        "Does NOT conclude that this offer is fake from reports about others: large "
        "employers are impersonated constantly, so the signal stays weak." + _CHECK_LIMITS,
        _CASE_ONLY,
        _search_check("check_scam_reports"),
    ),
    Tool(
        "check_contact_footprint",
        "Spends one `google` exact-phrase search for one recruiter-supplied phone number or "
        "email (contact_index counts from 0 through the case's claims.contacts) next to scam "
        "and fraud terms, and fires contact_reported (strong) when a page carries both. Adds "
        "a trace step and returns it with the signals it added. Does NOT search a contact "
        "marked synthetic (it refuses), anything not extracted from the offer, or the "
        "candidate's own details, and never calls, messages or emails the contact."
        + _CHECK_LIMITS,
        obj(
            {
                "case_id": CASE_ID,
                "contact_index": {"type": "integer", "minimum": 0},
            },
            "case_id",
            "contact_index",
        ),
        _search_check("check_contact_footprint"),
    ),
    Tool(
        "investigate",
        "Investigates a case whose claims are confirmed. Runs the text rules, then "
        "lookup_official_site, then only the checks earlier results make worthwhile, in an "
        "order chosen from those results; it stops as soon as the evidence is decisive, the "
        "search budget is spent, or the monthly quota guard trips. A case whose message was "
        "already investigated (sameAs) reuses that case's findings at 0 searches. "
        "Investigating the same case again starts afresh with a fresh budget: its earlier "
        "findings are set aside and the checks run again. Returns the "
        "band the evidence supports so far, the trace (each step with why it ran or was "
        "skipped), the signals, and the budget with the searches saved. Does NOT run on "
        "unconfirmed claims or on a case whose verdict is on the Offer Board (it refuses), "
        "spend more than max_searches (default and "
        "ceiling: the app's per-case budget, normally 6), search "
        "past a decisive result, fall back to made-up data when the quota guard trips, or "
        "draft, publish or close anything.",
        obj(
            {"case_id": CASE_ID, "max_searches": {"type": "integer", "minimum": 1}},
            "case_id",
        ),
        _investigate,
    ),
    Tool(
        "draft_verdict",
        "Applies the decision table to the case's signals and writes the case's draftVerdict: "
        "the band (high_risk, consistent_with_genuine or unverified) and a summary citing the "
        "evidence behind it by signal id. Returns the draft. Does NOT publish anything, write "
        "any field but draftVerdict (a publishedVerdict or any other extra argument is "
        "refused, and the refusal logged), or ever call an offer 'genuine' or 'safe': the best "
        "band means only that nothing found contradicts the offer.",
        _CASE_ONLY,
        _draft("draftVerdict", drafts.verdict),
    ),
    Tool(
        "draft_recruiter_reply",
        "Writes the case's draftReply: a message the person may send the recruiter "
        "themselves, with one verification question per red flag (for example 'please resend "
        "this from your @<official domain> address'). Returns the draft. Does NOT send it: "
        "the app has no messaging integration and never contacts a recruiter.",
        _CASE_ONLY,
        _draft("draftReply", drafts.recruiter_reply),
    ),
    Tool(
        "draft_cybercrime_report",
        "Writes the case's draftReport: a summary for the national cybercrime helpline 1930 "
        "and cybercrime.gov.in covering what was asked for, when, which recruiter contacts "
        "were used and which evidence was found. Returns the draft. Does NOT file or submit "
        "it, or contact any portal, helpline or authority; the person does that if they "
        "choose.",
        _CASE_ONLY,
        _draft("draftReport", drafts.cybercrime_report),
    ),
    Tool(
        "get_case",
        "Returns one case in full: the pasted message, the claims with their spans, the "
        "signals with their quoted evidence, the trace, the search budget, the drafts, any "
        "published verdict and recorded outcome, and the status. Does NOT change anything.",
        _CASE_ONLY,
        _get_case,
    ),
    Tool(
        "list_cases",
        "Returns every case, oldest first, or only the cases with the given status: open "
        "(not investigated yet), investigated, published (on the Offer Board) or retracted. "
        "Does NOT change anything.",
        obj({"status": {"type": "string", "enum": list(STATUSES)}}),
        _list_cases,
    ),
    Tool(
        "search_budget",
        "Reads SerpApi's free Account API and returns which provider serves searches (live, "
        "replay or fake) and only these counts: plan_searches_left, searches_per_month, "
        "this_month_usage, this_hour_searches and account_rate_limit_per_hour. In replay mode "
        "no account is behind the recorded responses and account is null. Does NOT spend a "
        "search, or return the API key, the account email or any other field the Account "
        "API sends.",
        obj({}),
        _search_budget,
    ),
)

TOOLS: dict[str, Tool] = {tool.name: tool for tool in _TOOLS}


def listing() -> list[dict[str, Any]]:
    """The agent's tool list, as ``GET /api/tools`` and an MCP ``tools/list`` serve it."""
    return [tool.listing() for tool in _TOOLS]
