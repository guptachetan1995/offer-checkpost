"""The recordings: the three demo samples replayed end to end from ``recordings/`` alone, as a
judge without a SerpApi key runs them; which other samples replay answers; and every recorded
response checked for the fields its reader needs and for the scrub.

Each recording is a real SerpApi response, recorded once by ``python -m offer_checkpost record``
and dated. No fixture stands in for one here, and nothing reaches the network: the socket guard
in ``conftest.py`` fails any test that tries."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from offer_checkpost import cli
from offer_checkpost.checks import (
    FRAUD_NOTICE_TERMS,
    may_report_fake_offers,
    read_fraud_notice,
    read_job_listings,
    read_office,
    read_scam_reports,
)
from offer_checkpost.domains import registrable_domain
from offer_checkpost.providers import (
    RECORDINGS_DIR,
    ReplaySearchProvider,
    cache_key,
    params_key,
)
from offer_checkpost.salary import listing_pay
from offer_checkpost.scrub import WHITELIST, is_people_profile, is_video_page, scrub
from offer_checkpost.store import Store
from test_no_secrets import key_shaped, personal_data

ENTRY = Path(__file__).resolve().parents[1]
SAMPLES = ENTRY / "samples" / "offers"
RECORDED_ON = "2026-09-29"
RECORDINGS = sorted(RECORDINGS_DIR.glob("*/*.json"))
# Keys that would identify a person: a reviewer, or a profile picture.
PERSON_KEYS = {"user", "profile", "thumbnail", "contributor_id"}
AT = f"{RECORDED_ON}T12:00:00+05:30"
REPLAYED = "(replay, cache replay) · 1 search · agent"
ASKED = "(replay, cache replay) · 1 search · human"


def recording(sample: str, engine: str, q: str) -> dict:
    """The one recording of ``sample`` for ``engine`` whose query is ``q``."""
    [found] = [
        record
        for path in RECORDINGS_DIR.glob(f"{sample}/{engine}-*.json")
        if (record := json.loads(path.read_text(encoding="utf-8")))["params"]["q"] == q
    ]
    return found


def replayed(capsys, name: str) -> tuple[int, str, str]:
    """``python -m offer_checkpost investigate samples/offers/<name>.txt --provider replay``."""
    code = cli.main(["investigate", str(SAMPLES / f"{name}.txt"), "--provider", "replay"], {})
    out, err = capsys.readouterr()
    return code, out, err


def section(out: str, title: str) -> list[str]:
    lines = out.split("\n")
    start = lines.index(title) + 1
    return lines[start : lines.index("", start)]


def headings(out: str) -> list[str]:
    return [line for line in section(out, "Trace") if not line.startswith(" ")]


def all_keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in all_keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in all_keys(v)}
    return set()


def all_links(node) -> list[str]:
    if isinstance(node, dict):
        own = [node["link"]] if isinstance(node.get("link"), str) else []
        return own + [link for v in node.values() for link in all_links(v)]
    if isinstance(node, list):
        return [link for v in node for link in all_links(v)]
    return []


# ---- the demo, replayed ---------------------------------------------------------------------


def test_the_recordings_are_the_three_demo_samples_recorded_on_one_day():
    assert sorted({p.parent.name for p in RECORDINGS}) == ["a", "b", "c"]
    assert [p.parent.name for p in RECORDINGS].count("a") == 6
    assert [p.parent.name for p in RECORDINGS].count("b") == 4
    assert [p.parent.name for p in RECORDINGS].count("c") == 4
    assert ReplaySearchProvider().recorded_dates == (RECORDED_ON,)
    notice = " ".join((RECORDINGS_DIR / "NOTICE.md").read_text(encoding="utf-8").split())
    for says in (
        "not covered by this repository's MIT licence",
        "Results that tend to name people are dropped",
        # The one search the app's 20 s timeout of the day could not have fetched.
        "sent with a longer client timeout, and with `no_cache`",
    ):
        assert says in notice, says


def test_sample_a_replays_to_high_risk_on_hcltechs_own_notice(capsys, no_network):
    code, out, err = replayed(capsys, "a")

    assert (code, err) == (0, "")
    assert out.split("\n")[0].endswith(
        "a.txt · provider replay (recorded SerpApi responses: 2026-09-29; not live) · as human"
    )
    assert headings(out) == [
        "step 0   text_rules · agent",
        f"step 1   lookup_official_site · google {REPLAYED}",
        f"step 2   find_fraud_notice · google {REPLAYED}",
        "stop     investigate · agent",
    ]
    trace = " ".join(" ".join(section(out, "Trace")).split())
    for says in (
        "signal sig_1 fee_requested (red, strong)",
        "found official domain hcltech.com (top organic result); recruiter domains: "
        "hcltech-careers.example lookalike",
        "signal sig_2 sender_lookalike (red, moderate)",
        'found hcltech.com has a recruitment-fraud notice: "never ask for recruitment fees"',
        "signal sig_3 fee_contradicts_employer (red, strong)",
    ):
        assert says in trace, says
    assert "band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive" in out
    verdict = " ".join(
        " ".join(section(out, "Draft verdict (a draft: only a person publishes)")).split()
    )
    # The employer's own words, quoted from its own site, dated when SerpApi returned them.
    assert "https://freshers.hcltech.com/ (retrieved 29 Sep 2026)" in verdict
    assert "we never ask for recruitment fees" in verdict
    assert verdict.endswith("Searches: 2 spent, 4 not spent: the evidence was decisive.")
    assert no_network == []


def test_sample_b_replays_to_consistent_with_genuine_on_four_real_searches(capsys, no_network):
    code, out, err = replayed(capsys, "b")

    assert (code, err) == (0, "")
    assert headings(out) == [
        "step 0   text_rules · agent",
        f"step 1   lookup_official_site · google {REPLAYED}",
        "skip     find_fraud_notice · agent",
        "skip     confirm_sender_domain · agent",
        f"step 2   check_job_listings · google_jobs {REPLAYED}",
        f"step 3   check_office · google_maps {REPLAYED}",
        "skip     scan_office_reviews · agent",
        f"step 4   check_scam_reports · google_news {REPLAYED}",
        "skip     check_contact_footprint · agent",
        "stop     investigate · agent",
    ]
    trace = " ".join(" ".join(section(out, "Trace")).split())
    # Google gave no knowledge graph, so sender_official can't fire; the link is on
    # siemens.com all the same, and with no fee asked a notice would have nothing to contradict.
    assert "not on the official domain" not in trace
    for says in (
        "found no text rule fired",
        "recruiter domains: siemens.com subdomain",
        "because no fee or sensitive documents were asked for, and no recruiter email or link is "
        "off siemens.com",
        "Siemens's listing applies on siemens.com",
        "signal sig_1 listing_match (green)",
        "found Siemens is on Maps in Bengaluru",
        "signal sig_2 office_found (green)",
        "found no news reports of fake offers in Siemens's name",
        "because no recruiter phone or email was extracted",
    ):
        assert says in trace, says
    assert "band consistent_with_genuine · 4 searches spent of 6 · stopped: done" in out
    verdict = " ".join(
        " ".join(section(out, "Draft verdict (a draft: only a person publishes)")).split()
    )
    assert "https://jobs.sw.siemens.com/bangalore-ind/application-support-engineer/" in verdict
    assert verdict.endswith("Searches: 4 spent: every check that applied has run.")
    assert no_network == []


def test_sample_c_replays_to_high_risk_and_its_remaining_checks_too(capsys, no_network):
    code, out, err = replayed(capsys, "c")

    assert (code, err) == (0, "")
    assert headings(out) == [
        "step 0   text_rules · agent",
        f"step 1   lookup_official_site · google {REPLAYED}",
        "reorder  check_office · agent",
        "skip     find_fraud_notice · agent",
        "skip     confirm_sender_domain · agent",
        f"step 2   check_office · google_maps {REPLAYED}",
        "stop     investigate · agent",
    ]
    trace = " ".join(" ".join(section(out, "Trace")).split())
    for says in (
        "signal sig_1 sensitive_docs_early (red, moderate)",
        "signal sig_2 chat_only_interview (red, weak)",
        "found no official domain found: no web footprint",
        "because R1, unknown firm: no_web_footprint fired",
        "found no place named Kavrellon Support Services in Indore among 0 Maps results",
        "signal sig_4 office_not_found (red, moderate)",
    ):
        assert says in trace, says
    assert "band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive" in out

    # The person asks for the checks the decisive stop saved, as the recorder did.
    text = (SAMPLES / "c.txt").read_text(encoding="utf-8")
    played = cli.play(Store(ReplaySearchProvider()), text, remaining=True)
    assert played.error is None
    assert [f"{verb}: {cli.budget_line(o)}" for verb, o in played.outcomes] == [
        "investigate: band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive",
        "run_remaining_checks: band high_risk · 4 searches spent of 6 · stopped: done",
    ]
    lines = cli.trace_lines(played.outcomes[-1][1])
    assert [line for line in lines if not line.startswith(" ")][7:] == [
        "step 3   check_job_listings · google_jobs (replay, cache replay) · 1 search · human",
        "skip     scan_office_reviews · human",
        "step 4   check_scam_reports · google_news (replay, cache replay) · 1 search · human",
        "skip     check_contact_footprint · human",
        "skip     check_contact_footprint · human",
        "stop     run_remaining_checks · human",
    ]
    joined = " ".join(" ".join(lines).split())
    assert (
        "found 10 listings; no listing by Kavrellon Support Services for this role; pay found in "
        "6 of 10 listings, median ₹17,292 a month"
    ) in joined
    assert "signal sig_5 no_listing_match (red, weak)" in joined
    assert "signal sig_6 pay_outlier (red, moderate)" in joined
    assert "found no news reports of fake offers in Kavrellon Support Services's name" in joined
    assert no_network == []


@pytest.mark.parametrize("name", ["a", "a-forwarded"])
def test_sample_as_remaining_checks_replay_and_spend_the_four_saved(name, no_network):
    """Sample A's decisive stop saves 4 searches and the page offers them. Replayed, as the
    recorder ran them: the look-alike is neither cleared nor named, HCLTech has no such
    listing in Noida but an office there, its reviews filtered on "fee" come back empty, and
    the budget is spent before the scam-report search. The band stays high risk. The forwarded
    copy makes the same searches, so it replays the same way."""
    played = cli.play(
        Store(ReplaySearchProvider()),
        (SAMPLES / f"{name}.txt").read_text(encoding="utf-8"),
        remaining=True,
    )
    assert played.error is None
    assert [f"{verb}: {cli.budget_line(o)}" for verb, o in played.outcomes] == [
        "investigate: band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive",
        "run_remaining_checks: band high_risk · 6 searches spent of 6 · stopped: budget",
    ]
    outcome = played.outcomes[-1][1]
    lines = cli.trace_lines(outcome)
    assert [line for line in lines if not line.startswith(" ")][4:] == [
        f"step 3   confirm_sender_domain · google {ASKED}",
        f"step 4   check_job_listings · google_jobs {ASKED}",
        f"step 5   check_office · google_maps {ASKED}",
        f"step 6   scan_office_reviews · google_maps_reviews {ASKED}",
        "stop     run_remaining_checks · human",
    ]
    joined = " ".join(" ".join(lines).split())
    for says in (
        "found hcltech.com neither clears hcltech-careers.example nor names it as fake: "
        "sender_lookalike stays moderate",
        "found 0 listings; no listing by HCLTech for this role; pay found in 0 of 0 listings: "
        "not enough pay data",
        "signal sig_4 no_listing_match (red, weak)",
        "found HCLTech is on Maps in Noida",
        "signal sig_5 office_found (green)",
        "because run on request after a decisive result: a fee was asked and check_office found "
        "HCLTech: its reviews are scanned for 'fee'",
        "found 0 of 0 reviews mention 'fee'",
        "because the search budget is spent: 6 of 6",
    ):
        assert says in joined, says
    assert [(s["rule"], s["stale"]) for s in outcome["signals"]] == [
        ("fee_requested", False),
        ("sender_lookalike", False),
        ("fee_contradicts_employer", False),
        ("no_listing_match", False),
        ("office_found", False),
    ]
    assert outcome["band"] == "high_risk"
    ran = [line["tool"] for line in outcome["trace"] if line["cache"] == "replay"]
    assert "check_scam_reports" not in ran and len(ran) == 6
    assert no_network == []


# What replay answers with no key, sample by sample, as the README says: the demo samples and
# Sample A forwarded again end to end, the samples that name no company on their text rules
# alone, and every other sample not at all, failing its first search and making nothing up.
REPLAYED_END_TO_END = {"a.txt", "a-forwarded.txt", "b.txt", "c.txt"}
NO_COMPANY = {
    "no-company-training-fee.txt",
    "task-daily-earning.txt",
    "task-hinglish-no-claims.txt",
    "task-hinglish-reviews.txt",
    "task-per-like.txt",
    "task-prepaid-deposit.txt",
}


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("*.txt")))
def test_replay_answers_the_demo_samples_and_says_which_others_need_a_key(name, no_network):
    played = cli.play(
        Store(ReplaySearchProvider()),
        (SAMPLES / name).read_text(encoding="utf-8"),
        remaining=False,
    )
    [(_, outcome)] = played.outcomes
    if name in REPLAYED_END_TO_END:
        assert played.error is None
        assert outcome["budget"]["stoppedBecause"] in ("decisive", "done")
    elif name in NO_COMPANY:
        assert played.error is None
        assert (outcome["budget"]["stoppedBecause"], outcome["budget"]["spent"]) == (
            "no_company",
            0,
        )
    else:
        assert played.error.startswith("investigate stopped: ")
        [failed] = [line for line in outcome["trace"] if line["action"] == "failed"]
        assert failed["note"].startswith("no recorded google response for this query")
    assert no_network == []


def test_the_demo_searches_are_exactly_the_recordings():
    """Every search the three samples make is recorded under its own sample, and every
    recording is one of them: nothing stale, nothing missing."""
    sent = {}
    for name in "abc":
        played = cli.play(
            Store(ReplaySearchProvider()),
            (SAMPLES / f"{name}.txt").read_text(encoding="utf-8"),
            remaining=name in cli.REMAINING_CHECKS_SAMPLES,
        )
        assert played.error is None, name
        for line in played.outcomes[-1][1]["trace"]:
            if line["cache"] == "replay":
                sent[params_key(line["params"])] = name
    recorded = {}
    for path in RECORDINGS:
        record = json.loads(path.read_text(encoding="utf-8"))
        recorded[params_key(record["params"])] = path.parent.name
    assert sent == recorded
    assert len(recorded) == 14


# ---- what each reader needs, in the real responses -------------------------------------------


def test_the_official_site_lookups_carry_organic_links_on_the_employers_domain():
    for sample, company, domain in (
        ("a", "HCLTech", "hcltech.com"),
        ("b", "Siemens", "siemens.com"),
    ):
        response = recording(sample, "google", company)["response"]
        # Recorded with a city, Google answers a brand with a local panel, not a knowledge
        # graph with a website: the reader falls back to the top organic result.
        assert not response.get("knowledge_graph", {}).get("website")
        organic = response["organic_results"]
        assert organic and all(r.get("title") and r.get("link") for r in organic)
        assert registrable_domain(organic[0]["link"]) == domain
    # The invented firm has no web footprint at all.
    assert recording("c", "google", "Kavrellon Support Services")["response"] == {}


def test_sample_as_fraud_notice_search_quotes_the_fee_phrase_from_hcltechs_own_site():
    record = recording(
        "a",
        "google",
        f"site:hcltech.com {FRAUD_NOTICE_TERMS}",
    )
    organic = record["response"]["organic_results"]
    # site: was honoured: every result is on the official domain, with a title and snippet.
    assert all(registrable_domain(r["link"]) == "hcltech.com" for r in organic)
    assert all(r.get("title") and r.get("snippet") for r in organic)
    # The first result carrying the fee phrase is the one quoted; the notice page above it
    # names no fee in its snippet.
    quoted = next(r for r in organic if "never ask for recruitment fees" in r["snippet"])
    assert quoted["link"] == "https://freshers.hcltech.com/"

    reading = read_fraud_notice(
        record["response"],
        params=record["params"],
        retrieved_at=AT,
        official_domain="hcltech.com",
        fee_requested=True,
    )
    [signal] = reading.signals
    assert signal["rule"] == "fee_contradicts_employer"
    assert signal["evidence"]["link"] == quoted["link"]
    assert signal["evidence"]["snippet"] == quoted["snippet"]


def test_the_job_searches_carry_apply_options_and_pay_the_benchmark_reads():
    for sample, q in (
        ("b", "Application Support Engineer Siemens"),
        ("c", "Customer Support Executive"),
    ):
        jobs = recording(sample, "google_jobs", q)["response"]["jobs_results"]
        assert len(jobs) == 10
        for job in jobs:
            assert job["title"] and job["company_name"] and job["location"]
            assert job["apply_options"] and all(o["link"] for o in job["apply_options"])
            assert isinstance(job["extensions"], list)
        # Google Jobs sent no detected_extensions: pay is in the extensions' text.
        assert not any("detected_extensions" in job for job in jobs)
        paid = [pay for pay in map(listing_pay, jobs) if pay]
        assert paid and any(pay.source == "extensions" and "₹" in pay.raw for pay in paid)

    b = recording("b", "google_jobs", "Application Support Engineer Siemens")
    [listing] = [
        j for j in b["response"]["jobs_results"] if j["title"] == "Application Support Engineer"
    ]
    assert listing["company_name"] == "Siemens" and listing["location"] == "Bengaluru, Karnataka"
    assert listing["apply_options"][0]["link"].startswith(
        "https://jobs.sw.siemens.com/bangalore-ind/application-support-engineer/"
    )
    matched = read_job_listings(
        b["response"],
        params=b["params"],
        retrieved_at=AT,
        company="Siemens",
        role="Application Support Engineer",
        city="Bengaluru",
        official_domain="siemens.com",
        offered_monthly_inr=None,
    )
    assert [s["rule"] for s in matched.signals] == ["listing_match"]

    c = recording("c", "google_jobs", "Customer Support Executive")
    benchmark = read_job_listings(
        c["response"],
        params=c["params"],
        retrieved_at=AT,
        company="Kavrellon Support Services Pvt. Ltd",
        role="Customer Support Executive",
        city="Indore",
        official_domain=None,
        offered_monthly_inr=42_000,
    )
    assert [s["rule"] for s in benchmark.signals] == ["no_listing_match", "pay_outlier"]
    # Virality School's "Annual CTC: ₹1.40 LPA – ₹1.80 LPA" is a year's pay: ₹13,333 a month.
    assert (benchmark.facts["payFound"], benchmark.facts["medianMonthlyInr"]) == (6, 17_291.5)


def test_the_maps_searches_carry_places_with_ids_and_addresses():
    b = recording("b", "google_maps", "Siemens")
    places = b["response"]["local_results"]
    assert places and "place_results" not in b["response"]
    for place in places:
        assert place["title"] and place["place_id"] and place["address"]
        # A list of places gives each one's type as text (an exact match, as a list).
        assert isinstance(place.get("type", ""), str)
    reading = read_office(
        b["response"], params=b["params"], retrieved_at=AT, company="Siemens", city="Bengaluru"
    )
    [signal] = reading.signals
    assert signal["rule"] == "office_found"
    assert signal["evidence"]["snippet"].endswith("Bengaluru, Karnataka 560055")

    # Sample A's remaining checks find HCLTech's campus in Noida, and the reviews search is
    # scoped to that place: filtered on "fee", it comes back empty.
    a = recording("a", "google_maps", "HCLTech")
    reading = read_office(
        a["response"], params=a["params"], retrieved_at=AT, company="HCLTech", city="Noida"
    )
    [signal] = reading.signals
    assert signal["rule"] == "office_found"
    assert signal["evidence"]["snippet"].endswith("Noida, Uttar Pradesh 201303")
    [reviews] = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in RECORDINGS_DIR.glob("a/google_maps_reviews-*.json")
    ]
    assert reviews["params"]["place_id"] == reading.facts["place_id"]
    assert (reviews["params"]["query"], reviews["response"]) == ("fee", {})

    c = recording("c", "google_maps", "Kavrellon Support Services")
    assert c["response"] == {}
    reading = read_office(
        c["response"],
        params=c["params"],
        retrieved_at=AT,
        company="Kavrellon Support Services Pvt. Ltd",
        city="Indore",
    )
    assert [s["rule"] for s in reading.signals] == ["office_not_found"]


def test_the_news_searches_keep_only_headlines_that_may_report_fake_offers():
    record = recording("b", "google_news", '"Siemens" job offer (fake OR scam OR fraud)')
    results = record["response"]["news_results"]
    # Google News answered with job-scam news, none of it in Siemens's name: the scrub kept the
    # one headline with a fraud term and a job word, and dropped the rest unread.
    assert len(results) == 1
    for result in results:
        assert result["title"] and result["link"] and result["date"]
        assert result["source"]["name"]
        assert may_report_fake_offers(result["title"])
    reading = read_scam_reports(
        record["response"], params=record["params"], retrieved_at=AT, company="Siemens"
    )
    assert (reading.signals, reading.facts["reports"]) == ((), [])
    empty = recording(
        "c", "google_news", '"Kavrellon Support Services" job offer (fake OR scam OR fraud)'
    )
    assert empty["response"] == {}


# ---- every recording, scrubbed and dated -----------------------------------------------------


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_recording_is_scrubbed_and_dated(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    engine, params, response = record["engine"], record["params"], record["response"]

    assert set(record) == {"recordedAt", "engine", "params", "response"}
    assert params["engine"] == engine
    assert not {"api_key", "no_cache"} & set(params)
    assert path.name == f"{engine}-{cache_key(params)[:12]}.json"
    when = datetime.fromisoformat(record["recordedAt"])
    assert when.utcoffset() == timedelta(hours=5, minutes=30)
    assert record["recordedAt"].startswith(RECORDED_ON)

    # Scrubbing it again changes nothing: only the reader's fields, cleaned.
    assert scrub(engine, response) == response
    top = {field.split(".")[0].removesuffix("[]") for field in WHITELIST[engine]}
    assert set(response) <= top
    assert key_shaped(path) == []
    assert personal_data(path) == []
    assert not PERSON_KEYS & all_keys(response)
    assert not [link for link in all_links(response) if is_people_profile(link)]
    # Video descriptions and news no reader counts name people: neither is kept.
    assert not [link for link in all_links(response) if is_video_page(link)]
    assert all(may_report_fake_offers(n["title"]) for n in response.get("news_results", []))
    text = json.dumps(response, ensure_ascii=False)
    assert not re.search(r"\b[0-9a-f]{64}\b", text)
    assert "api_key" not in text
