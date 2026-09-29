"""The drafts for Samples A, B and C. Each case is shaped like the store's: claims from the
sample text (confirmed), the text-rule signals ``open_case`` adds, then the signals the real
readers fire on the synthetic fixtures, each added by a trace step written by hand the way the
planner writes one (``step``, ``tool``, ``actor``, ``because``, ``engine``, ``params``,
``cache``, ``ms``, ``searchesSpent``, ``signalsAdded``). The paths are the planner's expected
ones: A stops decisive after 2 searches, B spends 4 with the fraud notice skipped, and C stops
decisive after 2 and runs 2 more when a person asks."""

import json
import re
from pathlib import Path

import pytest

from offer_checkpost import drafts, planner
from offer_checkpost.checks import (
    check_job_listings_params,
    check_office_params,
    check_scam_reports_params,
    confirm_sender_domain_params,
    find_fraud_notice_params,
    lookup_official_site_params,
    read_confirm_sender_domain,
    read_fraud_notice,
    read_job_listings,
    read_office,
    read_official_site,
    read_scam_reports,
)
from offer_checkpost.extract import extract_claims, text_rules
from offer_checkpost.providers import FakeSearchProvider
from offer_checkpost.rules import RULES, signal_for, text_signal

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples" / "offers"
# The synthetic stand-ins of the demo samples, which the fake provider's fixtures answer; any
# other sample is read from samples/offers.
STAND_INS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "offers"
NOW = 1_791_000_000.0  # 3 Oct 2026, 04:00 UTC: when the fake provider says it searched
CREATED_AT = "2026-10-06T10:02:11+05:30"
DRAFTED_AT = "2026-10-06T10:03:00+05:30"
PUBLISHED_AT = "2026-10-06T10:05:30+05:30"
MAX_SEARCHES = 6
ON_REQUEST = "run on request after a decisive result"
URL = re.compile(r"https?://[^\s\"'<>)]+")
RUPEES = re.compile(r"₹[\d,]+")
NEVER_SAID = re.compile(r"\b(?:genuine|safe)\b", re.I)


class Builder:
    def __init__(self, sample: str, case_id: str = "case_001"):
        stand_in = STAND_INS / f"{sample}.txt"
        path = stand_in if stand_in.exists() else SAMPLES_DIR / f"{sample}.txt"
        text = path.read_text(encoding="utf-8")
        claims = extract_claims(text)
        for name in ("company", "role", "city", "pay", "fee"):
            if claims[name] is not None:
                claims[name]["confirmed"] = True
        self.provider = FakeSearchProvider.from_fixtures(clock=lambda: NOW)
        self.steps = 0
        self.case = {
            "id": case_id,
            "createdAt": CREATED_AT,
            "sourceText": text,
            "fingerprint": "not read by the drafts",
            "sameAs": None,
            "claims": claims,
            "revision": 0,
            "signals": [],
            "trace": [],
            "budget": {
                "maxSearches": MAX_SEARCHES,
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
        self._add(text_signal(hit, None) for hit in text_rules(text))

    def claim(self, name):
        return self.case["claims"][name]["value"]

    def run(self, tool, because, params, read, *, actor="agent", **context):
        result = self.provider.search(params)
        reading = read(
            result.data, params=result.params, retrieved_at=result.retrieved_at, **context
        )
        self.steps += 1
        self.case["trace"].append(
            {
                "step": self.steps,
                "tool": tool,
                "actor": actor,
                "because": because,
                "engine": result.engine,
                "params": result.params,
                "cache": result.cache,
                "ms": result.ms,
                "searchesSpent": result.searches_spent,
                "signalsAdded": self._add(reading.signals),
            }
        )
        self.case["budget"]["spent"] += result.searches_spent
        self.case["status"] = "investigated"
        return reading

    def skip(self, tool, because):
        self.case["trace"].append(
            {"tool": tool, "actor": "agent", "skipped": True, "because": because}
        )

    def stop(self, because):
        budget = self.case["budget"]
        budget["stoppedBecause"] = because
        budget["saved"] = budget["maxSearches"] - budget["spent"]

    def _add(self, signals):
        ids = []
        for s in signals:
            ids.append(f"sig_{len(self.case['signals']) + 1}")
            self.case["signals"].append({**s, "id": ids[-1]})
        return ids

    def step1(self):
        company, city = self.claim("company"), self.claim("city")
        return self.run(
            "lookup_official_site",
            "a company is claimed",
            lookup_official_site_params(company, city),
            read_official_site,
            company=company,
            contacts=self.case["claims"]["contacts"],
            links=self.case["claims"]["links"],
        ).facts["officialDomain"]


def sample_a(*, confirm=False):
    b = Builder("a")
    official, city = b.step1(), b.claim("city")
    b.run(
        "find_fraud_notice",
        f"a fee was asked and step 1 named the official domain {official}",
        find_fraud_notice_params(official, city),
        read_fraud_notice,
        official_domain=official,
        fee_requested=True,
    )
    b.stop("decisive")
    if confirm:
        b.run(
            "confirm_sender_domain",
            ON_REQUEST,
            confirm_sender_domain_params(official, "brand-careers.example", city),
            read_confirm_sender_domain,
            actor="human",
            official_domain=official,
            candidate="brand-careers.example",
            contacts=b.case["claims"]["contacts"],
        )
    return b.case


def sample_b():
    b = Builder("b")
    company, role, city = b.claim("company"), b.claim("role"), b.claim("city")
    official = b.step1()
    b.skip(
        "find_fraud_notice",
        "the sender is on the official domain and asks for no fee, so there is nothing for a "
        "fraud notice to contradict",
    )
    b.run(
        "check_job_listings",
        "R2: a listing with an apply option on the official domain is the strongest green",
        check_job_listings_params(role, city, company),
        read_job_listings,
        company=company,
        role=role,
        city=city,
        official_domain=official,
        offered_monthly_inr=b.case["claims"]["pay"]["monthlyInr"],
    )
    b.run(
        "check_office",
        "a city is claimed",
        check_office_params(company, city),
        read_office,
        company=company,
        city=city,
    )
    b.run(
        "check_scam_reports",
        "a company is claimed",
        check_scam_reports_params(company),
        read_scam_reports,
        company=company,
    )
    return b.case


def sample_c(*, remaining=False):
    b = Builder("c")
    company, role, city = b.claim("company"), b.claim("role"), b.claim("city")
    b.step1()
    b.run(
        "check_office",
        "R1: an unknown firm's office is the cheapest claim to disprove",
        check_office_params(company, city),
        read_office,
        company=company,
        city=city,
    )
    b.stop("decisive")
    if remaining:
        b.run(
            "check_job_listings",
            ON_REQUEST,
            check_job_listings_params(role, city),
            read_job_listings,
            actor="human",
            company=company,
            role=role,
            city=city,
            official_domain=None,
            offered_monthly_inr=b.case["claims"]["pay"]["monthlyInr"],
        )
        b.run(
            "check_scam_reports",
            ON_REQUEST,
            check_scam_reports_params(company),
            read_scam_reports,
            actor="human",
            company=company,
        )
        b.case["budget"].update(saved=0, stoppedBecause=None)
    return b.case


def post(case, label, note=""):
    return drafts.post(case, label=label, note=note, published_at=PUBLISHED_AT)


def every_draft(case, label="unverified_ask_questions"):
    board = [post(case, label, "Checked for the final-year batch.")]
    return {
        "verdict": drafts.verdict(case, drafted_at=DRAFTED_AT)["summary"],
        "reply": drafts.recruiter_reply(case, drafted_at=DRAFTED_AT)["text"],
        "report": drafts.cybercrime_report(case, drafted_at=DRAFTED_AT)["text"],
        "whatsapp": board[0]["whatsapp"],
        "board": drafts.board_html(board, generated_at=PUBLISHED_AT),
    }


CASES = {
    "a": sample_a,
    "a_confirmed": lambda: sample_a(confirm=True),
    "b": sample_b,
    "c": sample_c,
    "c_remaining": lambda: sample_c(remaining=True),
}


# ---- Sample A -------------------------------------------------------------------------------


def test_a_verdict_cites_the_fee_and_the_employers_own_notice_with_four_searches_saved():
    case = sample_a()
    draft = drafts.verdict(case, drafted_at=DRAFTED_AT)
    assert (draft["band"], draft["evidenceIds"], draft["draftedAt"]) == (
        "high_risk",
        ["sig_1", "sig_3"],
        DRAFTED_AT,
    )
    assert draft["summary"].split("\n") == [
        "2 strong red signals, 1 of them from a search result: fee asked, employer says it "
        "charges no fee.",
        "Evidence:",
        '- fee asked (the message): "To confirm your slot, pay a refundable registration fee '
        'of ₹2,499 within 2 hours."',
        "- employer says it charges no fee (step 2, find_fraud_notice on google): a fee was "
        'asked, and brand.example\'s own recruitment-fraud notice says "never charges". '
        'Source: "Beware of Fake Job Offers | Brand Careers" · "Brand never charges '
        "candidates any fee at any stage of recruitment. Fraudsters send fake offer letters "
        'from look-alike domains." · https://careers.brand.example/fraud-alert '
        "(retrieved 3 Oct 2026)",
        "Also found:",
        "- look-alike sender domain (step 1, lookup_official_site on google): the recruiter's "
        "email is on brand-careers.example, not brand.example: 'brand-careers' contains the "
        "brand token 'brand'. Source: \"Brand\" · https://www.brand.example/ "
        "(retrieved 3 Oct 2026)",
        "Searches: 2 spent, 4 not spent: the evidence was decisive.",
    ]


def test_a_reply_asks_one_question_per_red_flag_and_names_the_official_address():
    reply = drafts.recruiter_reply(sample_a(), drafted_at=DRAFTED_AT)
    assert [(q["rule"], q["signalIds"]) for q in reply["questions"]] == [
        ("fee_requested", ["sig_1"]),
        ("fee_contradicts_employer", ["sig_3"]),
        ("sender_lookalike", ["sig_2"]),
    ]
    assert reply["evidenceIds"] == ["sig_1", "sig_3", "sig_2"]
    assert reply["text"].split("\n") == [
        "Hello,",
        "",
        "Thank you for your message about the Data Entry Executive (WFH) role at Brand. Before "
        "I go any further, please answer these questions in writing:",
        "",
        '1. Your message asks for "refundable registration fee of ₹2,499". Who would receive '
        "this money: which bank account or UPI ID, and under what registered company name?",
        "2. brand.example's own site says: \"Brand never charges candidates any fee at any "
        'stage of recruitment. Fraudsters send fake offer letters from look-alike domains." '
        "(https://careers.brand.example/fraud-alert). Why does this offer ask for ₹2,499?",
        "3. Please resend this offer from your @brand.example address. Your message uses "
        "brand-careers.example.",
        "",
        "I will go ahead once these are answered.",
    ]


def test_a_cybercrime_report_says_what_was_asked_when_by_which_contact_and_the_evidence():
    case = sample_a()
    report = drafts.cybercrime_report(case, drafted_at=DRAFTED_AT)
    text = report["text"]
    assert report["evidenceIds"] == ["sig_1", "sig_3", "sig_2"]
    assert text.startswith(
        "DRAFT: summary for the National Cybercrime Helpline 1930 / cybercrime.gov.in\n"
        "Offer Checkpost drafted this and files nothing."
    )
    for line in (
        "What happened: a job offer arrived as a message. It was checked on 6 Oct 2026, "
        "10:02 IST (case case_001).",
        "What the message claims: Data Entry Executive (WFH) · in the name of Brand · Noida · "
        'pay ₹38,000/month · fee asked: "refundable registration fee of ₹2,499".',
        'Money asked for: ₹2,499 ("refundable registration fee of ₹2,499").',
        '- fee asked: "To confirm your slot, pay a refundable registration fee of ₹2,499 '
        'within 2 hours."',
        "- email: hr.onboarding@brand-careers.example",
        "Searches: 2 spent, 4 not spent: the evidence was decisive.",
        "Drafted verdict: high risk. Software drafted this from the evidence above; it is not "
        "a finding by any authority.",
    ):
        assert line in text.split("\n"), line
    searched = text.split("Red flags found by web searches:\n")[1].split("\nSearches:")[0]
    assert [line.split(" (step")[0] for line in searched.split("\n")] == [
        "- employer says it charges no fee",
        "- look-alike sender domain",
    ]
    assert "https://careers.brand.example/fraud-alert" in searched
    assert "Found consistent with the offer:" not in text
    assert text.endswith("The message as pasted:\n" + case["sourceText"])


def test_a_whatsapp_text_carries_the_label_the_note_and_the_evidence_links():
    case = sample_a()
    text = post(case, "likely_impersonation", "Do not pay. Brand's real site says no fee.")[
        "whatsapp"
    ]
    assert text.split("\n") == [
        "*Offer check: Likely impersonation*",
        "The message: Data Entry Executive (WFH) · in the name of Brand · Noida · pay "
        '₹38,000/month · fee asked: "refundable registration fee of ₹2,499"',
        "",
        "What the checks found:",
        '• fee asked: "To confirm your slot, pay a refundable registration fee of ₹2,499 '
        'within 2 hours."',
        "• employer says it charges no fee: a fee was asked, and brand.example's own "
        'recruitment-fraud notice says "never charges" https://careers.brand.example/fraud-alert',
        "",
        "Note: Do not pay. Brand's real site says no fee.",
        "",
        "This describes this message, not the company named in it.",
        "Published 6 Oct 2026, 10:05 IST by a person, from web search results checked with "
        "Offer Checkpost.",
    ]


def test_a_confirmed_on_request_cites_the_employer_naming_the_lookalike_and_sets_it_aside():
    case = sample_a(confirm=True)
    draft = drafts.verdict(case, drafted_at=DRAFTED_AT)
    assert draft["evidenceIds"] == ["sig_1", "sig_3", "sig_4"]
    lines = draft["summary"].split("\n")
    assert any(
        line.startswith(
            "- sender domain named in the employer's fraud notice (step 3, "
            "confirm_sender_domain on google): brand.example's own site names "
            "brand-careers.example next to a fraud term."
        )
        for line in lines
    )
    assert (
        "Not counted: sender_lookalike set aside: replaced by domain_named_in_fraud_notice "
        "for brand-careers.example."
    ) in lines
    assert "Also found:" not in lines

    reply = drafts.recruiter_reply(case, drafted_at=DRAFTED_AT)
    assert [q["rule"] for q in reply["questions"]] == [
        "fee_requested",
        "domain_named_in_fraud_notice",
        "fee_contradicts_employer",
    ]
    assert reply["questions"][1]["question"] == (
        "brand.example's own site says: \"brand-careers.example is a fake domain and is not "
        'affiliated with Brand. Our only recruitment site is careers.brand.example." '
        "(https://www.brand.example/fraud-alert). Please resend this offer from your "
        "@brand.example address."
    )


# ---- Sample B -------------------------------------------------------------------------------


def test_b_verdict_cites_three_greens_notes_the_weak_reports_and_never_says_genuine():
    draft = drafts.verdict(sample_b(), drafted_at=DRAFTED_AT)
    assert (draft["band"], draft["evidenceIds"]) == (
        "consistent_with_genuine",
        ["sig_1", "sig_2", "sig_3"],
    )
    lines = draft["summary"].split("\n")
    assert lines[0] == (
        "3 green signals from search results and no strong or moderate red signal: sender on "
        "the official domain, listing applies on the official domain, office found on Maps; "
        "weak red signals noted: news of fake offers in the company's name."
    )
    assert [line.split(": ")[0] for line in lines[1:]] == [
        "Evidence:",
        "- sender on the official domain (step 1, lookup_official_site on google)",
        "- listing applies on the official domain (step 2, check_job_listings on google_jobs)",
        "- office found on Maps (step 3, check_office on google_maps)",
        "Also found:",
        "- news of fake offers in the company's name (step 4, check_scam_reports on google_news)",
        "Searches",
    ]
    assert lines[-1] == "Searches: 4 spent."
    assert "https://careers.contoso.example/jobs/graduate-engineer-trainee-pune" in lines[3]
    assert "dated 08/22/2026, 09:00 AM, +0000 UTC" in lines[6]
    assert NEVER_SAID.search(draft["summary"]) is None


def test_b_reply_asks_only_about_the_weak_news_reports():
    reply = drafts.recruiter_reply(sample_b(), drafted_at=DRAFTED_AT)
    assert [(q["rule"], q["signalIds"]) for q in reply["questions"]] == [
        ("impersonation_reports", ["sig_4"])
    ]
    assert reply["questions"][0]["question"] == (
        "News reports describe fake job offers made in Contoso's name. Can you confirm that "
        "this offer comes from Contoso's own recruitment team?"
    )
    assert "about the Graduate Engineer Trainee role at Contoso." in reply["text"]
    assert "fee" not in reply["text"] and "₹" not in reply["text"]


def test_b_cybercrime_report_lists_greens_separately_and_no_money():
    text = drafts.cybercrime_report(sample_b(), drafted_at=DRAFTED_AT)["text"]
    lines = text.split("\n")
    assert "Money asked for: none named in the message." in lines
    assert lines[lines.index("Red flags in the message itself:") + 1] == "- none"
    assert lines[lines.index("Links in the message:") + 1] == (
        "- https://careers.contoso.example/jobs/graduate-engineer-trainee-pune"
    )
    green = lines.index("Found consistent with the offer:")
    assert [line.split(" (step")[0] for line in lines[green + 1 : green + 4]] == [
        "- sender on the official domain",
        "- listing applies on the official domain",
        "- office found on Maps",
    ]
    assert lines[lines.index("Red flags found by web searches:") + 1].startswith(
        "- news of fake offers in the company's name (step 4"
    )
    assert "Drafted verdict: nothing found contradicts the offer." in text


def test_b_whatsapp_text_for_no_contradictions_says_that_is_not_proof():
    case = sample_b()
    text = post(case, "no_contradictions_found")["whatsapp"]
    lines = text.split("\n")
    assert lines[0] == "*Offer check: No contradictions found*"
    assert lines[1] == (
        "The message: Graduate Engineer Trainee · in the name of Contoso · Pune · pay 4.2 LPA"
    )
    assert [line.split(":")[0] for line in lines[4:7]] == [
        "• sender on the official domain",
        "• listing applies on the official domain",
        "• office found on Maps",
    ]
    assert "Note:" not in text
    assert "Nothing the checks found contradicts it; that is not proof it is real." in lines


# ---- Sample C -------------------------------------------------------------------------------


def test_c_verdict_cites_the_absences_by_the_search_that_found_nothing():
    draft = drafts.verdict(sample_c(), drafted_at=DRAFTED_AT)
    assert (draft["band"], draft["evidenceIds"]) == ("high_risk", ["sig_1", "sig_3", "sig_4"])
    lines = draft["summary"].split("\n")
    assert lines[0] == (
        "3 red signals of moderate or strong severity, 2 of them from search results: "
        "documents asked for before any interview, no web footprint, office not on Maps."
    )
    assert lines[4] == (
        "- office not on Maps (step 2, check_office on google_maps): no place named Zorvanta "
        "Support Services in Indore among 2 Maps results. Source: google_maps search "
        '"Zorvanta Support Services" (retrieved 3 Oct 2026)'
    )
    assert lines[5:] == [
        "Also found:",
        '- chat-only interview (the message): "The interview will be on Telegram chat only."',
        "Searches: 2 spent, 4 not spent: the evidence was decisive.",
    ]


def test_c_after_remaining_checks_cites_the_pay_benchmark_as_found():
    draft = drafts.verdict(sample_c(remaining=True), drafted_at=DRAFTED_AT)
    assert draft["evidenceIds"] == ["sig_1", "sig_3", "sig_4", "sig_6"]
    pay = next(line for line in draft["summary"].split("\n") if line.startswith("- pay far"))
    assert pay.startswith(
        "- pay far above comparable listings (step 3, check_job_listings on google_jobs): the "
        "offered ₹42,000 a month is 2.4x the median ₹17,250 (pay found in 4 of 6 listings)."
    )
    assert draft["summary"].endswith("Searches: 4 spent.")


def test_c_reply_asks_about_documents_footprint_office_pay_and_chat_but_never_a_fee():
    reply = drafts.recruiter_reply(sample_c(remaining=True), drafted_at=DRAFTED_AT)
    assert [q["rule"] for q in reply["questions"]] == [
        "sensitive_docs_early",
        "no_web_footprint",
        "pay_outlier",
        "office_not_found",
        "chat_only_interview",
        "no_listing_match",
    ]
    by_rule = {q["rule"]: q["question"] for q in reply["questions"]}
    assert by_rule["office_not_found"] == (
        "I could not find an office of Zorvanta Support Services in Indore on Google Maps. "
        "What is its full address?"
    )
    assert by_rule["pay_outlier"] == (
        "The pay offered (Rs. 42,000 per month) is far above comparable Customer Support "
        "Executive listings near Indore. Please send the written offer letter with the salary "
        "break-up."
    )
    assert "@" not in reply["text"]
    assert "fee" not in reply["text"].lower() and "₹" not in reply["text"]


def test_c_cybercrime_report_lists_both_contacts_and_the_documents_asked_for():
    text = drafts.cybercrime_report(sample_c(), drafted_at=DRAFTED_AT)["text"]
    lines = text.split("\n")
    contacts = lines.index("The sender's contacts, exactly as written in the message:")
    assert lines[contacts + 1 : contacts + 3] == [
        "- phone: +91 9XXXX XXXXX",
        "- email: joinus@zorvanta-support.example",
    ]
    assert "Money asked for: none named in the message." in lines
    assert lines[lines.index("Red flags in the message itself:") + 1].startswith(
        '- documents asked for before any interview: "Before the interview, send a clear photo '
        "of your Aadhaar card"
    )
    searched = lines.index("Red flags found by web searches:")
    assert [line.split(" (step")[0] for line in lines[searched + 1 : searched + 3]] == [
        "- no web footprint",
        "- office not on Maps",
    ]


def test_c_board_post_names_no_recruiter_contact():
    case = sample_c(remaining=True)
    text = post(case, "unverified_ask_questions")["whatsapp"]
    page = drafts.board_html([post(case, "unverified_ask_questions")], generated_at=PUBLISHED_AT)
    for contact in case["claims"]["contacts"]:
        assert contact["value"] not in text and contact["value"] not in page
    assert text.startswith("*Offer check: Unverified: ask questions first*\n")


# ---- text rules only --------------------------------------------------------------------------


def test_a_text_only_case_leads_with_its_text_red_flags_and_still_gets_a_reply_and_report():
    case = Builder("task-hinglish-no-claims").case
    draft = drafts.verdict(case, drafted_at=DRAFTED_AT)
    assert draft["band"] == "unverified"
    assert draft["summary"].startswith(
        "text-only red flags: task-scam pattern; nothing checkable was claimed.\nEvidence:\n"
        "- task-scam pattern (the message): "
    )
    assert draft["summary"].endswith("No searches run.")

    reply = drafts.recruiter_reply(case, drafted_at=DRAFTED_AT)
    assert [q["rule"] for q in reply["questions"]] == ["task_scam_pattern"]
    assert reply["text"].startswith("Hello,\n\nThank you for your message. Before I go any")

    report = drafts.cybercrime_report(case, drafted_at=DRAFTED_AT)["text"].split("\n")
    assert report[report.index("Red flags found by web searches:") + 1] == "- none"
    assert "No searches run." in report


def test_an_offer_with_no_red_flag_asks_only_for_the_offer_in_writing():
    reply = drafts.recruiter_reply(Builder("walk-in-genuine-shape").case, drafted_at=DRAFTED_AT)
    assert (reply["questions"], reply["evidenceIds"]) == ([], [])
    assert reply["text"].endswith(
        "about the Store Supervisor role at Hexavara Foods. Before I go any further, please "
        "send the offer in writing from the company's official email address."
    )


# ---- every draft, every sample -----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_link_in_a_draft_is_evidence_or_in_the_message(name):
    case = CASES[name]()
    allowed = {s["evidence"].get("link") for s in case["signals"]}
    allowed |= set(URL.findall(case["sourceText"]))
    for kind, text in every_draft(case).items():
        for url in URL.findall(text):
            assert url in allowed, (kind, url)


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_rupee_figure_in_a_draft_comes_from_the_message_or_the_evidence(name):
    case = CASES[name]()
    sources = case["sourceText"] + json.dumps(case["signals"], ensure_ascii=False)
    for kind, text in every_draft(case).items():
        for figure in RUPEES.findall(text):
            assert figure in sources, (kind, figure)


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_draft_calls_an_offer_genuine_or_safe(name):
    case = CASES[name]()
    for kind, text in every_draft(case, "no_contradictions_found").items():
        assert NEVER_SAID.search(text) is None, kind


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_draft_is_plain_json_and_cites_only_signals_the_trace_added(name):
    case = CASES[name]()
    added = {sid for step in case["trace"] for sid in step.get("signalsAdded", ())}
    text_ids = {s["id"] for s in case["signals"] if s["source"] == "text"}
    for draft in (
        drafts.verdict(case, drafted_at=DRAFTED_AT),
        drafts.recruiter_reply(case, drafted_at=DRAFTED_AT),
        drafts.cybercrime_report(case, drafted_at=DRAFTED_AT),
    ):
        assert json.loads(json.dumps(draft)) == draft
        assert set(draft["evidenceIds"]) <= added | text_ids
        assert "sig_" not in draft.get("text", "")


def test_a_stale_signal_is_never_cited():
    case = sample_a()
    case["signals"][2]["stale"] = True
    draft = drafts.verdict(case, drafted_at=DRAFTED_AT)
    assert "sig_3" not in draft["evidenceIds"]
    assert "fraud-alert" not in draft["summary"]
    assert (
        "Not counted: fee_contradicts_employer set aside: stale, a claim it depended on was "
        "corrected."
    ) in draft["summary"].split("\n")
    reply = drafts.recruiter_reply(case, drafted_at=DRAFTED_AT)
    assert "fee_contradicts_employer" not in [q["rule"] for q in reply["questions"]]
    assert "sig_3" not in drafts.cybercrime_report(case, drafted_at=DRAFTED_AT)["evidenceIds"]


def test_a_search_signal_the_trace_does_not_hold_is_never_cited():
    case = sample_a()
    case["trace"][1]["signalsAdded"] = []
    with pytest.raises(KeyError):
        drafts.verdict(case, drafted_at=DRAFTED_AT)


def test_a_text_signal_cites_the_message_even_when_a_step_zero_lists_it():
    case = sample_a()
    case["trace"].insert(
        0,
        {"step": 0, "tool": "text_rules", "actor": "agent", "because": "text rules cost no "
         "search", "searchesSpent": 0, "signalsAdded": ["sig_1"]},
    )  # fmt: skip
    [card] = drafts.citations(case, ["sig_1"])
    assert (card["step"], card["tool"], card["label"]) == (None, None, "fee asked")
    assert card["fromText"]
    assert card["finding"] == (
        '"To confirm your slot, pay a refundable registration fee of ₹2,499 within 2 hours."'
    )


def test_a_search_signal_cites_the_step_that_found_it_never_the_line_that_reused_it():
    case = sample_a()
    case["trace"].append(
        {"step": None, "tool": "investigate", "actor": "agent", "action": "reused",
         "because": "same message as case_001: reused, 0 searches", "signalsAdded": ["sig_3"]},
    )  # fmt: skip
    [card] = drafts.citations(case, ["sig_3"])
    assert (card["fromText"], card["step"], card["tool"]) == (False, 2, "find_fraud_notice")
    summary = drafts.verdict(case, drafted_at=DRAFTED_AT)["summary"]
    assert "employer says it charges no fee (step 2, find_fraud_notice on google)" in summary
    assert "(the message): a fee was asked" not in summary

    case["trace"] = [x for x in case["trace"] if x.get("step") != 2]
    with pytest.raises(KeyError):
        drafts.citations(case, ["sig_3"])


def test_a_reused_case_says_so_instead_of_its_searches():
    case = sample_a()
    case.update(id="case_002", sameAs="case_001")
    case["budget"].update(spent=0, saved=2, stoppedBecause="same_as")
    summary = drafts.verdict(case, drafted_at=DRAFTED_AT)["summary"]
    assert summary.endswith("Same message as case_001: its checks were reused, 0 searches spent.")


def test_a_matching_case_the_planner_searched_again_reports_its_own_searches():
    case = sample_b()
    case.update(id="case_002", sameAs="case_001")
    case["budget"].update(saved=0, stoppedBecause="done")
    assert drafts.verdict(case, drafted_at=DRAFTED_AT)["summary"].endswith(
        "Searches: 4 spent: every check that applied has run."
    )


def test_every_way_the_planner_stops_has_its_own_words():
    assert set(drafts.STOPPED) == {None, *planner.STOP_REASONS}


@pytest.mark.parametrize(
    ("stopped", "spent", "ending"),
    [
        ("budget", 6, "Searches: 6 spent: the per-case budget of 6 searches ran out."),
        ("quota", 1, "Searches: 1 spent; the quota guard stopped the checks: too few searches "
         "are left this month."),
        ("search_error", 1, "Searches: 1 spent; a search failed, so the checks stopped and "
         "nothing was made up."),
        ("no_company", 0, "No searches run: the message names no company to check on the web."),
        ("done", 0, "Searches: 0 spent: every check that applied has run."),
    ],
)  # fmt: skip
def test_each_stop_is_named_plainly(stopped, spent, ending):
    case = sample_b()
    case["budget"].update(spent=spent, saved=0, stoppedBecause=stopped)
    assert drafts.verdict(case, drafted_at=DRAFTED_AT)["summary"].endswith(ending)


# ---- one question per red flag ------------------------------------------------------------------


def test_every_red_rule_has_a_question():
    red = {name for name, rule in RULES.items() if rule.direction == "red"}
    assert set(drafts.QUESTIONS) == red


OFFICIAL_PAGE = {
    "engine": "google",
    "query": "site:brand.example …",
    "title": "Recruitment fraud | Brand",
    "link": "https://careers.brand.example/notice",
    "snippet": "Brand does not charge candidates.",
    "date": None,
    "retrievedAt": "2026-10-03T04:00:00+00:00",
}


@pytest.mark.parametrize("rule", sorted(n for n, r in RULES.items() if r.direction == "red"))
def test_each_red_rule_alone_gets_exactly_its_own_question(rule):
    case = Builder("a").case
    fee = case["signals"][0]
    needs_fee = RULES[rule].requires == "fee_requested"
    case["signals"] = [fee] if needs_fee or rule == "fee_requested" else []
    if rule != "fee_requested":
        extra = (
            {"domain": "brand-careers.example"} if rule.startswith(("sender", "domain")) else {}
        )
        signal = signal_for(rule, "sig_9", OFFICIAL_PAGE, detail=f"{rule} fired", **extra)
        case["signals"].append(signal)
    reply = drafts.recruiter_reply(case, drafted_at=DRAFTED_AT)
    rules = [q["rule"] for q in reply["questions"]]
    assert rules == (["fee_requested", rule] if needs_fee else [rule])
    question = reply["questions"][-1]["question"]
    assert "None" not in question and question.endswith(("?", "."))


# ---- the board page -------------------------------------------------------------------------


def test_the_board_page_renders_only_published_posts_escaped_with_safe_links():
    a, c = sample_a(), sample_c()
    c["id"] = "case_002"
    a["signals"][2]["evidence"] = {
        **a["signals"][2]["evidence"],
        "link": "javascript:alert(1)",
    }
    board = [post(a, "likely_impersonation", "<script>alert('x')</script> & more")]
    page = drafts.board_html(board, generated_at=PUBLISHED_AT)
    assert page.startswith("<!doctype html>")
    assert "<script" not in page and "javascript:" not in page
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; more" in page
    assert page.count('<article class="post"') == 1 and 'id="case_001"' in page
    assert "Zorvanta" not in page
    assert "Likely impersonation" in page and "Downloaded 6 Oct 2026, 10:05 IST · 1 " in page
    assert all(
        'rel="noopener noreferrer nofollow"' in anchor for anchor in re.findall(r"<a [^>]*>", page)
    )
    assert "http" not in page.split("<body>")[0]


def test_an_empty_board_page_says_nothing_is_published():
    page = drafts.board_html([], generated_at=PUBLISHED_AT)
    assert "No verdicts published yet." in page and "0 published verdicts" in page
