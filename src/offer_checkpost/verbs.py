"""The human-only verbs: publishing a verdict, retracting it, recording what the person
decided, and spending searches past a decisive result.

None of these is ever registered as a tool, so no agent's tool list shows them, and ``invoke``
refuses each one for any actor but ``"human"`` before its handler runs. The agent drafts; a
person decides and publishes.

A person publishes the case as they read it: ``publish_verdict`` names the case ``revision``
the page showed, and is refused when anything has changed since (a correction, or a check by
anyone), or when the draft verdict is older than the case. What goes on the board is then a
snapshot of that draft's claims and evidence, which nothing done to the case afterwards can
rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from offer_checkpost import drafts, planner
from offer_checkpost.rules import TEXT_SOURCE
from offer_checkpost.store import OUTCOMES, Refused
from offer_checkpost.tools import CASE_ID, Call, Handler, changeable, obj, runner


@dataclass(frozen=True)
class Verb:
    name: str
    does: str  # what only a person may do, completing "only a person can ..."
    schema: dict[str, Any]
    handler: Handler


def search_evidence(case: dict[str, Any]) -> list[dict[str, Any]]:
    """The case's signals that quote a search result and still stand: not stale, and not
    superseded by a later run of the same check or a later investigation."""
    return [
        s
        for s in case["signals"]
        if s["source"] != TEXT_SOURCE and not s["stale"] and not s.get("superseded")
    ]


def _no_search_evidence(case: dict[str, Any]) -> str:
    """Why ``case`` has no search evidence to publish, and what, if anything, would give it
    some."""
    none = f"{case['id']} has no evidence from a search result, only the message's own text"
    if not case["trace"]:
        return f"{none}; investigate it before publishing a verdict"
    stopped = case["budget"]["stoppedBecause"]
    if stopped == "no_company":
        return (
            f"{none}: the message names no company, so there is nothing a search can check, and "
            "a verdict on the text alone does not go on the Offer Board. Its recruiter reply and "
            "1930 summary can still be drafted"
        )
    if stopped == "search_error":
        return f"{none}: a search failed and nothing was made up; investigate it again to retry"
    return (
        f"{none}: a claim was corrected, so what the searches found is set aside; confirm the "
        "claims and investigate it again"
    )


def _publish_verdict(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    store = call.store
    case = store.case(args["case_id"])
    if case["publishedVerdict"] is not None:
        raise Refused(
            f"{case['id']} is already on the Offer Board; retract it before publishing again"
        )
    if not search_evidence(case):
        raise Refused(_no_search_evidence(case))
    if args["revision"] != case["revision"]:
        raise Refused(
            f"{case['id']} has changed since it was read (a claim was corrected, or a check "
            "ran): read it again, then publish"
        )
    draft = case["draftVerdict"]
    if draft is None:
        raise Refused(f"{case['id']} has no draft verdict: draft it, read it, then publish")
    if drafts.verdict(case, drafted_at=draft["draftedAt"]) != draft:
        raise Refused(
            f"{case['id']}'s draft verdict is older than the case (a claim was corrected, or a "
            "check ran, after it): draft the verdict again, read it, then publish"
        )
    if misfit := drafts.label_misfit(args["label"], draft["band"]):
        raise Refused(misfit)
    post = drafts.post(
        case,
        label=args["label"],
        note=args["note"],
        published_at=store.now(),
        by=call.actor,
    )
    store.board.append(post)
    case["publishedVerdict"] = post
    case["status"] = "published"
    return post


def _retract_verdict(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    store = call.store
    case = store.case(args["case_id"])
    post = case["publishedVerdict"]
    if post is None:
        raise Refused(f"{case['id']} has no verdict on the Offer Board to retract")
    store.board[:] = [p for p in store.board if p is not post]
    case["publishedVerdict"] = None
    case["status"] = "retracted"
    return {
        "caseId": case["id"],
        "label": post["label"],
        "reason": args["reason"],
        "retractedAt": store.now(),
        "by": call.actor,
    }


def _record_outcome(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    case = call.store.case(args["case_id"])
    case["outcome"] = {"value": args["outcome"], "recordedAt": call.store.now(), "by": call.actor}
    return case["outcome"]


def _run_remaining_checks(call: Call, args: dict[str, Any]) -> dict[str, Any]:
    store = call.store
    case = changeable(store, args["case_id"])
    result = planner.run_remaining_checks(
        case,
        store.provider,
        actor=call.actor,
        # Searches a person asked for are the person's, in the trace and the activity log.
        runner=runner(store, call.actor),
        environ=store.planner_settings,
    )
    case["revision"] += 1
    return result


_VERBS = (
    Verb(
        "publish_verdict",
        "publish a verdict to the Offer Board",
        obj(
            {
                "case_id": CASE_ID,
                "label": {"type": "string", "enum": list(drafts.BOARD_LABELS)},
                "note": {"type": "string", "maxLength": 1000},
                "revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "The case's revision as the person read it.",
                },
            },
            "case_id",
            "label",
            "note",
            "revision",
        ),
        _publish_verdict,
    ),
    Verb(
        "retract_verdict",
        "retract a verdict from the Offer Board",
        # Short enough that the activity log keeps the reason word for word.
        obj(
            {"case_id": CASE_ID, "reason": {"type": "string", "minLength": 1, "maxLength": 200}},
            "case_id",
            "reason",
        ),
        _retract_verdict,
    ),
    Verb(
        "record_outcome",
        "record what they decided about an offer",
        obj(
            {"case_id": CASE_ID, "outcome": {"type": "string", "enum": list(OUTCOMES)}},
            "case_id",
            "outcome",
        ),
        _record_outcome,
    ),
    Verb(
        "run_remaining_checks",
        "spend searches after the evidence is already decisive",
        obj({"case_id": CASE_ID}, "case_id"),
        _run_remaining_checks,
    ),
)

VERBS: dict[str, Verb] = {verb.name: verb for verb in _VERBS}
