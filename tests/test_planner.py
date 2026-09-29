"""The planner against the fake provider and the synthetic fixtures: the traces of samples A,
B and C line by line, with the searches spent and saved and the band; the reorder rules R1 and
R2 with their "because"; every skip reason; the budget cap; the quota guard; a failed search;
``run_remaining_checks`` for a person and never for the agent; dedupe at 0 searches; and the
local cache, replay and the key staying out of the trace."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from offer_checkpost import planner
from offer_checkpost.checks import (
    check_contact_footprint_params,
    check_job_listings_params,
    check_office_params,
    check_scam_reports_params,
    confirm_sender_domain_params,
    find_fraud_notice_params,
    lookup_official_site_params,
    scan_office_reviews_params,
)
from offer_checkpost.extract import extract_claims, text_rules
from offer_checkpost.planner import PlanRefused, investigate, run_remaining_checks
from offer_checkpost.providers import (
    FAKE_ACCOUNT,
    FIXTURES_DIR,
    ROUTES_FILE,
    FakeSearchProvider,
    ReplaySearchProvider,
    SearchError,
    SerpApiSearchProvider,
    write_recording,
)
from offer_checkpost.rules import decide, text_signal
from offer_checkpost.store import fingerprint

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples" / "offers"
# The synthetic stand-ins of the demo samples, which the fake provider's fixtures answer; any
# other sample is read from samples/offers.
STAND_INS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "offers"
NOW = 1_791_000_000.0
RECORDED_AT = "2026-10-03T10:02:11+05:30"
NO_ENV: dict[str, str] = {}
ROUTES = json.loads((FIXTURES_DIR / ROUTES_FILE).read_text(encoding="utf-8"))


def fixture(path):
    return json.loads((FIXTURES_DIR / path).read_text(encoding="utf-8"))


def fake(*extra, **kwargs):
    """The routes of samples A, B and C, then ``extra`` (params, fixture) pairs, which win
    over a route for the same params."""
    routes = [(r["params"], r["fixture"]) for r in ROUTES] + list(extra)
    return FakeSearchProvider(routes, clock=lambda: NOW, **kwargs)


def sample_calls(sample):
    return [r["params"] for r in ROUTES if r["sample"] == sample]


def open_text(text, case_id="case_001", *, confirm=True):
    """A case as ``open_case`` leaves it, with its claims confirmed as ``update_claims``
    would, and the text rules left for step 0."""
    claims = extract_claims(text)
    if confirm:
        for name in ("company", "role", "city", "pay", "fee"):
            if claims[name] is not None:
                claims[name]["confirmed"] = True
    return {
        "id": case_id,
        "sourceText": text,
        "fingerprint": fingerprint(claims),
        "sameAs": None,
        "claims": claims,
        "signals": [],
        "trace": [],
        "budget": None,
        "status": "open",
    }


def sample_text(name):
    stand_in = STAND_INS / f"{name}.txt"
    return (stand_in if stand_in.exists() else SAMPLES_DIR / f"{name}.txt").read_text(
        encoding="utf-8"
    )


def open_sample(name, case_id="case_001", **kwargs):
    return open_text(sample_text(name), case_id, **kwargs)


def rule_of(case, signal_id):
    return next(s["rule"] for s in case["signals"] if s["id"] == signal_id)


def walk(case, lines=None):
    """(action, tool, step, rules fired, band) per trace line."""
    return [
        (
            line["action"],
            line["tool"],
            line["step"],
            [rule_of(case, i) for i in line["signalsAdded"]],
            line["band"],
        )
        for line in (case["trace"] if lines is None else lines)
    ]


def line(case, tool, action="ran"):
    [found] = [x for x in case["trace"] if x["tool"] == tool and x["action"] == action]
    return found


def becauses(case, action):
    return {x["tool"]: x["because"] for x in case["trace"] if x["action"] == action}


def stop(case):
    return case["trace"][-1]


# ---- sample A ------------------------------------------------------------------------------


def test_sample_a_stops_decisive_after_two_searches_with_four_saved():
    provider = fake()
    case = open_sample("a")
    outcome = investigate(case, provider, environ=NO_ENV)

    assert walk(case) == [
        ("ran", "text_rules", 0, ["fee_requested"], "unverified"),
        ("ran", "lookup_official_site", 1, ["sender_lookalike"], "unverified"),
        ("ran", "find_fraud_notice", 2, ["fee_contradicts_employer"], "high_risk"),
        ("stopped", "investigate", None, [], "high_risk"),
    ]
    assert provider.calls == sample_calls("a")[:2]
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }
    assert outcome["band"] == "high_risk" and outcome["budget"] == case["budget"]
    # The status belongs to the tool that called the planner, not to the planner.
    assert case["status"] == "open"

    step1, step2, end = case["trace"][1:]
    assert step1["facts"]["officialDomain"] == "brand.example"
    assert step2["because"] == "a fee was asked and step 1 named the official domain brand.example"
    assert step2["params"] == sample_calls("a")[1]
    assert (step2["engine"], step2["provider"], step2["cache"]) == ("google", "fake", "miss")
    assert [x["searchesSpent"] for x in case["trace"]] == [0, 1, 1, 0]
    assert end["searchesSaved"] == 4
    assert end["because"] == (
        "decisive: the band is high_risk and no search left can change it, so 4 searches were "
        "not spent"
    )
    assert {x["actor"] for x in case["trace"]} == {"agent"}
    notice = case["signals"][2]["evidence"]
    assert notice["link"] == "https://careers.brand.example/fraud-alert"


def test_sample_a_decisive_stop_leaves_the_lookalike_unconfirmed():
    case = open_sample("a")
    investigate(case, fake(), environ=NO_ENV)
    assert not [x for x in case["trace"] if x["tool"] == "confirm_sender_domain"]
    assert decide(case["signals"]).evidence_ids == ("sig_1", "sig_3")


def test_sample_a_remaining_checks_spend_exactly_the_four_saved():
    provider = fake()
    case = open_sample("a")
    investigate(case, provider, environ=NO_ENV)
    before = len(case["trace"])
    run_remaining_checks(case, provider, actor="human", environ=NO_ENV)

    assert walk(case, case["trace"][before:]) == [
        ("ran", "confirm_sender_domain", 3, ["domain_named_in_fraud_notice"], "high_risk"),
        ("ran", "check_job_listings", 4, ["no_listing_match"], "high_risk"),
        ("ran", "check_office", 5, ["office_not_found"], "high_risk"),
        ("skipped", "scan_office_reviews", None, [], "high_risk"),
        ("ran", "check_scam_reports", 6, ["impersonation_reports"], "high_risk"),
        ("skipped", "check_contact_footprint", None, [], "high_risk"),
        ("stopped", "run_remaining_checks", None, [], "high_risk"),
    ]
    assert case["budget"] == {"maxSearches": 6, "spent": 6, "saved": 0, "stoppedBecause": "done"}
    confirm = line(case, "confirm_sender_domain")
    assert confirm["args"] == {"case_id": "case_001", "domain": "brand-careers.example"}
    assert confirm["because"] == (
        "run on request after a decisive result: sender_lookalike fired for "
        "brand-careers.example: a search of brand.example tells an impostor from the "
        "employer's own second domain"
    )
    assert {x["actor"] for x in case["trace"][before:]} == {"human"}


# ---- sample C ------------------------------------------------------------------------------


def test_sample_c_r1_checks_the_office_first_and_stops_decisive():
    provider = fake()
    case = open_sample("c")
    investigate(case, provider, environ=NO_ENV)

    assert walk(case) == [
        ("ran", "text_rules", 0, ["sensitive_docs_early", "chat_only_interview"], "unverified"),
        ("ran", "lookup_official_site", 1, ["no_web_footprint"], "unverified"),
        ("reordered", "check_office", None, [], "unverified"),
        ("skipped", "find_fraud_notice", None, [], "unverified"),
        ("skipped", "confirm_sender_domain", None, [], "unverified"),
        ("ran", "check_office", 2, ["office_not_found"], "high_risk"),
        ("stopped", "investigate", None, [], "high_risk"),
    ]
    assert provider.calls == sample_calls("c")[:2]
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }
    assert line(case, "check_office", "reordered")["because"] == (
        "R1, unknown firm: no_web_footprint fired, so check_office moves ahead of "
        "check_job_listings: there's no domain to anchor a listing match, and an unknown "
        "firm's office is the cheapest claim to disprove"
    )
    assert becauses(case, "skipped") == {
        "find_fraud_notice": (
            "step 1 found no official domain, so there is no employer site to search for a "
            "fraud notice"
        ),
        "confirm_sender_domain": (
            "sender_lookalike did not fire: there is no look-alike domain to confirm"
        ),
    }
    assert line(case, "check_office")["because"] == (
        "a city is claimed (Indore), and R1 checks the office before the job listings"
    )
    assert stop(case)["searchesSaved"] == 4


def test_sample_c_remaining_checks_add_the_listings_and_the_news_on_request():
    provider = fake()
    case = open_sample("c")
    investigate(case, provider, environ=NO_ENV)
    before = len(case["trace"])
    outcome = run_remaining_checks(case, provider, actor="human", environ=NO_ENV)

    assert walk(case, case["trace"][before:]) == [
        ("ran", "check_job_listings", 3, ["no_listing_match", "pay_outlier"], "high_risk"),
        ("skipped", "scan_office_reviews", None, [], "high_risk"),
        ("ran", "check_scam_reports", 4, [], "high_risk"),
        ("skipped", "check_contact_footprint", None, [], "high_risk"),
        ("skipped", "check_contact_footprint", None, [], "high_risk"),
        ("stopped", "run_remaining_checks", None, [], "high_risk"),
    ]
    assert provider.calls == sample_calls("c")
    assert outcome["budget"] == {
        "maxSearches": 6,
        "spent": 4,
        "saved": 0,
        "stoppedBecause": "done",
    }
    listings = line(case, "check_job_listings")
    assert listings["because"] == (
        "run on request after a decisive result: a role is claimed (Customer Support Executive)"
    )
    assert listings["note"].endswith("pay found in 4 of 6 listings, median ₹17,250 a month")
    assert listings["facts"]["payCoverage"] == "pay found in 4 of 6 listings"
    new = case["trace"][before:]
    assert [x["because"] for x in new if x["action"] == "skipped"] == [
        "there is no place from check_office, so there are no reviews to scan",
        "the recruiter's phone is a synthetic placeholder, so it is never searched",
        "the recruiter's email is a synthetic placeholder, so it is never searched",
    ]
    assert {x["actor"] for x in new} == {"human"}


# ---- sample B ------------------------------------------------------------------------------


def test_sample_b_r2_skips_the_fraud_notice_and_runs_the_listings_first():
    provider = fake()
    case = open_sample("b")
    investigate(case, provider, environ=NO_ENV)

    genuine = "consistent_with_genuine"
    assert walk(case) == [
        ("ran", "text_rules", 0, [], "unverified"),
        ("ran", "lookup_official_site", 1, ["sender_official"], "unverified"),
        ("reordered", "check_job_listings", None, [], "unverified"),
        ("skipped", "find_fraud_notice", None, [], "unverified"),
        ("ran", "check_job_listings", 2, ["listing_match"], genuine),
        ("skipped", "confirm_sender_domain", None, [], genuine),
        ("ran", "check_office", 3, ["office_found"], genuine),
        ("skipped", "scan_office_reviews", None, [], genuine),
        ("ran", "check_scam_reports", 4, ["impersonation_reports"], genuine),
        ("skipped", "check_contact_footprint", None, [], genuine),
        ("stopped", "investigate", None, [], genuine),
    ]
    assert provider.calls == sample_calls("b")
    assert case["budget"] == {"maxSearches": 6, "spent": 4, "saved": 0, "stoppedBecause": "done"}
    assert line(case, "check_job_listings", "reordered")["because"] == (
        "R2, official sender: the sender is on the official domain and asks for no fee, so "
        "check_job_listings runs first: a listing with an apply option on the official domain "
        "is the strongest green signal"
    )
    assert becauses(case, "skipped") == {
        "find_fraud_notice": (
            "R2, official sender: the sender is on the official domain and asks for no fee, so "
            "there is nothing for a fraud notice to contradict"
        ),
        "confirm_sender_domain": (
            "sender_lookalike did not fire: there is no look-alike domain to confirm"
        ),
        "scan_office_reviews": (
            "no fee was asked and no_listing_match did not fire, so the office's reviews are "
            "not scanned"
        ),
        "check_contact_footprint": (
            "the recruiter's email is a synthetic placeholder, so it is never searched"
        ),
    }
    assert line(case, "check_job_listings")["because"] == (
        "a role is claimed (Graduate Engineer Trainee), and R2 checks the listings first"
    )
    assert stop(case)["because"] == "every check has run or been skipped"
    assert decide(case["signals"]).band == genuine


def test_sample_b_is_not_decisive_so_nothing_waits_for_a_person():
    provider = fake()
    case = open_sample("b")
    investigate(case, provider, environ=NO_ENV)
    with pytest.raises(PlanRefused, match="did not stop at a decisive result"):
        run_remaining_checks(case, provider, actor="human", environ=NO_ENV)


# ---- reorder rules and skip reasons --------------------------------------------------------


def with_fee(case):
    case["claims"]["fee"] = {"raw": "registration fee", "amountInr": None, "confirmed": True}
    return case


def test_r2_needs_no_fee_asked_so_a_fee_runs_the_fraud_notice():
    notice = find_fraud_notice_params("contoso.example", "Pune")
    provider = fake((notice, "google/fraud_notice_no_fee_phrase.json"))
    case = with_fee(open_sample("b"))
    investigate(case, provider, max_searches=2, environ=NO_ENV)

    assert [x["action"] for x in case["trace"]] == ["ran", "ran", "ran", "skipped", "stopped"]
    assert not [x for x in case["trace"] if x["action"] == "reordered"]
    assert line(case, "find_fraud_notice")["because"] == (
        "a fee was asked and step 1 named the official domain contoso.example"
    )
    assert case["budget"]["stoppedBecause"] == "budget"


HEXAVARA_DIRECTORY = {
    "organic_results": [
        {
            "title": "Hexavara Foods Chennai - Business Directory",
            "link": "https://directory.example/chennai/hexavara-foods",
            "snippet": "Hexavara Foods, T. Nagar, Chennai. Snacks wholesaler.",
        }
    ]
}
RECRUITER = {"kind": "email", "value": "hr.desk@quickhire.example", "synthetic": False}


def test_r1_also_follows_a_footprint_with_no_official_domain_and_a_real_contact_is_searched():
    contact = {**RECRUITER, "span": [0, 0]}
    firm, city = "Hexavara Foods", "Chennai"
    provider = fake(
        (lookup_official_site_params(firm, city), HEXAVARA_DIRECTORY),
        (check_office_params(firm, city), "google_maps/office_empty.json"),
        (check_job_listings_params("Store Supervisor", city), "google_jobs/listings_empty.json"),
        (check_scam_reports_params(firm), "google_news/reports_empty.json"),
        (check_contact_footprint_params(contact, city), "google/contact_footprint_reported.json"),
    )
    case = open_sample("walk-in-genuine-shape")
    case["claims"]["contacts"] = [contact]
    investigate(case, provider, environ=NO_ENV)

    assert walk(case) == [
        ("ran", "text_rules", 0, [], "unverified"),
        ("ran", "lookup_official_site", 1, [], "unverified"),
        ("reordered", "check_office", None, [], "unverified"),
        ("skipped", "find_fraud_notice", None, [], "unverified"),
        ("skipped", "confirm_sender_domain", None, [], "unverified"),
        ("ran", "check_office", 2, ["office_not_found"], "unverified"),
        ("ran", "check_job_listings", 3, ["no_listing_match"], "unverified"),
        ("skipped", "scan_office_reviews", None, [], "unverified"),
        ("ran", "check_scam_reports", 4, [], "unverified"),
        ("ran", "check_contact_footprint", 5, ["contact_reported"], "unverified"),
        ("stopped", "investigate", None, [], "unverified"),
    ]
    assert line(case, "check_office", "reordered")["because"].startswith(
        "R1, unknown firm: step 1 found no official domain, so check_office moves ahead of "
        "check_job_listings"
    )
    footprint = line(case, "check_contact_footprint")
    assert footprint["args"] == {"case_id": "case_001", "contact_index": 0}
    assert footprint["because"] == (
        "the offer gives a recruiter email: whether it already appears next to scam reports"
    )
    assert case["budget"]["spent"] == 5


def test_nothing_for_a_fraud_notice_to_contradict_skips_it_even_off_r2():
    lookup = lookup_official_site_params("Hexavara Foods", "Chennai")
    provider = fake((lookup, "google/official_site_organic_only.json"))
    case = open_sample("walk-in-genuine-shape")
    investigate(case, provider, max_searches=1, environ=NO_ENV)

    assert line(case, "lookup_official_site")["facts"]["via"] == "organic"
    assert not [x for x in case["trace"] if x["action"] == "reordered"]
    assert becauses(case, "skipped") == {
        "find_fraud_notice": (
            "no fee or sensitive documents were asked for, and no recruiter email or link is "
            "off hexavara.example"
        ),
        "confirm_sender_domain": (
            "sender_lookalike did not fire: there is no look-alike domain to confirm"
        ),
    }
    assert stop(case)["because"] == "the search budget is spent: 1 of 1"


def test_a_link_on_the_official_domain_skips_the_fraud_notice_without_a_knowledge_graph():
    # Step 1 took the domain from the top organic result, so sender_official can't fire; the
    # link is on that domain all the same, and with no fee a notice has nothing to contradict.
    lookup = lookup_official_site_params("Hexavara Foods", "Chennai")
    notice = find_fraud_notice_params("hexavara.example", "Chennai")
    provider = fake(
        (lookup, "google/official_site_organic_only.json"),
        (notice, "google/fraud_notice_no_fee_phrase.json"),
    )
    posting = sample_text("walk-in-genuine-shape") + (
        "\nApply at https://careers.hexavara.example/store-supervisor"
    )
    case = open_text(posting)
    investigate(case, provider, max_searches=1, environ=NO_ENV)

    assert line(case, "lookup_official_site")["facts"]["via"] == "organic"
    assert not [s for s in case["signals"] if s["rule"] == "sender_official"]
    assert becauses(case, "skipped")["find_fraud_notice"] == (
        "no fee or sensitive documents were asked for, and no recruiter email or link is "
        "off hexavara.example"
    )

    # The same link, with the recruiter writing from another domain, runs it.
    case = open_text(posting + "\nWrite to hr.desk@quickhire.example")
    investigate(case, provider, max_searches=2, environ=NO_ENV)
    assert line(case, "find_fraud_notice")["because"] == (
        "the recruiter's email or link is not on the official domain and step 1 named the "
        "official domain hexavara.example"
    )


LOOKALIKES = (
    "Greetings from Brand! You are shortlisted for our Noida office. Write to "
    "hr@brand-careers.example, jobs@brand-hiring.example or desk@brandjobs.example to book a slot."
)


def test_at_most_two_lookalikes_are_confirmed_and_a_missing_role_skips_the_listings():
    confirm = confirm_sender_domain_params("brand.example", "brand-hiring.example", "Noida")
    provider = fake((confirm, "google/confirm_second_official.json"))
    case = open_text(LOOKALIKES)
    investigate(case, provider, max_searches=4, environ=NO_ENV)

    assert walk(case) == [
        ("ran", "text_rules", 0, [], "unverified"),
        ("ran", "lookup_official_site", 1, ["sender_lookalike"] * 3, "unverified"),
        ("ran", "find_fraud_notice", 2, [], "unverified"),
        ("ran", "confirm_sender_domain", 3, ["domain_named_in_fraud_notice"], "unverified"),
        ("ran", "confirm_sender_domain", 4, ["sender_official"], "unverified"),
        ("skipped", "confirm_sender_domain", None, [], "unverified"),
        ("skipped", "check_job_listings", None, [], "unverified"),
        ("stopped", "investigate", None, [], "unverified"),
    ]
    assert line(case, "find_fraud_notice")["because"] == (
        "the recruiter's email or link is not on the official domain and step 1 named the "
        "official domain brand.example"
    )
    skipped = [x for x in case["trace"] if x["action"] == "skipped"]
    assert skipped[0]["args"]["domain"] == "brandjobs.example"
    assert [x["because"] for x in skipped] == [
        "at most 2 look-alike domains are confirmed, so brandjobs.example stays a moderate "
        "look-alike",
        "no role is claimed, so there is no listing to look for",
    ]
    assert case["budget"]["stoppedBecause"] == "budget"


def test_a_missing_city_skips_the_office_and_its_reviews():
    listings = check_job_listings_params("Graduate Engineer Trainee", None, "Contoso")
    provider = fake(
        (lookup_official_site_params("Contoso"), "google/official_site_contoso.json"),
        (listings, "google_jobs/listings_contoso.json"),
    )
    case = open_sample("b")
    case["claims"]["city"] = None
    investigate(case, provider, environ=NO_ENV)

    assert becauses(case, "skipped")["check_office"] == (
        "no city or address is claimed, so there is no office to look for"
    )
    assert becauses(case, "skipped")["scan_office_reviews"] == (
        "there is no place from check_office, so there are no reviews to scan"
    )
    assert case["budget"] == {"maxSearches": 6, "spent": 3, "saved": 0, "stoppedBecause": "done"}


def test_the_office_reviews_are_scanned_for_fraud_when_no_listing_matched():
    listings = check_job_listings_params("Graduate Engineer Trainee", "Pune", "Contoso")
    reviews = scan_office_reviews_params("ChIJsyntheticContosoPune01", "fraud")
    provider = fake(
        (listings, "google_jobs/listings_empty.json"),
        (reviews, "google_maps_reviews/reviews_feedback_only.json"),
    )
    case = open_sample("b")
    investigate(case, provider, environ=NO_ENV)

    reviews = line(case, "scan_office_reviews")
    assert reviews["args"] == {"case_id": "case_001", "term": "fraud"}
    assert reviews["because"] == (
        "no listing by Contoso matched and check_office found Contoso Pune Office: its reviews "
        "are scanned for 'fraud'"
    )
    assert reviews["engine"] == "google_maps_reviews"
    assert case["budget"]["spent"] == 5


def test_no_company_named_stops_before_any_search():
    provider = fake()
    case = open_sample("task-per-like")
    investigate(case, provider, environ=NO_ENV)

    assert walk(case) == [
        ("ran", "text_rules", 0, ["task_scam_pattern"], "unverified"),
        ("stopped", "investigate", None, [], "unverified"),
    ]
    assert stop(case)["because"] == "no company named: nothing to check on the web"
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 0,
        "saved": 0,
        "stoppedBecause": "no_company",
    }
    assert provider.calls == []


# ---- budget, quota, errors -----------------------------------------------------------------


def test_the_budget_cap_stops_before_the_search_that_would_pass_it():
    provider = fake()
    case = open_sample("a")
    investigate(case, provider, max_searches=1, environ=NO_ENV)

    assert [x["tool"] for x in case["trace"]] == [
        "text_rules",
        "lookup_official_site",
        "investigate",
    ]
    assert case["budget"] == {"maxSearches": 1, "spent": 1, "saved": 0, "stoppedBecause": "budget"}
    assert len(provider.calls) == 1


def test_the_configured_budget_is_a_ceiling_the_tool_argument_cannot_raise():
    env = {"OFFER_CHECKPOST_MAX_SEARCHES": "3"}
    provider = fake()
    case = open_sample("b")
    investigate(case, provider, max_searches=10, environ=env)

    assert case["budget"] == {"maxSearches": 3, "spent": 3, "saved": 0, "stoppedBecause": "budget"}
    assert provider.calls == sample_calls("b")[:3]
    assert stop(case)["because"] == "the search budget is spent: 3 of 3"


def test_the_quota_guard_stops_below_the_reserve_without_falling_back():
    provider = fake(account={**FAKE_ACCOUNT, "plan_searches_left": 21})
    case = open_sample("b")
    investigate(case, provider, environ=NO_ENV)

    assert [x["tool"] for x in case["trace"] if x["action"] == "ran"] == [
        "text_rules",
        "lookup_official_site",
        "check_job_listings",
    ]
    assert case["budget"] == {"maxSearches": 6, "spent": 2, "saved": 0, "stoppedBecause": "quota"}
    assert stop(case)["because"] == (
        "quota guard: 19 searches left this month, below the reserve of 20; stopped, with no "
        "fallback to other data"
    )
    assert provider.calls == sample_calls("b")[:2]
    assert provider.account()["plan_searches_left"] == 19


def test_the_quota_guard_can_stop_before_the_first_search():
    provider = fake(account={**FAKE_ACCOUNT, "plan_searches_left": 7})
    case = open_sample("a")
    investigate(case, provider, environ={"OFFER_CHECKPOST_QUOTA_RESERVE": "8"})

    assert walk(case) == [
        ("ran", "text_rules", 0, ["fee_requested"], "unverified"),
        ("stopped", "investigate", None, [], "unverified"),
    ]
    assert case["budget"]["stoppedBecause"] == "quota"
    assert provider.calls == []


def test_a_failed_search_stops_the_plan_and_nothing_is_made_up():
    [lookup] = sample_calls("a")[:1]
    provider = FakeSearchProvider([(lookup, "google/official_site_brand.json")], clock=lambda: NOW)
    case = open_sample("a")
    investigate(case, provider, environ=NO_ENV)

    failed = line(case, "find_fraud_notice", "failed")
    assert failed["step"] == 2 and failed["signalsAdded"] == [] and failed["searchesSpent"] == 0
    assert failed["note"].startswith("no fake fixture for")
    assert failed["params"] == sample_calls("a")[1]
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 1,
        "saved": 0,
        "stoppedBecause": "search_error",
    }
    assert stop(case)["because"].startswith("find_fraud_notice failed: no fake fixture for")
    assert [s["rule"] for s in case["signals"]] == ["fee_requested", "sender_lookalike"]


def test_a_check_called_directly_records_a_failed_search_and_raises_it():
    provider = FakeSearchProvider([], clock=lambda: NOW)
    case = open_sample("a")
    args = {"case_id": "case_001"}
    with pytest.raises(SearchError, match="no fake fixture for"):
        planner.run_check(case, "lookup_official_site", args, provider=provider, actor="agent")
    [failed] = case["trace"]
    assert (failed["action"], failed["step"], failed["searchesSpent"]) == ("failed", 1, 0)
    assert failed["params"] == sample_calls("a")[0]
    assert case["signals"] == []


# ---- the human verb ------------------------------------------------------------------------


def test_run_remaining_checks_refuses_the_agent_and_changes_nothing():
    provider = fake()
    case = open_sample("c")
    investigate(case, provider, environ=NO_ENV)
    before = json.dumps(case, sort_keys=True)

    for actor in ("agent", "mcp", ""):
        with pytest.raises(PlanRefused, match="for a person only"):
            run_remaining_checks(case, provider, actor=actor, environ=NO_ENV)
    assert json.dumps(case, sort_keys=True) == before
    assert provider.calls == sample_calls("c")[:2]


def test_run_remaining_checks_stays_within_the_budget_of_the_investigation():
    provider = fake()
    case = open_sample("a")
    investigate(case, provider, max_searches=5, environ=NO_ENV)
    assert case["budget"] == {
        "maxSearches": 5,
        "spent": 2,
        "saved": 3,
        "stoppedBecause": "decisive",
    }
    run_remaining_checks(case, provider, actor="human", environ=NO_ENV)

    assert [x["tool"] for x in case["trace"] if x["action"] == "ran"][-3:] == [
        "confirm_sender_domain",
        "check_job_listings",
        "check_office",
    ]
    assert case["budget"] == {"maxSearches": 5, "spent": 5, "saved": 0, "stoppedBecause": "budget"}
    with pytest.raises(PlanRefused):
        run_remaining_checks(case, provider, actor="human", environ=NO_ENV)


def test_the_planner_exposes_no_human_verb_but_run_remaining_checks_and_gates_that():
    human_only = {"publish_verdict", "retract_verdict", "record_outcome"}
    assert not human_only & set(dir(planner))
    assert not {"run_remaining_checks", *human_only} & set(planner.CHECK_TOOLS)


# ---- dedupe --------------------------------------------------------------------------------


def test_a_forwarded_copy_reuses_the_earlier_case_at_zero_searches():
    earlier = open_sample("a", "case_001")
    investigate(earlier, fake(), environ=NO_ENV)

    provider = fake()
    case = open_sample("a-forwarded", "case_002")
    assert case["fingerprint"] == earlier["fingerprint"]
    case["sameAs"] = "case_001"
    outcome = investigate(case, provider, same_as=earlier, environ=NO_ENV)

    assert provider.calls == []
    assert outcome["band"] == "high_risk"
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 0,
        "saved": 2,
        "stoppedBecause": "same_as",
    }
    reused = case["trace"][1]
    assert (reused["action"], reused["because"]) == (
        "reused",
        "same message as case_001: reused, 0 searches",
    )
    assert [rule_of(case, i) for i in reused["signalsAdded"]] == [
        "sender_lookalike",
        "fee_contradicts_employer",
    ]
    copied = case["trace"][2:]
    assert [x["tool"] for x in copied] == [
        "lookup_official_site",
        "find_fraud_notice",
        "investigate",
    ]
    assert {x["reusedFrom"] for x in copied} == {"case_001"}
    assert [s["id"] for s in case["signals"]] == ["sig_1", "sig_2", "sig_3"]
    assert [s.get("reusedFrom") for s in case["signals"]] == [None, "case_001", "case_001"]
    assert decide(case["signals"]).evidence_ids == ("sig_1", "sig_3")


def test_a_corrected_claim_breaks_the_match_and_the_case_is_searched():
    earlier = open_sample("a", "case_001")
    investigate(earlier, fake(), environ=NO_ENV)
    provider = fake()
    case = open_sample("b", "case_002")
    case["sameAs"] = "case_001"
    investigate(case, provider, same_as=earlier, environ=NO_ENV)
    assert provider.calls == sample_calls("b")
    assert case["budget"]["stoppedBecause"] == "done"


def test_an_unfinished_earlier_case_is_searched_again():
    earlier = open_sample("a", "case_001")
    investigate(earlier, fake(account={**FAKE_ACCOUNT, "plan_searches_left": 0}), environ=NO_ENV)
    assert earlier["budget"]["stoppedBecause"] == "quota"
    provider = fake()
    case = open_sample("a-forwarded", "case_002")
    investigate(case, provider, same_as=earlier, environ=NO_ENV)
    assert provider.calls == sample_calls("a")[:2]


def test_the_fingerprint_ignores_case_spacing_and_legal_suffixes():
    claims = extract_claims(sample_text("c"))
    variant = json.loads(json.dumps(claims))
    variant["company"]["value"] = "ZORVANTA  Support Services"
    variant["contacts"][0]["value"] = "+919XXXXXXXXX"
    assert fingerprint(variant) == fingerprint(claims)
    variant["city"]["value"] = "Pune"
    assert fingerprint(variant) != fingerprint(claims)


def test_an_offer_at_another_pay_or_fee_is_another_message():
    # The pay benchmark and the fee rules read these, so a finding for one never answers another.
    claims = extract_claims(sample_text("a"))
    for field, key, value in [
        ("pay", "monthlyInr", 16000),
        ("fee", "amountInr", 999),
        ("fee", "amountInr", None),
    ]:
        variant = json.loads(json.dumps(claims))
        variant[field][key] = value
        assert fingerprint(variant) != fingerprint(claims), (field, value)
    variant = json.loads(json.dumps(claims))
    variant["fee"] = None
    assert fingerprint(variant) != fingerprint(claims)


# ---- step 0, confirmation and the runner ---------------------------------------------------


def test_investigate_refuses_unconfirmed_claims_and_searches_nothing():
    provider = fake()
    case = open_sample("a", confirm=False)
    with pytest.raises(PlanRefused, match="not confirmed"):
        investigate(case, provider, environ=NO_ENV)
    assert case["trace"] == [] and provider.calls == []


def test_step_0_never_fires_a_text_rule_twice_and_keeps_a_stale_one_stale():
    case = open_sample("a")
    [hit] = text_rules(case["sourceText"])
    case["signals"] = [{**text_signal(hit, "sig_1"), "stale": True}]
    case["claims"]["fee"] = None
    notice = sample_calls("a")[1]
    investigate(case, fake(), max_searches=3, environ=NO_ENV)

    assert [s["rule"] for s in case["signals"]].count("fee_requested") == 1
    assert case["trace"][0]["signalsAdded"] == []
    assert case["trace"][0]["note"] == "no text rule fired"
    fraud = line(case, "find_fraud_notice")
    assert fraud["params"] == notice and fraud["signalsAdded"] == []
    assert fraud["because"].startswith("the recruiter's email or link is not on the official")


def test_every_search_goes_through_the_runner_and_carries_the_planners_because():
    calls = []
    case = open_sample("a")
    provider = fake()

    def runner(tool, args):
        calls.append((tool, args))
        planner.run_check(case, tool, args, provider=provider, actor="agent")

    investigate(case, provider, runner=runner, environ=NO_ENV)
    assert calls == [
        ("lookup_official_site", {"case_id": "case_001"}),
        ("find_fraud_notice", {"case_id": "case_001"}),
    ]
    assert [x["because"] for x in case["trace"][1:3]] == [
        "a company is claimed (Brand), so step 1 looks up its official domain and classifies "
        "the recruiter's domains",
        "a fee was asked and step 1 named the official domain brand.example",
    ]


class LoggedSearches:
    """A store's stand-in: wraps a provider and logs every search it serves."""

    def __init__(self, provider):
        self.provider = provider
        self.log = []

    def search(self, params):
        result = self.provider.search(params)
        self.log.append((result.engine, result.cache, result.searches_spent))
        return result

    def account(self):
        return self.provider.account()


def test_anything_that_searches_and_reads_the_account_can_stand_in_for_the_provider():
    searcher = LoggedSearches(fake(account={**FAKE_ACCOUNT, "plan_searches_left": 21}))
    case = open_sample("c")
    investigate(case, searcher, environ=NO_ENV)
    assert searcher.log == [("google", "miss", 1), ("google_maps", "miss", 1)]
    assert case["budget"]["stoppedBecause"] == "decisive"


def test_a_runner_that_appends_no_line_fails_loudly_instead_of_relabelling_another():
    case = open_sample("a")
    with pytest.raises(ValueError):
        investigate(case, fake(), runner=lambda tool, args: None, environ=NO_ENV)
    assert [x["because"] for x in case["trace"]] == [
        "text rules read the message itself and cost no search"
    ]


def test_every_trace_line_has_the_same_keys():
    cases = []
    for sample in "abc":
        case = open_sample(sample)
        investigate(case, fake(), environ=NO_ENV)
        cases.append(case)
    run_remaining_checks(cases[2], fake(), actor="human", environ=NO_ENV)
    for case in cases:
        json.dumps(case)
        for x in case["trace"]:
            assert tuple(x) == planner.TRACE_KEYS
            assert x["action"] in {"ran", "failed", "skipped", "reordered", "stopped"}
        assert case["budget"]["stoppedBecause"] in planner.STOP_REASONS


# ---- one check at a time -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sample", "tool", "extra", "reason"),
    [
        ("c", "find_fraud_notice", {}, "needs the employer's official domain"),
        ("c", "confirm_sender_domain", {"domain": "zorvanta-support.example"}, "official domain"),
        ("c", "scan_office_reviews", {"term": "fee"}, "needs a place_id"),
        ("c", "check_contact_footprint", {"contact_index": 1}, "synthetic placeholder"),
        ("c", "check_contact_footprint", {"contact_index": 5}, "there is no contact 5"),
        ("a", "confirm_sender_domain", {"domain": "elsewhere.example"}, "is not one"),
        ("a", "scan_office_reviews", {"term": "salary"}, "'fee' or 'fraud' only"),
        ("task-per-like", "check_scam_reports", {}, "needs a claimed company"),
    ],
)
def test_a_check_refuses_what_it_cannot_or_must_not_search(sample, tool, extra, reason):
    provider = fake()
    case = open_sample(sample)
    investigate(case, provider, environ=NO_ENV)
    before, calls = json.dumps(case, sort_keys=True), len(provider.calls)
    args = {"case_id": "case_001", **extra}
    with pytest.raises(PlanRefused, match=reason):
        planner.run_check(case, tool, args, provider=provider, actor="agent")
    assert json.dumps(case, sort_keys=True) == before
    assert len(provider.calls) == calls


def test_a_check_called_directly_is_traced_and_counted():
    contact = {**RECRUITER, "span": [0, 0]}
    provider = fake(
        (
            check_contact_footprint_params(contact, "Noida"),
            "google/contact_footprint_reported.json",
        )
    )
    case = open_sample("a")
    case["claims"]["contacts"] = [contact]
    result = planner.run_check(
        case,
        "check_contact_footprint",
        {"case_id": "case_001", "contact_index": 0},
        provider=provider,
        actor="agent",
    )
    assert result is case["trace"][-1]
    assert (result["step"], result["because"]) == (1, "called directly by the agent")
    assert [rule_of(case, i) for i in result["signalsAdded"]] == ["contact_reported"]
    assert case["budget"]["spent"] == 1


def check_directly(case, tool, provider, actor, reserve=20, **extra):
    args = {"case_id": case["id"], **extra}
    return planner.check_directly(
        case, tool, args, provider=provider, actor=actor, reserve=reserve
    )


def test_the_agent_calling_a_check_directly_never_searches_past_a_decisive_stop():
    provider = fake()
    case = open_sample("c")
    investigate(case, provider, environ=NO_ENV)
    before, calls = json.dumps(case, sort_keys=True), len(provider.calls)

    for tool in ("check_job_listings", "check_scam_reports", "check_office"):
        with pytest.raises(PlanRefused, match="the evidence is already decisive"):
            check_directly(case, tool, provider, "agent")
    assert json.dumps(case, sort_keys=True) == before and len(provider.calls) == calls

    # A person may choose to, and the search comes out of what the decisive stop saved.
    check_directly(case, "check_job_listings", provider, "human")
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 3,
        "saved": 3,
        "stoppedBecause": "decisive",
    }


def test_the_agent_cannot_confirm_a_lookalike_the_decisive_result_does_not_need():
    case = open_sample("a")
    investigate(case, fake(), environ=NO_ENV)
    with pytest.raises(PlanRefused, match="already decisive"):
        check_directly(
            case, "confirm_sender_domain", fake(), "agent", domain="brand-careers.example"
        )


def test_a_check_called_directly_keeps_the_budget_the_quota_guard_and_confirmation():
    spent = open_sample("b")
    investigate(spent, fake(), max_searches=2, environ=NO_ENV)
    assert spent["budget"]["stoppedBecause"] == "budget"
    with pytest.raises(PlanRefused, match="search budget is spent: 2 of 2"):
        check_directly(spent, "check_scam_reports", fake(), "human")

    low = fake(account={**FAKE_ACCOUNT, "plan_searches_left": 5})
    case = open_sample("b")
    with pytest.raises(PlanRefused, match="quota guard: 5 searches left this month, below the"):
        check_directly(case, "check_office", low, "human")

    unconfirmed = open_sample("c", confirm=False)
    with pytest.raises(PlanRefused, match="not confirmed"):
        check_directly(unconfirmed, "check_scam_reports", fake(), "human")
    assert low.calls == [] and unconfirmed["trace"] == [] and case["trace"] == []


def test_a_check_run_again_replaces_its_earlier_finding():
    provider = fake()
    case = open_sample("b")
    investigate(case, provider, environ=NO_ENV)
    [first] = line(case, "check_office")["signalsAdded"]

    again = check_directly(case, "check_office", provider, "agent")

    [second] = again["signalsAdded"]
    by_id = {s["id"]: s for s in case["signals"]}
    assert by_id[first]["superseded"] == f"step {again['step']} ran check_office again"
    assert "superseded" not in by_id[second]
    assert [rule_of(case, i) for i in decide(case["signals"]).evidence_ids].count(
        "office_found"
    ) == 1


# ---- investigating a case again -------------------------------------------------------------


def test_investigating_again_checks_afresh_and_a_person_can_still_run_the_remaining_checks():
    provider = fake()
    case = open_sample("a")
    investigate(case, provider, environ=NO_ENV)
    first = len(case["trace"])

    investigate(case, provider, environ=NO_ENV)

    assert walk(case, case["trace"][first:]) == [
        ("ran", "text_rules", 0, ["fee_requested"], "unverified"),
        ("ran", "lookup_official_site", 3, ["sender_lookalike"], "unverified"),
        ("ran", "find_fraud_notice", 4, ["fee_contradicts_employer"], "high_risk"),
        ("stopped", "investigate", None, [], "high_risk"),
    ]
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 2,
        "saved": 4,
        "stoppedBecause": "decisive",
    }
    assert {s["id"] for s in case["signals"] if s.get("superseded")} == {"sig_2", "sig_3"}
    assert decide(case["signals"]).evidence_ids == ("sig_1", "sig_5")

    run_remaining_checks(case, provider, actor="human", environ=NO_ENV)
    assert [x["tool"] for x in case["trace"] if x["action"] == "ran"][-4:] == [
        "confirm_sender_domain",
        "check_job_listings",
        "check_office",
        "check_scam_reports",
    ]
    assert case["budget"] == {"maxSearches": 6, "spent": 6, "saved": 0, "stoppedBecause": "done"}


LOOKALIKE_LINK = """Dear Candidate,

Greetings from Brand! You are shortlisted for the post of Data Entry Executive (WFH) with our \
Noida team.

Salary: ₹18,000/month

Send photos of your Aadhaar card and PAN card before the interview to hr.brand.desk@gmail.com \
and apply at https://brand-hiring.example/apply

Regards,
Brand HR"""


def test_a_lookalike_an_earlier_investigation_left_open_is_confirmed_before_a_decisive_stop():
    provider = fake(
        (
            confirm_sender_domain_params("brand.example", "brand-hiring.example", "Noida"),
            "google/confirm_second_official.json",
        ),
    )
    case = open_text(LOOKALIKE_LINK)
    investigate(case, provider, max_searches=2, environ=NO_ENV)
    assert case["budget"]["stoppedBecause"] == "budget"
    assert decide(case["signals"]).band == "high_risk"
    first = len(case["trace"])

    investigate(case, provider, environ=NO_ENV)

    # The earlier look-alike is not taken as settled: step 1 runs again, and the confirming
    # search withdraws it before anything is decisive.
    assert [x for x in walk(case, case["trace"][first:]) if x[0] == "ran"] == [
        ("ran", "text_rules", 0, ["sensitive_docs_early"], "unverified"),
        ("ran", "lookup_official_site", 3, ["sender_free_mail", "sender_lookalike"], "high_risk"),
        ("ran", "find_fraud_notice", 4, [], "high_risk"),
        ("ran", "confirm_sender_domain", 5, ["sender_official"], "unverified"),
        ("ran", "check_job_listings", 6, ["no_listing_match"], "unverified"),
        ("ran", "check_office", 7, ["office_not_found"], "high_risk"),
    ]
    assert case["budget"] == {
        "maxSearches": 6,
        "spent": 5,
        "saved": 1,
        "stoppedBecause": "decisive",
    }
    assert stop(case)["because"].endswith("so 1 search was not spent")


def test_a_person_who_says_a_fee_is_asked_after_all_gets_the_fee_red_flag_back():
    case = open_sample("a")
    [hit] = text_rules(case["sourceText"])
    case["signals"] = [{**text_signal(hit, "sig_1"), "stale": True}]

    investigate(case, fake(), environ=NO_ENV)

    fee = [s for s in case["signals"] if s["rule"] == "fee_requested"]
    assert [(s["id"], s["stale"], s.get("superseded")) for s in fee] == [
        ("sig_1", True, "fired again at step 0"),
        ("sig_2", False, None),
    ]
    assert case["trace"][0]["signalsAdded"] == ["sig_2"]
    assert decide(case["signals"]).band == "high_risk"
    assert "fee_requested set aside" not in " ".join(decide(case["signals"]).reasons)


def test_run_remaining_checks_refuses_claims_changed_since_the_decisive_stop():
    provider = fake()
    case = open_sample("a")
    investigate(case, provider, environ=NO_ENV)

    case["claims"]["role"]["confirmed"] = False
    with pytest.raises(PlanRefused, match="not confirmed"):
        run_remaining_checks(case, provider, actor="human", environ=NO_ENV)
    case["claims"]["role"]["confirmed"] = True

    case["signals"][1]["stale"] = True  # the look-alike step 1 found, after a company fix
    before, calls = json.dumps(case, sort_keys=True), len(provider.calls)
    with pytest.raises(PlanRefused, match="no longer stands: investigate the case again"):
        run_remaining_checks(case, provider, actor="human", environ=NO_ENV)
    assert json.dumps(case, sort_keys=True) == before and len(provider.calls) == calls


def test_a_reused_case_cites_each_finding_by_the_step_that_found_it():
    earlier = open_sample("a", "case_001")
    investigate(earlier, fake(), environ=NO_ENV)
    investigate(earlier, fake(), environ=NO_ENV)

    case = open_sample("a-forwarded", "case_002")
    investigate(case, fake(), same_as=earlier, environ=NO_ENV)

    copied = {s["id"] for s in case["signals"] if s.get("reusedFrom")}
    assert {rule_of(case, i) for i in copied} == {"sender_lookalike", "fee_contradicts_employer"}
    numbered = {sid for x in case["trace"] if x["step"] is not None for sid in x["signalsAdded"]}
    assert copied <= numbered
    assert decide(case["signals"]).band == "high_risk"


# ---- replay and the live cache -------------------------------------------------------------


def test_a_replayed_investigation_matches_and_marks_every_search_replay(tmp_path):
    for route in ROUTES:
        if route["sample"] == "a":
            write_recording(
                "a",
                route["params"],
                fixture(route["fixture"]),
                recorded_at=RECORDED_AT,
                recordings_dir=tmp_path,
            )
    served = open_sample("a")
    investigate(served, fake(), environ=NO_ENV)
    replayed = open_sample("a")
    investigate(replayed, ReplaySearchProvider(tmp_path), environ=NO_ENV)

    assert walk(replayed) == walk(served)
    assert replayed["budget"] == served["budget"]
    searches = [x for x in replayed["trace"] if x["engine"]]
    assert {(x["provider"], x["cache"]) for x in searches} == {("replay", "replay")}


ACCOUNT = {
    "account_email": "owner@example.com",
    "plan_searches_left": 200,
    "searches_per_month": 250,
    "this_month_usage": 50,
    "this_hour_searches": 0,
    "account_rate_limit_per_hour": 50,
}


def test_a_local_cache_hit_costs_nothing_and_the_key_stays_out_of_the_case(tmp_path, serpapi_stub):
    provider = SerpApiSearchProvider(
        client=serpapi_stub.client, cache_dir=tmp_path / "cache", no_cache=False
    )
    [lookup, notice] = [fixture(r["fixture"]) for r in ROUTES if r["sample"] == "a"][:2]
    serpapi_stub.reply(ACCOUNT).reply(lookup).reply(notice).reply(ACCOUNT)

    first = open_sample("a", "case_001")
    investigate(first, provider, environ=NO_ENV)
    second = open_sample("a", "case_002")
    investigate(second, provider, environ=NO_ENV)

    assert len(serpapi_stub.urls) == 4
    firsts = [x for x in first["trace"] if x["engine"]]
    seconds = [x for x in second["trace"] if x["engine"]]
    assert [(x["cache"], x["searchesSpent"]) for x in firsts] == [("miss", 1), ("miss", 1)]
    assert [(x["cache"], x["searchesSpent"]) for x in seconds] == [("hit", 0), ("hit", 0)]
    assert second["budget"] == {
        "maxSearches": 6,
        "spent": 0,
        "saved": 6,
        "stoppedBecause": "decisive",
    }
    for case in (first, second):
        text = json.dumps(case)
        assert serpapi_stub.api_key not in text and "api_key" not in text
        assert "owner@example.com" not in text


def test_a_retrieval_date_rides_on_every_search_signal():
    case = open_sample("a")
    investigate(case, fake(), environ=NO_ENV)
    when = datetime.fromtimestamp(NOW, UTC).isoformat()
    assert {s["evidence"]["retrievedAt"] for s in case["signals"][1:]} == {when}
