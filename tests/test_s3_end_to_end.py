"""Samples A, B and C end to end, driven by the agent through ``invoke`` alone: open, confirm,
investigate and draft, with the real extraction, planner, readers, rules and drafts, and only
the provider faked (``tests/fixtures/serp/``, never the network). The traces are the ones the
planner policy fixes for the three samples; the drafts cite only what those traces found; and
every human-only verb is refused to the agent while a person's call goes through."""

import copy
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from offer_checkpost import drafts, planner
from offer_checkpost.invoke import invoke
from offer_checkpost.providers import (
    FAKE_ACCOUNT,
    FIXTURES_DIR,
    ROUTES_FILE,
    FakeSearchProvider,
    SearchError,
)
from offer_checkpost.store import IST, Store
from offer_checkpost.tools import listing

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "offers"
# The synthetic stand-ins of the demo samples, which the fake provider's fixtures answer; any
# other sample is read from samples/offers.
STAND_INS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "offers"
ROUTES = json.loads((FIXTURES_DIR / ROUTES_FILE).read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 6, 10, 2, 11, tzinfo=IST)
NEVER_SAID = re.compile(r"\b(genuine|safe)\b", re.I)
VERBS = ("publish_verdict", "retract_verdict", "record_outcome", "run_remaining_checks")


@pytest.fixture
def store():
    provider = FakeSearchProvider.from_fixtures(clock=lambda: NOW.timestamp())
    return Store(provider, clock=lambda: NOW)


def call(store, tool, args, actor="agent"):
    out = invoke(tool, args, actor, store=store)
    assert out["ok"], out
    return out["result"]


def refusal(store, tool, args, actor="agent"):
    out = invoke(tool, args, actor, store=store)
    assert (out["ok"], out["outcome"]) == (False, "refused"), out
    return out["error"]


def sample_text(name):
    stand_in = STAND_INS / f"{name}.txt"
    return (stand_in if stand_in.exists() else SAMPLES / f"{name}.txt").read_text(encoding="utf-8")


def agent_investigates(store, name):
    text = sample_text(name)
    case_id = call(store, "open_case", {"text": text})["id"]
    call(store, "update_claims", {"case_id": case_id, "confirm": True})
    return case_id, call(store, "investigate", {"case_id": case_id})


def as_read(store, case_id, label, note=""):
    """publish_verdict's arguments for the case as it stands: what a person who has just read
    it sends."""
    revision = store.cases[case_id]["revision"]
    return {"case_id": case_id, "label": label, "note": note, "revision": revision}


def walk(case, lines=None):
    """(action, tool, step, rules fired, band) per trace line."""
    rule = {s["id"]: s["rule"] for s in case["signals"]}
    return [
        (x["action"], x["tool"], x["step"], [rule[i] for i in x["signalsAdded"]], x["band"])
        for x in (case["trace"] if lines is None else lines)
    ]


def without_log(store):
    state = store.state()
    del state["activityLog"]
    return state


def test_the_agents_tool_list_holds_no_human_verb():
    assert not {tool["name"] for tool in listing()} & set(VERBS)


# ---- Sample A: impersonation, a fee asked, a look-alike sender ----------------------------------


def test_sample_a_the_employers_own_notice_is_decisive_after_two_searches(store):
    case_id, result = agent_investigates(store, "a")
    case = store.cases[case_id]

    assert walk(case) == [
        ("ran", "text_rules", 0, ["fee_requested"], "unverified"),
        ("ran", "lookup_official_site", 1, ["sender_lookalike"], "unverified"),
        ("ran", "find_fraud_notice", 2, ["fee_contradicts_employer"], "high_risk"),
        ("stopped", "investigate", None, [], "high_risk"),
    ]
    assert case["trace"][1]["facts"]["officialDomain"] == "brand.example"
    assert case["trace"][2]["because"] == (
        "a fee was asked and step 1 named the official domain brand.example"
    )
    assert case["trace"][3]["because"].endswith("so 4 searches were not spent")
    assert result["band"] == "high_risk"
    assert result["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }
    assert [(c["engine"], c["provider"], c["cache"]) for c in store.calls] == [
        ("google", "fake", "miss")
    ] * 2
    assert {e["actor"] for e in store.activity_log} == {"agent"}


def test_sample_a_drafts_cite_the_notice_and_the_look_alike(store):
    case_id, _ = agent_investigates(store, "a")
    args = {"case_id": case_id}

    verdict = call(store, "draft_verdict", args)
    assert (verdict["band"], verdict["evidenceIds"]) == ("high_risk", ["sig_1", "sig_3"])
    assert "https://careers.brand.example/fraud-alert" in verdict["summary"]
    assert verdict["summary"].endswith(
        "Searches: 2 spent, 4 not spent: the evidence was decisive."
    )

    reply = call(store, "draft_recruiter_reply", args)
    assert [(q["rule"], q["signalIds"]) for q in reply["questions"]] == [
        ("fee_requested", ["sig_1"]),
        ("fee_contradicts_employer", ["sig_3"]),
        ("sender_lookalike", ["sig_2"]),
    ]
    assert "Please resend this offer from your @brand.example address." in reply["text"]
    assert "sig_" not in reply["text"]

    report = call(store, "draft_cybercrime_report", args)["text"]
    assert "Money asked for: ₹2,499" in report
    assert "- email: hr.onboarding@brand-careers.example" in report
    assert "Drafted verdict: high risk." in report

    case = store.cases[case_id]
    assert (case["draftVerdict"], case["draftReply"]) == (verdict, reply)
    assert case["publishedVerdict"] is None and case["status"] == "investigated"


def test_sample_a_the_agent_cannot_publish_or_decide_anything_a_person_can(store):
    case_id, _ = agent_investigates(store, "a")
    call(store, "draft_verdict", {"case_id": case_id})
    attempts = {
        "publish_verdict": as_read(store, case_id, "likely_impersonation"),
        "retract_verdict": {"case_id": case_id, "reason": "posted by mistake"},
        "record_outcome": {"case_id": case_id, "outcome": "walked_away"},
        "run_remaining_checks": {"case_id": case_id},
    }
    before = without_log(store)
    for verb, args in attempts.items():
        error = refusal(store, verb, args)
        assert error.startswith(f"{verb} is human-only"), error
        assert store.activity_log[-1]["result"] == "refused"
    assert without_log(store) == before

    post = call(store, "publish_verdict", attempts["publish_verdict"], actor="human")
    assert (post["by"], post["band"], post["label"]) == (
        "human",
        "high_risk",
        "likely_impersonation",
    )
    assert post["whatsapp"].startswith("*Offer check: Likely impersonation*")
    assert "hr.onboarding@" not in post["whatsapp"]
    assert store.board == [post] and store.cases[case_id]["status"] == "published"
    page = drafts.board_html(store.board, generated_at=NOW.isoformat())
    assert "Likely impersonation" in page and "careers.brand.example/fraud-alert" in page

    call(store, "record_outcome", attempts["record_outcome"], actor="human")
    assert store.cases[case_id]["outcome"]["value"] == "walked_away"


def test_sample_a_a_published_case_is_investigated_again_only_once_retracted(store):
    case_id, _ = agent_investigates(store, "a")
    call(store, "draft_verdict", {"case_id": case_id})
    args = as_read(store, case_id, "likely_impersonation", "shared with batch")
    post = call(store, "publish_verdict", args, actor="human")
    before = without_log(store)

    error = refusal(store, "investigate", {"case_id": case_id})

    assert "on the Offer Board" in error and without_log(store) == before
    retracted = {"case_id": case_id, "reason": "checking it again"}
    call(store, "retract_verdict", retracted, actor="human")
    call(store, "investigate", {"case_id": case_id})
    case = store.cases[case_id]
    assert case["status"] == "retracted" and case["publishedVerdict"] is None
    assert post["evidence"][1]["signalId"] == "sig_3" and not post["evidence"][1]["fromText"]
    # A second investigation checks afresh: the first one's findings are superseded, never
    # counted twice, and the same two searches are decisive again.
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }
    assert [s["id"] for s in case["signals"] if s.get("superseded")] == ["sig_2", "sig_3"]
    assert call(store, "draft_verdict", {"case_id": case_id})["evidenceIds"] == ["sig_1", "sig_5"]


def test_sample_a_forwarded_again_is_answered_at_zero_searches(store):
    first, _ = agent_investigates(store, "a")
    searched = len(store.calls)

    forwarded, result = agent_investigates(store, "a-forwarded")

    case = store.cases[forwarded]
    assert case["sameAs"] == first and len(store.calls) == searched
    assert result["band"] == "high_risk"
    assert result["budget"]["spent"] == 0 and result["budget"]["stoppedBecause"] == "same_as"
    assert case["trace"][1]["because"] == f"same message as {first}: reused, 0 searches"
    summary = call(store, "draft_verdict", {"case_id": forwarded})["summary"]
    assert summary.endswith(f"Same message as {first}: its checks were reused, 0 searches spent.")


# ---- Sample B: a real opening, the sender on the official domain --------------------------------


def test_sample_b_r2_skips_the_fraud_notice_and_ends_consistent_after_four_searches(store):
    case_id, result = agent_investigates(store, "b")
    case = store.cases[case_id]

    assert walk(case) == [
        ("ran", "text_rules", 0, [], "unverified"),
        ("ran", "lookup_official_site", 1, ["sender_official"], "unverified"),
        ("reordered", "check_job_listings", None, [], "unverified"),
        ("skipped", "find_fraud_notice", None, [], "unverified"),
        ("ran", "check_job_listings", 2, ["listing_match"], "consistent_with_genuine"),
        ("skipped", "confirm_sender_domain", None, [], "consistent_with_genuine"),
        ("ran", "check_office", 3, ["office_found"], "consistent_with_genuine"),
        ("skipped", "scan_office_reviews", None, [], "consistent_with_genuine"),
        ("ran", "check_scam_reports", 4, ["impersonation_reports"], "consistent_with_genuine"),
        ("skipped", "check_contact_footprint", None, [], "consistent_with_genuine"),
        ("stopped", "investigate", None, [], "consistent_with_genuine"),
    ]
    assert case["trace"][3]["because"] == planner.R2_SKIP
    assert result["budget"] == {"maxSearches": 6, "spent": 4, "saved": 0, "stoppedBecause": "done"}
    assert [c["engine"] for c in store.calls] == [
        "google",
        "google_jobs",
        "google_maps",
        "google_news",
    ]

    verdict = call(store, "draft_verdict", {"case_id": case_id})
    assert (verdict["band"], verdict["evidenceIds"]) == (
        "consistent_with_genuine",
        ["sig_1", "sig_2", "sig_3"],
    )
    assert verdict["summary"].endswith("Searches: 4 spent: every check that applied has run.")
    reply = call(store, "draft_recruiter_reply", {"case_id": case_id})
    assert [q["rule"] for q in reply["questions"]] == ["impersonation_reports"]
    report = call(store, "draft_cybercrime_report", {"case_id": case_id})["text"]
    assert "Drafted verdict: nothing found contradicts the offer." in report
    for text in (verdict["summary"], reply["text"]):
        assert NEVER_SAID.search(text) is None

    error = refusal(store, "run_remaining_checks", {"case_id": case_id}, actor="human")
    assert "did not stop at a decisive result" in error


# ---- Sample C: a fictional firm, documents asked for up front -----------------------------------


def test_sample_c_r1_checks_the_office_first_and_only_a_person_spends_past_decisive(store):
    case_id, result = agent_investigates(store, "c")
    case = store.cases[case_id]
    decisive = [
        ("ran", "text_rules", 0, ["sensitive_docs_early", "chat_only_interview"], "unverified"),
        ("ran", "lookup_official_site", 1, ["no_web_footprint"], "unverified"),
        ("reordered", "check_office", None, [], "unverified"),
        ("skipped", "find_fraud_notice", None, [], "unverified"),
        ("skipped", "confirm_sender_domain", None, [], "unverified"),
        ("ran", "check_office", 2, ["office_not_found"], "high_risk"),
        ("stopped", "investigate", None, [], "high_risk"),
    ]
    assert walk(case) == decisive
    assert case["trace"][2]["because"].startswith("R1, unknown firm: no_web_footprint fired")
    assert result["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }

    before = without_log(store)
    refusal(store, "run_remaining_checks", {"case_id": case_id})
    assert without_log(store) == before

    call(store, "run_remaining_checks", {"case_id": case_id}, actor="human")

    assert walk(case)[: len(decisive)] == decisive
    assert walk(case)[len(decisive) :] == [
        ("ran", "check_job_listings", 3, ["no_listing_match", "pay_outlier"], "high_risk"),
        ("skipped", "scan_office_reviews", None, [], "high_risk"),
        ("ran", "check_scam_reports", 4, [], "high_risk"),
        ("skipped", "check_contact_footprint", None, [], "high_risk"),
        ("skipped", "check_contact_footprint", None, [], "high_risk"),
        ("stopped", "run_remaining_checks", None, [], "high_risk"),
    ]
    on_request = [x for x in case["trace"] if x["action"] == "ran" and x["step"] > 2]
    assert {(x["actor"], x["because"].startswith(planner.ON_REQUEST)) for x in on_request} == {
        ("human", True)
    }
    assert case["budget"]["spent"] == 4
    assert [(e["actor"], e["tool"]) for e in store.activity_log[-3:]] == [
        ("human", "run_remaining_checks"),
        ("human", "check_job_listings"),
        ("human", "check_scam_reports"),
    ]


def test_sample_c_drafts_ask_one_question_per_red_flag_with_no_fee_asked(store):
    case_id, _ = agent_investigates(store, "c")
    call(store, "run_remaining_checks", {"case_id": case_id}, actor="human")
    args = {"case_id": case_id}

    verdict = call(store, "draft_verdict", args)
    assert (verdict["band"], verdict["evidenceIds"]) == (
        "high_risk",
        ["sig_1", "sig_3", "sig_4", "sig_6"],
    )
    reply = call(store, "draft_recruiter_reply", args)
    assert [q["rule"] for q in reply["questions"]] == [
        "sensitive_docs_early",
        "no_web_footprint",
        "pay_outlier",
        "office_not_found",
        "chat_only_interview",
        "no_listing_match",
    ]
    report = call(store, "draft_cybercrime_report", args)["text"]
    assert "Money asked for: none named in the message." in report
    assert "- phone: +91 9XXXX XXXXX" in report
    for draft in (verdict["summary"], reply["text"], report):
        assert NEVER_SAID.search(draft) is None


# ---- the stop rules hold for a check called on its own ------------------------------------------


def test_sample_c_the_agent_cannot_spend_past_the_decisive_stop_by_calling_checks(store):
    case_id, _ = agent_investigates(store, "c")
    before, searched = without_log(store), len(store.calls)

    for tool in ("check_job_listings", "check_scam_reports", "check_office"):
        error = refusal(store, tool, {"case_id": case_id})
        assert error.startswith("the evidence is already decisive"), error
    assert without_log(store) == before and len(store.calls) == searched

    call(store, "check_job_listings", {"case_id": case_id}, actor="human")
    summary = call(store, "draft_verdict", {"case_id": case_id})["summary"]
    assert summary.endswith("Searches: 3 spent, 3 not spent: the evidence was decisive.")


def test_a_check_called_on_its_own_stops_at_the_budget_and_the_quota_guard():
    account = {**FAKE_ACCOUNT, "plan_searches_left": 21}
    provider = FakeSearchProvider.from_fixtures(clock=lambda: NOW.timestamp(), account=account)
    store = Store(provider, max_searches=3, clock=lambda: NOW)
    text = sample_text("b")
    case_id = call(store, "open_case", {"text": text})["id"]
    assert "not confirmed" in refusal(store, "lookup_official_site", {"case_id": case_id})
    call(store, "update_claims", {"case_id": case_id, "confirm": True})

    for tool in ("lookup_official_site", "check_job_listings"):
        call(store, tool, {"case_id": case_id})
    error = refusal(store, "check_office", {"case_id": case_id}, actor="human")
    assert error.startswith("quota guard: 19 searches left this month, below the reserve of 20")

    store.quota_reserve = 0
    call(store, "check_office", {"case_id": case_id})
    error = refusal(store, "check_scam_reports", {"case_id": case_id}, actor="human")
    assert error.startswith("the case's search budget is spent: 3 of 3")
    assert len(store.calls) == 3


# ---- investigating again, corrections and copies ------------------------------------------------


def test_sample_c_investigated_twice_still_leaves_the_remaining_checks_to_a_person(store):
    case_id, _ = agent_investigates(store, "c")
    again = call(store, "investigate", {"case_id": case_id})
    assert again["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }

    done = call(store, "run_remaining_checks", {"case_id": case_id}, actor="human")

    assert done["budget"] == {"maxSearches": 6, "spent": 4, "saved": 0, "stoppedBecause": "done"}
    assert store.activity_log[-3]["result"] == "ok"
    verdict = call(store, "draft_verdict", {"case_id": case_id})
    assert len(verdict["evidenceIds"]) == len(set(verdict["evidenceIds"])) == 4


def test_sample_a_a_new_fee_amount_keeps_the_fee_red_flag(store):
    case_id, _ = agent_investigates(store, "a")
    args = {"case_id": case_id, "fields": {"fee": 2500}, "confirm": True}

    assert call(store, "update_claims", args)["staleSignals"] == []
    assert call(store, "draft_verdict", {"case_id": case_id})["band"] == "high_risk"
    assert call(store, "investigate", {"case_id": case_id})["band"] == "high_risk"
    reply = call(store, "draft_recruiter_reply", {"case_id": case_id})["text"]
    assert "Why does this offer ask for ₹2,500?" in reply

    removed = {"case_id": case_id, "fields": {"fee": None}, "confirm": True}
    stale = call(store, "update_claims", removed)["staleSignals"]
    rules = {s["id"]: s["rule"] for s in store.cases[case_id]["signals"]}
    assert sorted(rules[i] for i in stale) == ["fee_contradicts_employer", "fee_requested"]


def test_run_remaining_checks_waits_for_claims_corrected_since_the_decisive_stop(store):
    c_case, _ = agent_investigates(store, "c")
    call(store, "update_claims", {"case_id": c_case, "fields": {"role": "Telecaller"}})
    a_case, _ = agent_investigates(store, "a")
    fix = {"case_id": a_case, "fields": {"company": "Acme Widgets"}, "confirm": True}
    call(store, "update_claims", fix)
    before, searched = without_log(store), len(store.calls)

    error = refusal(store, "run_remaining_checks", {"case_id": c_case}, actor="human")
    assert "not confirmed" in error
    error = refusal(store, "run_remaining_checks", {"case_id": a_case}, actor="human")
    assert error.endswith("no longer stands: investigate the case again")
    assert without_log(store) == before and len(store.calls) == searched


class FirstSearchTimesOut(FakeSearchProvider):
    def search(self, params):
        if not self.calls:
            self.calls.append(dict(params))
            raise SearchError("timeout", "SerpApi did not answer in time")
        return super().search(params)


def test_sample_a_a_copy_that_failed_is_passed_over_for_one_that_finished():
    routes = [(r["params"], r["fixture"]) for r in ROUTES]
    store = Store(FirstSearchTimesOut(routes, clock=lambda: NOW.timestamp()), clock=lambda: NOW)

    failed, first = agent_investigates(store, "a")
    finished, second = agent_investigates(store, "a")
    copy, third = agent_investigates(store, "a-forwarded")

    assert first["budget"]["stoppedBecause"] == "search_error"
    assert store.cases[finished]["sameAs"] is None
    assert second["budget"]["stoppedBecause"] == "decisive"
    assert store.cases[copy]["sameAs"] == finished
    assert third["budget"] == {
        "maxSearches": 6,
        "spent": 0,
        "saved": 2,
        "stoppedBecause": "same_as",
    }
    assert len(store.calls) == 2


def test_sample_a_at_another_pay_is_not_answered_from_the_first_check(store):
    first, _ = agent_investigates(store, "a")
    text = sample_text("a").replace("₹38,000", "₹16,000")
    case = call(store, "open_case", {"text": text})

    assert case["sameAs"] is None
    assert case["fingerprint"] != store.cases[first]["fingerprint"]


def test_sample_b_investigated_twice_cites_each_finding_once(store):
    case_id, _ = agent_investigates(store, "b")
    call(store, "investigate", {"case_id": case_id})
    args = {"case_id": case_id}

    verdict = call(store, "draft_verdict", args)
    assert verdict["evidenceIds"] == ["sig_5", "sig_6", "sig_7"]
    evidence = [x for x in verdict["summary"].split("\n") if x.startswith("- ")]
    assert len(evidence) == 4 and len(set(evidence)) == 4
    report = call(store, "draft_cybercrime_report", args)["text"]
    assert report.count("office found on Maps") == 1
    publish = as_read(store, case_id, "no_contradictions_found")
    post = call(store, "publish_verdict", publish, "human")
    assert [card["signalId"] for card in post["evidence"]] == ["sig_5", "sig_6", "sig_7"]
    assert post["whatsapp"].count("•") == 3


def test_sample_a_a_copy_of_a_case_investigated_twice_cites_its_searches_with_sources(store):
    first, _ = agent_investigates(store, "a")
    call(store, "investigate", {"case_id": first})
    forwarded, result = agent_investigates(store, "a-forwarded")
    assert result["budget"]["stoppedBecause"] == "same_as"
    args = {"case_id": forwarded}

    summary = call(store, "draft_verdict", args)["summary"]
    assert "(the message): a fee was asked" not in summary
    assert "(step 4, find_fraud_notice on google)" in summary
    assert "Source: " in summary and "https://careers.brand.example/fraud-alert" in summary
    report = call(store, "draft_cybercrime_report", args)["text"]
    searched = report.split("Red flags found by web searches:\n")[1]
    assert not searched.startswith("- none")
    assert "employer says it charges no fee (step 4" in searched


# ---- a search that fails ------------------------------------------------------------------------


def test_a_failed_search_is_an_error_on_the_log_and_a_failed_line_on_the_trace():
    [lookup] = [r for r in ROUTES if (r["sample"], r["check"]) == ("a", "lookup_official_site")]
    store = Store(FakeSearchProvider([(lookup["params"], lookup["fixture"])]), clock=lambda: NOW)
    case_id, result = agent_investigates(store, "a")

    assert result["budget"]["stoppedBecause"] == "search_error"
    [failed] = [x for x in result["trace"] if x["action"] == "failed"]
    assert failed["tool"] == "find_fraud_notice" and failed["note"].startswith("no fake fixture")
    entry = store.activity_log[-1]
    assert (entry["actor"], entry["tool"], entry["result"]) == (
        "agent",
        "find_fraud_notice",
        "error",
    )

    out = invoke("find_fraud_notice", {"case_id": case_id}, "agent", store=store)
    assert (out["ok"], out["outcome"]) == (False, "error")
    assert out["error"].startswith("no fake fixture")
    assert store.cases[case_id]["trace"][-1]["action"] == "failed"


# ---- publishing: the case a person read, and nothing after ----------------------------------


def published_a(store):
    case_id, _ = agent_investigates(store, "a")
    call(store, "draft_verdict", {"case_id": case_id})
    args = as_read(store, case_id, "likely_impersonation", "Do not pay.")
    return case_id, call(store, "publish_verdict", args, actor="human")


def test_a_post_is_a_snapshot_that_nothing_done_to_the_case_rewrites(store):
    case_id, post = published_a(store)
    page = drafts.board_html(store.board, generated_at=NOW.isoformat())
    sent = copy.deepcopy(post)

    rename = {"case_id": case_id, "fields": {"company": "Northwind Bank of India Limited"}}
    assert "on the Offer Board" in refusal(store, "update_claims", rename)
    # Even a change made behind invoke's back reaches only the case, never the post.
    case = store.cases[case_id]
    case["claims"]["company"]["value"] = "Northwind Bank of India Limited"
    for signal in case["signals"]:
        signal["stale"] = True
        signal["evidence"]["link"] = "https://elsewhere.example/"

    assert store.board == [sent]
    assert drafts.board_html(store.board, generated_at=NOW.isoformat()) == page
    assert "in the name of Brand ·" in page and "Northwind" not in page
    assert page.count("https://careers.brand.example/fraud-alert") == 2  # the href and its text


def test_a_click_on_a_case_that_changed_after_the_person_read_it_is_refused(store):
    case_id, _ = agent_investigates(store, "a")
    call(store, "draft_verdict", {"case_id": case_id})
    read = as_read(store, case_id, "likely_impersonation")
    # Between the person's read and their click, the agent corrects claims that stale nothing,
    # confirms them and drafts again: the new draft is current, but it is not what was read.
    edit = {"role": "Relationship Manager", "city": "Pune", "pay": 380000}
    assert call(store, "update_claims", {"case_id": case_id, "fields": edit})["staleSignals"] == []
    call(store, "update_claims", {"case_id": case_id, "confirm": True})
    call(store, "draft_verdict", {"case_id": case_id})
    before = without_log(store)

    error = refusal(store, "publish_verdict", read, actor="human")

    assert error == (
        f"{case_id} has changed since it was read (a claim was corrected, or a check ran): read "
        "it again, then publish"
    )
    assert without_log(store) == before and store.board == []
    post = call(store, "publish_verdict", as_read(store, case_id, "likely_impersonation"), "human")
    assert post["offer"].startswith("Relationship Manager · in the name of Brand · Pune")


def test_only_a_current_draft_verdict_is_published(store):
    case_id, _ = agent_investigates(store, "a")
    args = as_read(store, case_id, "likely_impersonation")
    assert refusal(store, "publish_verdict", args, actor="human") == (
        f"{case_id} has no draft verdict: draft it, read it, then publish"
    )

    call(store, "draft_verdict", {"case_id": case_id})
    call(store, "update_claims", {"case_id": case_id, "fields": {"role": "Clerk"}})
    args = as_read(store, case_id, "likely_impersonation")
    assert refusal(store, "publish_verdict", args, actor="human").startswith(
        f"{case_id}'s draft verdict is older than the case"
    )
    assert store.board == []


@pytest.mark.parametrize(
    ("sample", "label", "error"),
    [
        (
            "a",
            "no_contradictions_found",
            "the label 'No contradictions found' is not what the checks found: the draft verdict "
            "is high risk",
        ),
        (
            "b",
            "likely_impersonation",
            "the label 'Likely impersonation' says more than the checks found: nothing they "
            "found contradicts the offer",
        ),
        (
            "b",
            "pay_to_apply_red_flag",
            "the label 'Pay-to-apply red flag' says more than the checks found: nothing they "
            "found contradicts the offer",
        ),
    ],
)
def test_a_label_that_says_more_than_the_checks_found_is_refused(store, sample, label, error):
    case_id, _ = agent_investigates(store, sample)
    call(store, "draft_verdict", {"case_id": case_id})

    assert refusal(store, "publish_verdict", as_read(store, case_id, label), "human") == error
    assert store.board == []


@pytest.mark.parametrize("sample", ["a", "b"])
def test_asking_questions_first_fits_any_band(store, sample):
    case_id, _ = agent_investigates(store, sample)
    call(store, "draft_verdict", {"case_id": case_id})
    post = call(
        store, "publish_verdict", as_read(store, case_id, "unverified_ask_questions"), "human"
    )
    not_proof = "Nothing the checks found contradicts it; that is not proof it is real."
    # The closing line follows what the checks found, whatever the label.
    assert (not_proof in post["whatsapp"].split("\n")) == (
        post["band"] == "consistent_with_genuine"
    )


def test_a_message_naming_no_company_is_told_why_it_cannot_be_published(store):
    case_id, result = agent_investigates(store, "task-per-like")
    assert result["budget"]["stoppedBecause"] == "no_company"
    call(store, "draft_verdict", {"case_id": case_id})

    error = refusal(
        store, "publish_verdict", as_read(store, case_id, "unverified_ask_questions"), "human"
    )

    assert error.startswith(f"{case_id} has no evidence from a search result")
    assert "names no company" in error and "recruiter reply and 1930 summary" in error
    assert "investigate it" not in error
