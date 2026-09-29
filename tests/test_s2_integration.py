"""Samples A, B and C through every S2 module at once: the params builders, the fake provider
serving ``tests/fixtures/serp/`` by normalised params (``routes.json``), the readers, and the
decision table. Each walk runs the checks in the order the planner's expected trace for that
sample runs them, reordering rules included, and the band after every search is the one that
trace names. The same walks then run on scrubbed recordings through the replay provider, which
must reach the same signals and bands, so the scrubber never removes what a reader needs."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from offer_checkpost.checks import (
    RESTRICTORS,
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
from offer_checkpost.providers import (
    FIXTURES_DIR,
    ROUTES_FILE,
    FakeSearchProvider,
    ReplaySearchProvider,
    SearchProvider,
    params_key,
    provider_from_env,
    write_recording,
)
from offer_checkpost.rules import RULES, decide, is_decisive, text_signal
from offer_checkpost.scrub import WHITELIST

# The synthetic stand-ins of the demo samples, which the fake provider's fixtures answer.
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "offers"
MAX_SEARCHES = 6  # the planner's default budget
NOW = 1_791_000_000.0
RETRIEVED_AT = datetime.fromtimestamp(NOW, UTC).isoformat()
RECORDED_AT = "2026-10-03T10:02:11+05:30"
SIGNAL_KEYS = {"id", "rule", "direction", "severity", "source", "stale", "evidence"}
EVIDENCE_KEYS = {"engine", "query", "title", "link", "snippet", "retrievedAt"}


class Case:
    """A case as the planner will hold one: claims, text-rule signals, then one step per
    search, each step keeping the band it left the case in."""

    def __init__(self, sample: str, provider: SearchProvider):
        self.text = (SAMPLES_DIR / f"{sample}.txt").read_text(encoding="utf-8")
        self.claims = extract_claims(self.text)
        self.provider = provider
        self.signals = []
        self._add(text_signal(hit, None) for hit in text_rules(self.text))
        self.steps = []

    def claim(self, name):
        return self.claims[name]["value"]

    def fired(self, rule):
        return any(s["rule"] == rule for s in self.signals)

    def run(self, tool, params, read, **context):
        result = self.provider.search(params)
        reading = read(
            result.data, params=result.params, retrieved_at=result.retrieved_at, **context
        )
        added = self._add(reading.signals)
        self.steps.append((tool, result, reading, added, self.decide().band))
        return reading

    def decide(self):
        return decide(self.signals, company_claimed=self.claims["company"] is not None)

    @property
    def spent(self):
        return sum(result.searches_spent for _, result, _, _, _ in self.steps)

    def trail(self):
        return [
            (tool, result.engine, [s["rule"] for s in added], band)
            for tool, result, _, added, band in self.steps
        ]

    def _add(self, signals):
        added = [{**s, "id": f"sig_{len(self.signals) + i}"} for i, s in enumerate(signals, 1)]
        self.signals += added
        return added


def step1(case):
    company, city = case.claim("company"), case.claim("city")
    return case.run(
        "lookup_official_site",
        lookup_official_site_params(company, city),
        read_official_site,
        company=company,
        contacts=case.claims["contacts"],
        links=case.claims["links"],
    )


def walk_a(case):
    """Step 1, then a: ``find_fraud_notice``, because a fee was asked and step 1 named the
    official domain. Decisive there."""
    official = step1(case).facts["officialDomain"]
    case.run(
        "find_fraud_notice",
        find_fraud_notice_params(official, case.claim("city")),
        read_fraud_notice,
        official_domain=official,
        fee_requested=case.fired("fee_requested"),
    )
    return case


def confirm_a(case):
    """b: ``confirm_sender_domain`` on the look-alike, run only when a person asks for the
    checks the decisive stop skipped."""
    official = case.steps[0][2].facts["officialDomain"]
    case.run(
        "confirm_sender_domain",
        confirm_sender_domain_params(official, "brand-careers.example", case.claim("city")),
        read_confirm_sender_domain,
        official_domain=official,
        candidate="brand-careers.example",
        contacts=case.claims["contacts"],
    )
    return case


def remaining_a(case):
    """The rest of the checks the decisive stop skipped, after ``confirm_a``: the employer's
    own listing, the office and the scam reports."""
    company, role, city = case.claim("company"), case.claim("role"), case.claim("city")
    case.run(
        "check_job_listings",
        check_job_listings_params(role, city, company),
        read_job_listings,
        company=company,
        role=role,
        city=city,
        official_domain=case.steps[0][2].facts["officialDomain"],
        offered_monthly_inr=case.claims["pay"]["monthlyInr"],
    )
    case.run(
        "check_office",
        check_office_params(company, city),
        read_office,
        company=company,
        city=city,
    )
    case.run(
        "check_scam_reports",
        check_scam_reports_params(company),
        read_scam_reports,
        company=company,
    )
    return case


def walk_b(case):
    """Step 1 finds the sender official and no fee is asked, so R2 skips the fraud notice
    and runs the job listings first, then the office and the scam reports."""
    company, role, city = case.claim("company"), case.claim("role"), case.claim("city")
    official = step1(case).facts["officialDomain"]
    case.run(
        "check_job_listings",
        check_job_listings_params(role, city, company),
        read_job_listings,
        company=company,
        role=role,
        city=city,
        official_domain=official,
        offered_monthly_inr=case.claims["pay"]["monthlyInr"],
    )
    case.run(
        "check_office",
        check_office_params(company, city),
        read_office,
        company=company,
        city=city,
    )
    case.run(
        "check_scam_reports",
        check_scam_reports_params(company),
        read_scam_reports,
        company=company,
    )
    return case


def walk_c(case):
    """Step 1 finds no footprint and no official domain, so R1 moves the office ahead of the
    job listings. Decisive there."""
    company, city = case.claim("company"), case.claim("city")
    step1(case)
    case.run(
        "check_office",
        check_office_params(company, city),
        read_office,
        company=company,
        city=city,
    )
    return case


def remaining_c(case):
    """The checks the decisive stop skipped, run when a person asks: the listings (by role
    alone, since no official domain is known) and the scam reports."""
    company, role, city = case.claim("company"), case.claim("role"), case.claim("city")
    case.run(
        "check_job_listings",
        check_job_listings_params(role, city),
        read_job_listings,
        company=company,
        role=role,
        city=city,
        official_domain=None,
        offered_monthly_inr=case.claims["pay"]["monthlyInr"],
    )
    case.run(
        "check_scam_reports",
        check_scam_reports_params(company),
        read_scam_reports,
        company=company,
    )
    return case


WALKS = {
    "a": lambda case: remaining_a(confirm_a(walk_a(case))),
    "b": walk_b,
    "c": lambda case: remaining_c(walk_c(case)),
}


def fake():
    return FakeSearchProvider.from_fixtures(clock=lambda: NOW)


# ---- the three samples ----------------------------------------------------------------------


def test_sample_a_is_high_risk_after_two_searches_with_four_saved():
    case = Case("a", fake())
    assert [s["rule"] for s in case.signals] == ["fee_requested"]
    assert case.decide().band == "unverified"

    walk_a(case)
    assert case.trail() == [
        ("lookup_official_site", "google", ["sender_lookalike"], "unverified"),
        ("find_fraud_notice", "google", ["fee_contradicts_employer"], "high_risk"),
    ]
    site = case.steps[0][2]
    assert (site.facts["officialDomain"], site.facts["via"]) == (
        "brand.example",
        "knowledge_graph",
    )
    assert case.steps[0][3][0]["domain"] == "brand-careers.example"

    d = case.decide()
    assert d.evidence_ids == ("sig_1", "sig_3")
    assert d.summary == (
        "2 strong red signals, 1 of them from a search result: "
        "fee asked, employer says it charges no fee"
    )
    notice = case.signals[2]["evidence"]
    assert notice["link"] == "https://careers.brand.example/fraud-alert"
    assert "never charges candidates any fee" in notice["snippet"]
    # Decisive while the look-alike is still unconfirmed: confirming it can't lower the band.
    assert is_decisive(case.signals, open_domains=["brand-careers.example"])
    assert (case.spent, MAX_SEARCHES - case.spent) == (2, 4)


def test_sample_a_the_employer_naming_the_lookalike_replaces_it():
    case = confirm_a(walk_a(Case("a", fake())))
    assert case.trail()[-1] == (
        "confirm_sender_domain",
        "google",
        ["domain_named_in_fraud_notice"],
        "high_risk",
    )
    d = case.decide()
    assert d.evidence_ids == ("sig_1", "sig_3", "sig_4")
    assert (
        "sender_lookalike set aside: replaced by domain_named_in_fraud_notice "
        "for brand-careers.example"
    ) in d.reasons


def test_sample_a_stays_high_risk_through_the_rest_of_the_remaining_checks():
    case = remaining_a(confirm_a(walk_a(Case("a", fake()))))
    assert case.trail()[3:] == [
        ("check_job_listings", "google_jobs", ["no_listing_match"], "high_risk"),
        ("check_office", "google_maps", ["office_not_found"], "high_risk"),
        ("check_scam_reports", "google_news", ["impersonation_reports"], "high_risk"),
    ]
    assert case.spent == MAX_SEARCHES


def test_sample_b_is_consistent_with_genuine_after_four_searches():
    case = walk_b(Case("b", fake()))
    assert case.trail() == [
        ("lookup_official_site", "google", ["sender_official"], "unverified"),
        ("check_job_listings", "google_jobs", ["listing_match"], "consistent_with_genuine"),
        ("check_office", "google_maps", ["office_found"], "consistent_with_genuine"),
        (
            "check_scam_reports",
            "google_news",
            ["impersonation_reports"],
            "consistent_with_genuine",
        ),
    ]
    d = case.decide()
    assert d.evidence_ids == ("sig_1", "sig_2", "sig_3")
    assert d.summary.endswith(
        "; weak red signals noted: news of fake offers in the company's name"
    )
    assert case.steps[2][2].facts["place_id"] == "ChIJsyntheticContosoPune01"
    assert not is_decisive(case.signals)
    assert case.spent == 4


def test_sample_c_is_high_risk_after_two_searches_and_stays_so_on_request():
    case = Case("c", fake())
    assert [s["rule"] for s in case.signals] == ["sensitive_docs_early", "chat_only_interview"]

    walk_c(case)
    assert case.trail() == [
        ("lookup_official_site", "google", ["no_web_footprint"], "unverified"),
        ("check_office", "google_maps", ["office_not_found"], "high_risk"),
    ]
    assert case.steps[0][2].facts["officialDomain"] is None
    assert is_decisive(case.signals)
    assert (case.spent, MAX_SEARCHES - case.spent) == (2, 4)
    assert case.decide().evidence_ids == ("sig_1", "sig_3", "sig_4")

    remaining_c(case)
    assert case.trail()[2:] == [
        ("check_job_listings", "google_jobs", ["no_listing_match", "pay_outlier"], "high_risk"),
        ("check_scam_reports", "google_news", [], "high_risk"),
    ]
    assert case.steps[2][2].note.endswith("pay found in 4 of 6 listings, median ₹17,250 a month")
    assert case.spent == 4


# ---- the modules agree ----------------------------------------------------------------------


def every_case():
    return [walk(Case(sample, fake())) for sample, walk in WALKS.items()]


def test_every_signal_is_a_decision_table_signal_citing_what_its_provider_served():
    for case in every_case():
        for _, result, _, added, _ in case.steps:
            for s in added:
                rule = RULES[s["rule"]]
                assert SIGNAL_KEYS <= s.keys() and EVIDENCE_KEYS <= s["evidence"].keys()
                assert (s["direction"], s["severity"]) == (rule.direction, rule.severity)
                assert s["source"] == rule.engine == result.engine == s["evidence"]["engine"]
                assert s["evidence"]["retrievedAt"] == result.retrieved_at == RETRIEVED_AT
                query = result.params.get("q", result.params.get("query"))
                assert s["evidence"]["query"] == query
                assert re.fullmatch(r"sig_\d+", s["id"]) and s["stale"] is False
            assert "api_key" not in result.params
            assert (result.provider, result.cache, result.searches_spent) == ("fake", "miss", 1)


def test_the_routes_file_serves_exactly_the_three_walks():
    served = {
        params_key(result.params) for case in every_case() for _, result, _, _, _ in case.steps
    }
    routes = json.loads((FIXTURES_DIR / ROUTES_FILE).read_text(encoding="utf-8"))
    assert served == {params_key(route["params"]) for route in routes}
    assert len(routes) == len(served)
    assert all((FIXTURES_DIR / route["fixture"]).is_file() for route in routes)


def test_the_fake_named_in_the_environment_serves_the_samples():
    case = walk_a(Case("a", provider_from_env({"OFFER_CHECKPOST_PROVIDER": "fake"})))
    assert case.decide().band == "high_risk"


@pytest.mark.parametrize("sample", sorted(WALKS))
def test_replaying_scrubbed_recordings_reaches_the_same_signals_and_bands(sample, tmp_path):
    served = WALKS[sample](Case(sample, fake()))
    for _, result, _, _, _ in served.steps:
        write_recording(
            sample,
            result.params,
            result.data,
            recorded_at=RECORDED_AT,
            recordings_dir=tmp_path,
        )

    replayed = WALKS[sample](Case(sample, ReplaySearchProvider(tmp_path)))
    assert replayed.trail() == served.trail()
    assert replayed.decide() == served.decide()
    assert replayed.spent == served.spent
    for (_, result, reading, added, _), (_, _, fresh, fresh_added, _) in zip(
        replayed.steps, served.steps, strict=True
    ):
        assert (result.provider, result.cache, result.retrieved_at) == (
            "replay",
            "replay",
            RECORDED_AT,
        )
        assert (reading.note, reading.facts) == (fresh.note, fresh.facts)
        assert added == [redated(s, RECORDED_AT) for s in fresh_added]


def redated(signal, retrieved_at):
    return {**signal, "evidence": {**signal["evidence"], "retrievedAt": retrieved_at}}


def expand(restrictor):
    """``a.{b,c},d[].{e}`` as field paths: ``a.b``, ``a.c``, ``d[].e``."""
    paths = []
    for prefix, fields in re.findall(r"([\w\[\]]+)\.\{([^}]*)\}", restrictor):
        paths += [f"{prefix}.{field}" for field in fields.split(",")]
    return paths


def test_the_restrictor_and_the_scrub_whitelist_keep_the_same_fields():
    assert set(RESTRICTORS) == set(WHITELIST)
    for engine, restrictor in RESTRICTORS.items():
        assert sorted(expand(restrictor)) == sorted(WHITELIST[engine]), engine
