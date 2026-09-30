"""The gate: one ``invoke(tool, args, actor)`` for everything, and human-only verbs no agent
can reach.

Most tests replace the planner with a small fake that acts on a case the way the real one
does (a check adds a trace line and a signal; an investigation runs its checks through the
runner it is given), so they hold the gate itself: routing, argument schemas, actor checks,
refusals that change nothing, and the activity log. The drafts are the real ones. The last
section runs the real planner on Samples A, B and C against the synthetic fixtures, through
the same ``invoke``.
"""

import copy
import hashlib
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from offer_checkpost import planner, tools, verbs
from offer_checkpost.drafts import BOARD_LABELS
from offer_checkpost.invoke import invoke, validate
from offer_checkpost.providers import FAKE_ACCOUNT, FakeSearchProvider, SearchError
from offer_checkpost.rules import RULES, TEXT_SOURCE, signal_for
from offer_checkpost.store import IST, OUTCOMES, STATUSES, Store, add_signals
from offer_checkpost.tools import TOOLS, listing
from offer_checkpost.verbs import VERBS

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "offers"
# The synthetic stand-ins of the demo samples, which the fake provider's fixtures answer; any
# other sample is read from samples/offers.
STAND_INS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "offers"
NOW = datetime(2026, 10, 3, 10, 2, 11, tzinfo=IST)
LABELS = list(BOARD_LABELS)

# The agent's tools, in the README's order, with their arguments: required, then optional.
AGENT_TOOLS = {
    "open_case": ({"text"}, set()),
    "update_claims": ({"case_id"}, {"fields", "confirm"}),
    "lookup_official_site": ({"case_id"}, set()),
    "find_fraud_notice": ({"case_id"}, {"wording"}),
    "confirm_sender_domain": ({"case_id", "domain"}, set()),
    "check_job_listings": ({"case_id"}, set()),
    "check_office": ({"case_id"}, set()),
    "scan_office_reviews": ({"case_id", "term"}, set()),
    "check_scam_reports": ({"case_id"}, set()),
    "check_contact_footprint": ({"case_id", "contact_index"}, set()),
    "investigate": ({"case_id"}, {"max_searches"}),
    "draft_verdict": ({"case_id"}, set()),
    "draft_recruiter_reply": ({"case_id"}, set()),
    "draft_cybercrime_report": ({"case_id"}, set()),
    "get_case": ({"case_id"}, set()),
    "list_cases": (set(), {"status"}),
    "search_budget": (set(), set()),
}
# The human-only verbs and their arguments.
HUMAN_VERBS = {
    "publish_verdict": {"case_id", "label", "note", "revision"},
    "retract_verdict": {"case_id", "reason"},
    "record_outcome": {"case_id", "outcome"},
    "run_remaining_checks": {"case_id"},
}

# The one rule each fake check fires, as a real reader might for Sample C.
FINDS = {
    "lookup_official_site": "no_web_footprint",
    "find_fraud_notice": "employer_fraud_notice_exists",
    "confirm_sender_domain": "domain_named_in_fraud_notice",
    "check_job_listings": "no_listing_match",
    "check_office": "office_not_found",
    "scan_office_reviews": "reviews_mention_fees",
    "check_scam_reports": "impersonation_reports",
    "check_contact_footprint": "contact_reported",
}


class FakePlanner:
    """Stands in for ``planner`` with the same call signatures."""

    def __init__(self):
        self.calls = []

    def investigate(self, case, provider, *, max_searches, same_as, runner, environ):
        self.calls.append(("investigate", case["id"], max_searches, environ))
        for tool in ("lookup_official_site", "check_office"):
            runner(tool, {"case_id": case["id"]})
            case["trace"][-1]["because"] = f"the fake plan runs {tool}"
        case["budget"]["stoppedBecause"] = "decisive"
        case["status"] = "investigated"
        return {"caseId": case["id"], "budget": case["budget"], "trace": case["trace"]}

    def run_check(self, case, tool, args, *, provider, actor):
        self.calls.append(("run_check", case["id"], tool, actor))
        rule = RULES[FINDS[tool]]
        evidence = {
            "engine": rule.engine,
            "query": f"fake {tool}",
            "title": None,
            "link": None,
            "snippet": None,
            "date": None,
            "retrievedAt": NOW.isoformat(),
        }
        # A rule that can replace a look-alike names the domain it speaks for.
        domain = {"domain": args["domain"]} if rule.replaces else {}
        signal = signal_for(
            rule.name, None, evidence, detail=f"the fake {tool} found it", **domain
        )
        added = add_signals(case, [signal])
        line = dict.fromkeys(planner.TRACE_KEYS) | {
            "step": len(case["trace"]) + 1,
            "tool": tool,
            "args": dict(args),
            "actor": actor,
            "action": "ran",
            "because": f"called directly by the {actor}",
            "engine": rule.engine,
            "searchesSpent": 1,
            "searchesSaved": 0,
            "signalsAdded": [s["id"] for s in added],
        }
        case["trace"].append(line)
        case["budget"]["spent"] += 1
        return line

    def check_directly(self, case, tool, args, *, provider, actor, reserve):
        # The stop rules a check called on its own keeps are the real planner's, and the last
        # section tests them with it.
        return self.run_check(case, tool, args, provider=provider, actor=actor)

    def run_remaining_checks(self, case, provider, *, actor, runner, environ):
        self.calls.append(("run_remaining_checks", case["id"], actor))
        runner("check_scam_reports", {"case_id": case["id"]})
        case["trace"][-1]["because"] = planner.ON_REQUEST
        return {"caseId": case["id"], "budget": case["budget"], "trace": case["trace"]}


@pytest.fixture
def fake_planner(monkeypatch):
    fake = FakePlanner()
    monkeypatch.setattr(tools, "planner", fake)
    monkeypatch.setattr(verbs, "planner", fake)
    return fake


@pytest.fixture
def store(fake_planner):
    return Store(FakeSearchProvider(), clock=lambda: NOW)


def sample(name):
    stand_in = STAND_INS / f"{name}.txt"
    return (stand_in if stand_in.exists() else SAMPLES / f"{name}.txt").read_text(encoding="utf-8")


def ok(store, tool, args, actor="human"):
    out = invoke(tool, args, actor, store=store)
    assert out["ok"], out
    return out["result"]


def refused(store, tool, args, actor="human"):
    out = invoke(tool, args, actor, store=store)
    assert out == {"ok": False, "outcome": "refused", "error": out["error"]}, out
    return out["error"]


def without_log(store):
    state = store.state()
    del state["activityLog"]
    return state


def investigated(store, name="c"):
    case_id = ok(store, "open_case", {"text": sample(name)})["id"]
    ok(store, "update_claims", {"case_id": case_id, "confirm": True})
    ok(store, "investigate", {"case_id": case_id})
    return case_id


def publish_args(store, case_id, label=LABELS[0], note=""):
    """Publishing the case as it stands now, the way a person who has just read it would."""
    revision = store.cases[case_id]["revision"] if case_id in store.cases else 0
    return {"case_id": case_id, "label": label, "note": note, "revision": revision}


def published(store, case_id, label=LABELS[0], note=""):
    """Drafts the verdict, then publishes it as the person who read it."""
    ok(store, "draft_verdict", {"case_id": case_id})
    return ok(store, "publish_verdict", publish_args(store, case_id, label, note))


@pytest.fixture
def ready(store):
    """case_001 stopped on a decisive result; case_002 is on the Offer Board."""
    decisive = investigated(store)
    on_board = investigated(store, "a")
    published(store, on_board)
    return SimpleNamespace(store=store, decisive=decisive, published=on_board)


def verb_args(verb, ready):
    return {
        "publish_verdict": publish_args(ready.store, ready.decisive, note="n"),
        "retract_verdict": {"case_id": ready.published, "reason": "posted to the wrong batch"},
        "record_outcome": {"case_id": ready.decisive, "outcome": OUTCOMES[0]},
        "run_remaining_checks": {"case_id": ready.decisive},
    }[verb]


# ---- the registry ------------------------------------------------------------------------


def test_the_registry_is_exactly_the_agent_tools_in_order():
    assert list(TOOLS) == list(AGENT_TOOLS)
    for name, (required, optional) in AGENT_TOOLS.items():
        schema = TOOLS[name].schema
        assert set(schema["required"]) == required, name
        assert set(schema["properties"]) == required | optional, name


def test_the_verbs_take_their_arguments():
    assert set(VERBS) == set(HUMAN_VERBS)
    for name, args in HUMAN_VERBS.items():
        assert set(VERBS[name].schema["properties"]) == args
        assert set(VERBS[name].schema["required"]) == args


def test_no_human_verb_is_ever_registered_as_a_tool():
    listed = [tool["name"] for tool in listing()]
    assert listed == list(TOOLS)
    assert not set(VERBS) & set(TOOLS)
    assert not set(VERBS) & set(listed)
    for tool in listing():
        assert set(tool) == {"name", "description", "inputSchema"}
        assert not any(verb in tool["description"] for verb in VERBS)


@pytest.mark.parametrize("name", list(AGENT_TOOLS))
def test_every_description_says_what_the_tool_does_not_do(name):
    description = TOOLS[name].description
    assert "Does NOT" in description
    assert len(description) > 150


def _objects(schema):
    if schema.get("type") == "object":
        yield schema
    for sub in schema.get("properties", {}).values():
        yield from _objects(sub)


@pytest.mark.parametrize("name", [*AGENT_TOOLS, *HUMAN_VERBS])
def test_every_schema_refuses_fields_it_does_not_name(name):
    schema = (TOOLS.get(name) or VERBS[name]).schema
    objects = list(_objects(schema))
    assert objects
    assert all(o["additionalProperties"] is False for o in objects)


def test_the_publish_labels_are_the_board_labels():
    assert VERBS["publish_verdict"].schema["properties"]["label"]["enum"] == [
        "likely_impersonation",
        "pay_to_apply_red_flag",
        "unverified_ask_questions",
        "no_contradictions_found",
    ]


# ---- human-only verbs --------------------------------------------------------------------


@pytest.mark.parametrize("verb", list(HUMAN_VERBS))
def test_each_human_verb_is_refused_for_the_agent_and_changes_nothing(
    verb, ready, fake_planner, store
):
    before = without_log(store)
    planner_calls = len(fake_planner.calls)

    error = refused(store, verb, verb_args(verb, ready), actor="agent")

    assert error.startswith(f"{verb} is human-only: only a person can {VERBS[verb].does}.")
    assert "nothing was changed" in error
    assert without_log(store) == before
    assert len(fake_planner.calls) == planner_calls
    assert store.activity_log[-1] == {
        "ts": NOW.isoformat(),
        "actor": "agent",
        "tool": verb,
        "args": verb_args(verb, ready),
        "result": "refused",
        "reason": error,
    }


@pytest.mark.parametrize("verb", list(HUMAN_VERBS))
def test_the_same_verb_call_succeeds_for_a_person(verb, ready, store):
    if verb == "publish_verdict":
        ok(store, "draft_verdict", {"case_id": ready.decisive})
    ok(store, verb, verb_args(verb, ready))
    assert store.activity_log[-1]["actor"] == "human"
    assert store.activity_log[-1]["result"] == "ok"


@pytest.mark.parametrize("verb", list(HUMAN_VERBS))
def test_a_verb_refuses_the_agent_before_reading_its_arguments(verb, store):
    error = refused(store, verb, {"case_id": "case_999", "extra": 1}, actor="agent")
    assert error.startswith(f"{verb} is human-only")


@pytest.mark.parametrize(
    "actor", [None, "", "Human", "HUMAN", "human ", "system", "admin", 1, ["human"]]
)
@pytest.mark.parametrize("tool", ["open_case", "publish_verdict", "get_case"])
def test_a_missing_or_forged_actor_is_refused(tool, actor, ready, store):
    before = without_log(store)
    args = {
        "open_case": {"text": "hello"},
        "publish_verdict": publish_args(store, ready.decisive),
        "get_case": {"case_id": ready.decisive},
    }[tool]

    error = refused(store, tool, args, actor=actor)

    assert error.startswith("unknown actor")
    assert without_log(store) == before
    assert store.activity_log[-1]["result"] == "refused"


# ---- unknown tools and bad arguments ------------------------------------------------------


@pytest.mark.parametrize("actor", ["human", "agent"])
@pytest.mark.parametrize("tool", ["delete_case", "send_reply", "publish", "", None, ["get_case"]])
def test_an_unknown_tool_is_refused_and_logged(tool, actor, ready, store):
    before = without_log(store)
    error = refused(store, tool, {"case_id": ready.decisive}, actor=actor)
    assert error.startswith("unknown tool")
    assert without_log(store) == before
    entry = store.activity_log[-1]
    assert (entry["actor"], entry["result"]) == (actor, "refused")


BAD_ARGS = [
    ("open_case", {"text": "x", "actor": "human"}, "unknown field actor"),
    ("open_case", {}, "missing text"),
    ("open_case", {"text": ""}, "args.text must not be empty"),
    ("open_case", {"text": 5}, "args.text must be text"),
    ("get_case", None, "args must be an object"),
    ("get_case", ["case_001"], "args must be an object"),
    ("get_case", {"case_id": "case_1"}, "args.case_id is not in the expected form"),
    ("get_case", {"case_id": "case_001; drop"}, "args.case_id is not in the expected form"),
    ("draft_verdict", {"case_id": "case_001", "publishedVerdict": {}}, "publishedVerdict"),
    ("draft_verdict", {"case_id": "case_001", "status": "published"}, "unknown field status"),
    ("update_claims", {"case_id": "case_001", "fields": {"status": "x"}}, "unknown field"),
    ("update_claims", {"case_id": "case_001", "draftVerdict": {}}, "unknown field"),
    ("update_claims", {"case_id": "case_001", "fields": {}}, "at least 1 field"),
    ("update_claims", {"case_id": "case_001", "fields": {"company": 5}}, "text or null"),
    ("update_claims", {"case_id": "case_001", "fields": {"pay": "40k"}}, "a whole number"),
    ("update_claims", {"case_id": "case_001", "fields": {"fee": 0}}, "at least 1"),
    ("update_claims", {"case_id": "case_001", "confirm": False}, "must be one of True"),
    ("update_claims", {"case_id": "case_001", "confirm": 1}, "true or false"),
    ("investigate", {"case_id": "case_001", "max_searches": True}, "a whole number"),
    ("investigate", {"case_id": "case_001", "max_searches": 0}, "at least 1"),
    ("investigate", {"case_id": "case_001", "max_searches": 2.0}, "a whole number"),
    ("scan_office_reviews", {"case_id": "case_001", "term": "salary"}, "one of 'fee', 'fraud'"),
    ("confirm_sender_domain", {"case_id": "case_001", "domain": "a b"}, "expected form"),
    ("check_contact_footprint", {"case_id": "case_001", "contact_index": -1}, "at least 0"),
    ("list_cases", {"status": "deleted"}, "one of"),
    ("search_budget", {"account_email": True}, "unknown field account_email"),
    ("publish_verdict", {"case_id": "case_001", "label": "genuine", "note": ""}, "one of"),
    ("publish_verdict", {"case_id": "case_001", "label": LABELS[0]}, "missing note"),
    ("publish_verdict", {"case_id": "case_001", "label": "safe", "note": 1, "by": "x"}, "by"),
    ("retract_verdict", {"case_id": "case_002", "reason": ""}, "must not be empty"),
    ("record_outcome", {"case_id": "case_001", "outcome": "paid_the_fee"}, "one of"),
]


@pytest.mark.parametrize(
    ("tool", "args", "says", "actor"),
    # A verb refuses the agent before it reads the arguments, so only a person gets this far.
    [(*bad, actor) for bad in BAD_ARGS for actor in ("human", "agent") if bad[0] in TOOLS]
    + [(*bad, "human") for bad in BAD_ARGS if bad[0] in VERBS],
)
def test_bad_arguments_are_refused_logged_and_change_nothing(
    tool, args, says, actor, ready, fake_planner, store
):
    before = without_log(store)
    planner_calls = len(fake_planner.calls)

    error = refused(store, tool, args, actor=actor)

    assert error.startswith(f"{tool} was refused: ")
    assert says in error
    assert without_log(store) == before
    assert len(fake_planner.calls) == planner_calls
    entry = store.activity_log[-1]
    assert (entry["actor"], entry["tool"], entry["result"]) == (actor, tool, "refused")
    assert entry["reason"] == error


def test_draft_verdict_refuses_a_smuggled_published_verdict_and_logs_it(ready, store):
    smuggled = {"caseId": ready.decisive, "label": "no_contradictions_found", "by": "agent"}
    error = refused(
        store,
        "draft_verdict",
        {"case_id": ready.decisive, "publishedVerdict": smuggled},
        actor="agent",
    )
    case = store.cases[ready.decisive]
    assert "unknown field publishedVerdict" in error
    assert case["publishedVerdict"] is None and case["draftVerdict"] is None
    assert [p["caseId"] for p in store.board] == [ready.published]
    assert store.activity_log[-1]["result"] == "refused"
    assert store.activity_log[-1]["tool"] == "draft_verdict"


def test_the_validator_reads_nested_fields_and_types():
    schema = TOOLS["update_claims"].schema
    assert validate(schema, {"case_id": "case_001", "fields": {"company": None}}) == []
    confirmed = {"case_id": "case_001", "fields": {"pay": 42000}, "confirm": True}
    assert validate(schema, confirmed) == []
    assert validate(schema, {"fields": {"city": 3, "zip": 1}}) == [
        "args is missing case_id",
        "args.fields has unknown field zip (allowed: company, role, city, pay, fee)",
        "args.fields.city must be text or null",
    ]


# ---- refusals inside a tool or a verb ------------------------------------------------------


def test_an_unknown_case_is_refused(store):
    error = refused(store, "get_case", {"case_id": "case_404"}, actor="agent")
    assert error == "there is no case case_404; list_cases shows the cases there are"
    assert store.activity_log[-1]["result"] == "refused"


def test_publish_refuses_a_case_with_no_search_sourced_evidence(store):
    case_id = ok(store, "open_case", {"text": sample("training-fee-bangalore")})["id"]
    assert [s["source"] for s in store.cases[case_id]["signals"]] == [TEXT_SOURCE]
    before = without_log(store)

    error = refused(store, "publish_verdict", publish_args(store, case_id, LABELS[1]))

    assert "no evidence from a search result" in error
    assert without_log(store) == before
    assert store.board == []


def test_publish_refuses_when_every_search_signal_is_stale(store):
    case_id = investigated(store)
    ok(store, "update_claims", {"case_id": case_id, "fields": {"company": "Another Firm"}})
    error = refused(store, "publish_verdict", publish_args(store, case_id))
    assert "no evidence from a search result" in error


def test_publishing_twice_and_retracting_nothing_are_refused(ready, store):
    error = refused(store, "publish_verdict", publish_args(store, ready.published, LABELS[2]))
    assert "already on the Offer Board" in error
    error = refused(store, "retract_verdict", {"case_id": ready.decisive, "reason": "r"})
    assert "no verdict on the Offer Board" in error


def test_investigate_refuses_a_budget_above_the_cap_and_passes_the_stores_settings(
    fake_planner,
):
    store = Store(FakeSearchProvider(), max_searches=4, quota_reserve=30, clock=lambda: NOW)
    case_id = ok(store, "open_case", {"text": sample("c")})["id"]
    ok(store, "update_claims", {"case_id": case_id, "confirm": True}, actor="agent")

    error = refused(store, "investigate", {"case_id": case_id, "max_searches": 5})
    assert "above this app's per-case budget of 4" in error
    assert fake_planner.calls == []
    assert store.cases[case_id]["status"] == "open"

    ok(store, "investigate", {"case_id": case_id, "max_searches": 2}, actor="agent")
    settings = {"OFFER_CHECKPOST_MAX_SEARCHES": "4", "OFFER_CHECKPOST_QUOTA_RESERVE": "30"}
    assert fake_planner.calls[0] == ("investigate", case_id, 2, settings)


def test_update_claims_needs_something_to_update(store):
    case_id = ok(store, "open_case", {"text": sample("c")})["id"]
    assert refused(store, "update_claims", {"case_id": case_id}).startswith("nothing to update")


def test_confirming_records_who_confirmed_and_a_persons_confirmation_stands(store):
    case_id = ok(store, "open_case", {"text": sample("c")})["id"]
    claims = store.cases[case_id]["claims"]
    fields = [f for f in tools.CLAIM_FIELDS if claims[f] is not None]

    def confirmed_by():
        return {f: claims[f].get("confirmedBy") for f in fields}

    ok(store, "update_claims", {"case_id": case_id, "confirm": True}, actor="agent")
    assert set(confirmed_by().values()) == {"agent"}
    ok(store, "update_claims", {"case_id": case_id, "confirm": True})
    assert set(confirmed_by().values()) == {"human"}
    ok(store, "update_claims", {"case_id": case_id, "confirm": True}, actor="agent")
    assert set(confirmed_by().values()) == {"human"}

    # A corrected claim is unconfirmed again, and whoever confirms it next is named.
    args = {"case_id": case_id, "fields": {"city": "Pune"}, "confirm": True}
    ok(store, "update_claims", args, actor="agent")
    assert confirmed_by() == {f: "agent" if f == "city" else "human" for f in fields}


# ---- what a person can do ----------------------------------------------------------------


def test_a_person_can_do_everything(fake_planner, store):
    case_id = ok(store, "open_case", {"text": sample("c")})["id"]
    assert case_id == "case_001"
    ok(store, "update_claims", {"case_id": case_id, "fields": {"city": "Pune"}, "confirm": True})
    ok(store, "investigate", {"case_id": case_id})
    ok(store, "run_remaining_checks", {"case_id": case_id})
    for tool, extra in [
        ("lookup_official_site", {}),
        ("find_fraud_notice", {}),
        ("confirm_sender_domain", {"domain": "zorvanta-support.example"}),
        ("check_job_listings", {}),
        ("check_office", {}),
        ("scan_office_reviews", {"term": "fee"}),
        ("check_scam_reports", {}),
        ("check_contact_footprint", {"contact_index": 1}),
    ]:
        result = ok(store, tool, {"case_id": case_id, **extra})
        assert (result["step"]["tool"], result["step"]["actor"]) == (tool, "human")
        assert result["step"]["because"] == "called directly by the human"
        assert [s["rule"] for s in result["signals"]] == [FINDS[tool]]
    assert ok(store, "draft_verdict", {"case_id": case_id})["band"] == "high_risk"
    assert ok(store, "draft_recruiter_reply", {"case_id": case_id})["questions"]
    assert "1930" in ok(store, "draft_cybercrime_report", {"case_id": case_id})["text"]
    assert ok(store, "get_case", {"case_id": case_id})["id"] == case_id
    assert [c["id"] for c in ok(store, "list_cases", {"status": "investigated"})] == [case_id]
    assert ok(store, "search_budget", {})["provider"] == "fake"
    post = ok(store, "publish_verdict", publish_args(store, case_id))
    ok(store, "record_outcome", {"case_id": case_id, "outcome": "walked_away"})
    ok(store, "retract_verdict", {"case_id": case_id, "reason": "published on the wrong case"})
    assert store.board == [] and store.cases[case_id]["status"] == "retracted"
    ok(store, "publish_verdict", publish_args(store, case_id, LABELS[2], "ask"))

    case = store.cases[case_id]
    assert case["status"] == "published"
    assert case["outcome"] == {
        "value": "walked_away",
        "recordedAt": NOW.isoformat(),
        "by": "human",
    }
    assert [p["label"] for p in store.board] == [LABELS[2]]
    assert post["whatsapp"].startswith("*Offer check: Likely impersonation*")
    assert post["band"] == "high_risk" and post["by"] == "human"
    assert not all(card["fromText"] for card in post["evidence"])
    cited = [card["signalId"] for card in post["evidence"]]
    assert cited == case["draftVerdict"]["evidenceIds"]
    done = {e["tool"] for e in store.activity_log if e["actor"] == "human" and e["result"] == "ok"}
    assert done == set(TOOLS) | set(VERBS)


def test_the_agent_reaches_every_agent_tool_and_nothing_else(store):
    case_id = ok(store, "open_case", {"text": sample("c")}, actor="agent")["id"]
    args = {
        "open_case": {"text": sample("b")},
        "update_claims": {"case_id": case_id, "confirm": True},
        "confirm_sender_domain": {"case_id": case_id, "domain": "zorvanta-support.example"},
        "scan_office_reviews": {"case_id": case_id, "term": "fraud"},
        "check_contact_footprint": {"case_id": case_id, "contact_index": 1},
        "list_cases": {},
        "search_budget": {},
    }
    for tool in TOOLS:
        ok(store, tool, args.get(tool, {"case_id": case_id}), actor="agent")
    for verb in VERBS:
        refused(store, verb, {"case_id": case_id}, actor="agent")
    done = {e["tool"] for e in store.activity_log if e["actor"] == "agent" and e["result"] == "ok"}
    assert done == set(TOOLS)
    assert not store.board and store.cases[case_id]["publishedVerdict"] is None


# ---- the activity log --------------------------------------------------------------------


def test_the_log_shows_who_started_a_check_and_what_the_agent_ran(ready, store):
    started = next(
        i
        for i, e in enumerate(store.activity_log)
        if e["tool"] == "investigate" and e["args"] == {"case_id": ready.decisive}
    )
    rows = [(e["actor"], e["tool"], e["result"]) for e in store.activity_log[started:][:3]]
    assert rows == [
        ("human", "investigate", "ok"),
        ("agent", "lookup_official_site", "ok"),
        ("agent", "check_office", "ok"),
    ]
    trace = store.cases[ready.decisive]["trace"]
    assert [(s["tool"], s["actor"], s["because"]) for s in trace] == [
        ("lookup_official_site", "agent", "the fake plan runs lookup_official_site"),
        ("check_office", "agent", "the fake plan runs check_office"),
    ]


def test_the_planners_checks_inside_a_call_are_marked_and_no_other_call_is(store):
    """So the page tells the agent's own calls from the checks a person's click ran."""
    investigated(store)
    other = ok(store, "open_case", {"text": sample("a")})["id"]
    ok(store, "update_claims", {"case_id": other, "confirm": True}, actor="agent")
    ok(store, "lookup_official_site", {"case_id": other}, actor="agent")

    rows = [(e["actor"], e["tool"], e.get("planner")) for e in store.activity_log]

    assert rows == [
        ("human", "open_case", None),
        ("human", "update_claims", None),
        ("human", "investigate", None),
        ("agent", "lookup_official_site", True),
        ("agent", "check_office", True),
        ("human", "open_case", None),
        ("agent", "update_claims", None),
        ("agent", "lookup_official_site", None),
    ]


def test_every_log_entry_carries_actor_and_outcome(ready, store):
    refused(store, "publish_verdict", verb_args("publish_verdict", ready), actor="agent")
    refused(store, "no_such_tool", {}, actor="agent")
    for entry in store.activity_log:
        assert {"ts", "actor", "tool", "args", "result"} <= set(entry)
        assert entry["actor"] in ("human", "agent")
        assert entry["result"] in ("ok", "refused", "error")
        assert ("reason" in entry) == (entry["result"] != "ok")


def test_the_log_redacts_contacts_keys_and_the_pasted_message(ready, store):
    phone = "+91 9" + "0" * 4 + " " + "0" * 5  # built here: the tree holds no phone number
    key = hashlib.sha256(b"offer-checkpost gate test").hexdigest()
    note = f"recruiter called from {phone}, mail hiring.desk@recruit-mail.example"
    ok(store, "retract_verdict", {"case_id": ready.published, "reason": note})
    refused(store, "search_budget", {"api_key": key}, actor="agent")
    refused(store, key, {}, actor="agent")

    opened = next(e for e in store.activity_log if e["tool"] == "open_case")
    assert re.fullmatch(r"\[\d+ characters\]", opened["args"]["text"])
    retracted = store.activity_log[-3]["args"]["reason"]
    assert phone not in retracted and "+91 9XXXX XXXXX" in retracted
    assert "hiring.desk@" not in retracted and "***@recruit-mail.example" in retracted
    assert key not in str(store.activity_log)
    assert store.cases[ready.published]["sourceText"] == sample("a")


class FailingAccount(FakeSearchProvider):
    def account(self):
        raise SearchError("key_rejected", "SerpApi rejected the key")


def test_a_failed_search_is_logged_with_its_fixed_message():
    store = Store(FailingAccount(), clock=lambda: NOW)
    out = invoke("search_budget", {}, "agent", store=store)
    assert out == {"ok": False, "outcome": "error", "error": "SerpApi rejected the key"}
    entry = store.activity_log[-1]
    assert (entry["result"], entry["reason"]) == ("error", "SerpApi rejected the key")


def test_an_unexpected_failure_is_logged_by_type_only_and_raised(fake_planner, store, monkeypatch):
    case_id = ok(store, "open_case", {"text": sample("c")})["id"]
    key = hashlib.sha256(b"offer-checkpost gate test").hexdigest()

    def broken(*args, **kwargs):
        raise RuntimeError(f"GET https://serpapi.com/search?api_key={key}")

    monkeypatch.setattr(fake_planner, "run_check", broken)
    with pytest.raises(RuntimeError):
        invoke("check_office", {"case_id": case_id}, "human", store=store)
    assert store.activity_log[-1]["reason"] == "RuntimeError"
    assert key not in str(store.state())


# ---- the store ---------------------------------------------------------------------------


def test_a_result_is_a_copy_the_caller_cannot_write_through(ready, store):
    case = ok(store, "get_case", {"case_id": ready.decisive})
    case["status"] = "published"
    case["signals"].clear()
    assert store.cases[ready.decisive]["status"] == "investigated"
    assert store.cases[ready.decisive]["signals"]


def test_open_case_builds_a_whole_case(store):
    case = ok(store, "open_case", {"text": sample("c")})
    assert case["id"] == "case_001" and case["status"] == "open"
    assert case["createdAt"] == "2026-10-03T10:02:11+05:30"
    assert case["sourceText"] == sample("c")
    assert [(s["id"], s["rule"]) for s in case["signals"]] == [
        ("sig_1", "sensitive_docs_early"),
        ("sig_2", "chat_only_interview"),
    ]
    assert case["budget"] == {"maxSearches": 6, "spent": 0, "saved": 0, "stoppedBecause": None}
    assert re.fullmatch(r"sha256:[0-9a-f]{16}", case["fingerprint"])
    for key in ("draftVerdict", "draftReply", "draftReport", "publishedVerdict", "outcome"):
        assert case[key] is None
    assert not any(c["confirmed"] for c in (case["claims"][f] for f in ("company", "city")))


def test_a_forwarded_copy_of_an_investigated_message_is_the_same_as_it(store):
    first = investigated(store, "a")
    forwarded = ok(store, "open_case", {"text": sample("a-forwarded")})
    assert forwarded["sameAs"] == first
    assert forwarded["fingerprint"] == store.cases[first]["fingerprint"]
    other = ok(store, "open_case", {"text": sample("b")})
    assert other["sameAs"] is None


def test_a_copy_opened_before_the_first_is_investigated_is_matched_when_investigated(store):
    first = ok(store, "open_case", {"text": sample("a")})["id"]
    forwarded = ok(store, "open_case", {"text": sample("a-forwarded")})["id"]
    assert store.cases[forwarded]["sameAs"] is None
    assert store.cases[forwarded]["fingerprint"] == store.cases[first]["fingerprint"]

    for case_id in (first, forwarded):
        ok(store, "update_claims", {"case_id": case_id, "confirm": True})
        ok(store, "investigate", {"case_id": case_id})
    assert store.cases[forwarded]["sameAs"] == first
    assert store.cases[first]["sameAs"] is None


def test_a_message_naming_no_company_gets_no_fingerprint(store):
    case = ok(store, "open_case", {"text": sample("task-per-like")})
    assert case["claims"]["company"] is None
    assert case["fingerprint"] is None and case["sameAs"] is None


def test_a_correction_stales_only_the_signals_that_read_that_claim(store):
    case_id = investigated(store)
    ok(store, "run_remaining_checks", {"case_id": case_id})
    rules = {s["id"]: s["rule"] for s in store.cases[case_id]["signals"]}

    same = ok(store, "update_claims", {"case_id": case_id, "fields": {"city": "Indore"}})
    assert same["corrected"] == [] and same["staleSignals"] == []

    moved = ok(store, "update_claims", {"case_id": case_id, "fields": {"city": "Pune"}})
    assert moved["corrected"] == ["city"]
    assert [rules[i] for i in moved["staleSignals"]] == ["office_not_found"]
    claim = store.cases[case_id]["claims"]["city"]
    assert claim == {"value": "Pune", "span": None, "confirmed": False, "corrected": True}

    renamed = ok(store, "update_claims", {"case_id": case_id, "fields": {"company": "Zorvanta"}})
    assert sorted(rules[i] for i in renamed["staleSignals"]) == [
        "impersonation_reports",
        "no_web_footprint",
    ]
    text_rules = {"sensitive_docs_early", "chat_only_interview"}
    assert all(not s["stale"] for s in store.cases[case_id]["signals"] if s["rule"] in text_rules)


def test_correcting_pay_and_fee_writes_rupee_claims(store):
    case_id = ok(store, "open_case", {"text": sample("c")})["id"]
    result = ok(store, "update_claims", {"case_id": case_id, "fields": {"pay": 40000, "fee": 999}})
    assert result["claims"]["pay"]["monthlyInr"] == 40000
    assert result["claims"]["fee"] == {
        "raw": "₹999",
        "amountInr": 999,
        "span": None,
        "confirmed": False,
        "corrected": True,
    }
    removed = ok(store, "update_claims", {"case_id": case_id, "fields": {"fee": None}})
    assert removed["claims"]["fee"] is None and removed["corrected"] == ["fee"]


def test_update_claims_leaves_drafts_board_and_status_alone(ready, store):
    ok(store, "draft_verdict", {"case_id": ready.decisive})
    before = copy.deepcopy(store.cases[ready.decisive])
    board = copy.deepcopy(store.board)

    result = ok(store, "update_claims", {"case_id": ready.decisive, "fields": {"role": "Clerk"}})

    after = store.cases[ready.decisive]
    for key in ("draftVerdict", "draftReply", "draftReport", "publishedVerdict", "status"):
        assert after[key] == before[key], key
    assert after["trace"] == before["trace"] and store.board == board
    # The one count a draft is checked against: this draft was written from the case before.
    assert result["corrected"] == ["role"]
    assert after["revision"] == before["revision"] + 1
    assert after["draftVerdict"]["revision"] == before["revision"]


@pytest.mark.parametrize(
    ("tool", "extra"),
    [
        ("update_claims", {"fields": {"company": "Northwind Bank of India Limited"}}),
        ("update_claims", {"confirm": True}),
        ("investigate", {}),
        ("lookup_official_site", {}),
        ("check_office", {}),
        ("confirm_sender_domain", {"domain": "brand-careers.example"}),
    ],
)
@pytest.mark.parametrize("actor", ["agent", "human"])
def test_a_case_on_the_board_is_not_changed_until_a_person_retracts_it(
    tool, extra, actor, ready, fake_planner, store
):
    before = without_log(store)
    planner_calls = len(fake_planner.calls)

    error = refused(store, tool, {"case_id": ready.published, **extra}, actor=actor)

    assert error == (
        f"{ready.published}'s verdict is on the Offer Board, and the case stays as it was "
        "published: a person retracts the verdict first, then the case can be corrected or "
        "checked again"
    )
    assert without_log(store) == before
    assert len(fake_planner.calls) == planner_calls
    assert store.activity_log[-1]["result"] == "refused"


def test_running_the_remaining_checks_on_a_case_on_the_board_is_refused_too(ready, store):
    before = without_log(store)
    error = refused(store, "run_remaining_checks", {"case_id": ready.published})
    assert "on the Offer Board" in error and without_log(store) == before


def test_once_retracted_the_case_can_be_corrected_and_published_again(ready, store):
    ok(store, "retract_verdict", {"case_id": ready.published, "reason": "wrong company"})
    ok(store, "update_claims", {"case_id": ready.published, "fields": {"role": "Clerk"}})
    assert store.cases[ready.published]["status"] == "retracted"

    post = published(store, ready.published, LABELS[2])

    assert post["offer"].startswith("Clerk · in the name of Brand")
    assert store.cases[ready.published]["status"] == "published"


def test_search_budget_returns_only_the_five_counts():
    key = hashlib.sha256(b"offer-checkpost gate test").hexdigest()
    account = {**FAKE_ACCOUNT, "account_email": "owner@mail.example", "api_key": key}
    store = Store(FakeSearchProvider(account=account), clock=lambda: NOW)
    budget = ok(store, "search_budget", {}, actor="agent")
    assert budget == {"provider": "fake", "account": FAKE_ACCOUNT}


def test_every_search_the_provider_serves_lands_in_the_call_log():
    route = {"engine": "google", "q": "x"}
    store = Store(FakeSearchProvider([(route, {"organic_results": []})]), clock=lambda: NOW)
    assert store.provider.search(route).searches_spent == 1
    assert store.calls == [
        {"ts": NOW.isoformat(), "provider": "fake", "engine": "google", "cache": "miss", "ms": 0}
    ]


def test_the_state_snapshot_carries_every_part(ready, store):
    state = store.state()
    assert set(state) == {"cases", "board", "activityLog", "calls", "server"}
    assert state["server"] == {"bind": None, "provider": "fake", "key": "missing"}
    assert set(STATUSES) >= {c["status"] for c in state["cases"]}


def test_from_env_reads_the_budget_settings_and_never_the_key_value():
    store = Store.from_env(
        {
            "OFFER_CHECKPOST_PROVIDER": "fake",
            "OFFER_CHECKPOST_MAX_SEARCHES": "4",
            "OFFER_CHECKPOST_QUOTA_RESERVE": "30",
            "SERPAPI_KEY": "set-for-this-test-only",
        }
    )
    assert (store.max_searches, store.quota_reserve) == (4, 30)
    assert store.server == {"bind": None, "provider": "fake", "key": "set"}
    assert "set-for-this-test-only" not in str(store.state())


# ---- the real planner, through the same invoke ---------------------------------------------


@pytest.fixture
def live_store():
    """The real planner and readers on the synthetic fixtures; no fake anywhere but the
    provider, which serves ``tests/fixtures/serp/`` and never the network."""
    return Store(
        FakeSearchProvider.from_fixtures(clock=lambda: NOW.timestamp()), clock=lambda: NOW
    )


def opened(store, name, actor="human"):
    case_id = ok(store, "open_case", {"text": sample(name)}, actor=actor)["id"]
    ok(store, "update_claims", {"case_id": case_id, "confirm": True}, actor=actor)
    return case_id


def test_sample_a_a_person_starts_the_agent_searches_only_a_person_publishes(live_store):
    store = live_store
    case_id = opened(store, "a")

    result = ok(store, "investigate", {"case_id": case_id})

    assert result["band"] == "high_risk"
    assert store.cases[case_id]["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }
    rows = [(e["actor"], e["tool"], e["result"]) for e in store.activity_log[-3:]]
    assert rows == [
        ("human", "investigate", "ok"),
        ("agent", "lookup_official_site", "ok"),
        ("agent", "find_fraud_notice", "ok"),
    ]
    searched = [line for line in store.cases[case_id]["trace"] if line["action"] == "ran"]
    assert [(line["tool"], line["actor"]) for line in searched] == [
        ("text_rules", "agent"),
        ("lookup_official_site", "agent"),
        ("find_fraud_notice", "agent"),
    ]
    assert searched[2]["because"].startswith("a fee was asked")
    assert [(c["engine"], c["provider"]) for c in store.calls] == [("google", "fake")] * 2

    draft = ok(store, "draft_verdict", {"case_id": case_id}, actor="agent")
    assert draft["band"] == "high_risk"
    before = without_log(store)
    args = publish_args(store, case_id)
    refused(store, "publish_verdict", args, "agent")
    assert without_log(store) == before

    post = ok(store, "publish_verdict", args)
    assert post["band"] == "high_risk"
    assert "fee_contradicts_employer" in {card["rule"] for card in post["evidence"]}
    assert post["whatsapp"].startswith("*Offer check: Likely impersonation*")


def test_sample_c_only_a_person_spends_searches_past_the_decisive_stop(live_store):
    store = live_store
    case_id = opened(store, "c", actor="agent")
    ok(store, "investigate", {"case_id": case_id}, actor="agent")
    assert store.cases[case_id]["budget"]["stoppedBecause"] == "decisive"
    before = without_log(store)

    error = refused(store, "run_remaining_checks", {"case_id": case_id}, actor="agent")
    assert error.startswith("run_remaining_checks is human-only")
    assert without_log(store) == before

    ok(store, "run_remaining_checks", {"case_id": case_id})
    on_request = [
        (line["tool"], line["actor"])
        for line in store.cases[case_id]["trace"]
        if line["action"] == "ran" and line["because"].startswith(planner.ON_REQUEST)
    ]
    assert on_request == [("check_job_listings", "human"), ("check_scam_reports", "human")]
    rows = [(e["actor"], e["tool"]) for e in store.activity_log[-3:]]
    assert rows == [
        ("human", "run_remaining_checks"),
        ("human", "check_job_listings"),
        ("human", "check_scam_reports"),
    ]


def test_sample_b_ends_consistent_with_genuine_after_four_searches(live_store):
    case_id = opened(live_store, "b")
    result = ok(live_store, "investigate", {"case_id": case_id})
    assert result["band"] == "consistent_with_genuine"
    assert result["budget"]["spent"] == 4
    assert len(live_store.calls) == 4


def test_the_planners_refusals_come_back_through_invoke_and_change_nothing(live_store):
    store = live_store
    unconfirmed = ok(store, "open_case", {"text": sample("c")})["id"]
    investigated_c = opened(store, "c")
    ok(store, "investigate", {"case_id": investigated_c})
    genuine = opened(store, "b")
    ok(store, "investigate", {"case_id": genuine})
    impersonation = opened(store, "a")
    ok(store, "investigate", {"case_id": impersonation})
    # A person's direct check reaches what the check itself refuses; the agent's is refused
    # first because the evidence on C and A is already decisive.
    attempts = [
        ("investigate", {"case_id": unconfirmed}, "human", "not confirmed"),
        ("check_office", {"case_id": unconfirmed}, "human", "not confirmed"),
        ("find_fraud_notice", {"case_id": investigated_c}, "human", "official domain"),
        ("find_fraud_notice", {"case_id": investigated_c}, "agent", "already decisive"),
        (
            "confirm_sender_domain",
            {"case_id": impersonation, "domain": "someone-else.example"},
            "human",
            "extracted from the offer",
        ),
        (
            "confirm_sender_domain",
            {"case_id": impersonation, "domain": "brand-careers.example"},
            "agent",
            "already decisive",
        ),
        (
            "check_contact_footprint",
            {"case_id": investigated_c, "contact_index": 0},
            "human",
            "synthetic placeholder",
        ),
        (
            "check_contact_footprint",
            {"case_id": investigated_c, "contact_index": 5},
            "human",
            "there is no contact 5",
        ),
        ("run_remaining_checks", {"case_id": genuine}, "human", "did not stop at a decisive"),
    ]
    for tool, args, actor, says in attempts:
        before = without_log(store)
        error = refused(store, tool, args, actor=actor)
        assert says in error, (tool, error)
        assert without_log(store) == before, tool
        assert store.activity_log[-1]["result"] == "refused"


def test_a_forwarded_copy_is_answered_from_the_first_check_at_zero_searches(live_store):
    store = live_store
    first = opened(store, "a")
    ok(store, "investigate", {"case_id": first})
    calls = len(store.calls)
    forwarded = opened(store, "a-forwarded")

    ok(store, "investigate", {"case_id": forwarded})

    case = store.cases[forwarded]
    assert case["sameAs"] == first
    assert case["budget"]["spent"] == 0 and case["budget"]["stoppedBecause"] == "same_as"
    assert len(store.calls) == calls
    assert ok(store, "draft_verdict", {"case_id": forwarded})["band"] == "high_risk"
