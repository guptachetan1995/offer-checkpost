import json
import re
from pathlib import Path

import pytest

from offer_checkpost.checks import (
    GOOGLE_INDIA,
    MAPS_ZOOM,
    RESTRICTORS,
    check_contact_footprint_params,
    check_job_listings_params,
    check_office_params,
    check_scam_reports_params,
    confirm_sender_domain_params,
    find_fraud_notice_params,
    lookup_official_site_params,
    maps_link,
    mentions,
    names_match,
    read_confirm_sender_domain,
    read_contact_footprint,
    read_fraud_notice,
    read_job_listings,
    read_office,
    read_office_reviews,
    read_official_site,
    read_scam_reports,
    role_matches,
    scan_office_reviews_params,
    search_name,
)
from offer_checkpost.extract import extract_claims
from offer_checkpost.providers import ROUTES_FILE
from offer_checkpost.rules import RULES, decide

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "serp"
AT = "2026-10-02T10:00:00+05:30"

SEARCH_RULES = {
    "sender_lookalike", "domain_named_in_fraud_notice", "sender_free_mail",
    "fee_contradicts_employer", "employer_fraud_notice_exists", "no_web_footprint",
    "pay_outlier", "no_listing_match", "office_not_found", "reviews_mention_fees",
    "impersonation_reports", "contact_reported", "sender_official", "listing_match",
    "office_found",
}  # fmt: skip
EVIDENCE_KEYS = {"engine", "query", "title", "link", "snippet", "date", "retrievedAt"}


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def claims_of(sample):
    return extract_claims((ROOT / "samples" / "offers" / sample).read_text())


def rules_of(reading):
    return [s["rule"] for s in reading.signals]


def only(reading, rule):
    [signal] = [s for s in reading.signals if s["rule"] == rule]
    return signal


def email(value):
    return {"kind": "email", "value": value, "synthetic": False}


def link(value):
    return {"value": value, "synthetic": False}


# ---- params builders ----------------------------------------------------------------------

GOOGLE_BUILDERS = [
    lambda city: lookup_official_site_params("Brand", city),
    lambda city: find_fraud_notice_params("brand.example", city),
    lambda city: confirm_sender_domain_params("brand.example", "brand-careers.example", city),
    lambda city: check_contact_footprint_params(email("hr.desk@quickhire.example"), city),
]


@pytest.mark.parametrize("build", GOOGLE_BUILDERS)
def test_google_builders_set_the_india_parameters_and_the_restrictor(build):
    params = build("Noida")
    assert params["engine"] == "google"
    assert params["gl"] == "in"
    assert params["google_domain"] == "google.co.in"
    assert params["hl"] == "en"
    assert params["location"] == "Noida, India"
    assert params["json_restrictor"] == RESTRICTORS["google"]


@pytest.mark.parametrize("build", GOOGLE_BUILDERS)
def test_google_location_falls_back_to_india_when_no_city_is_claimed(build):
    assert build(None)["location"] == "India"


def test_jobs_builder_sets_the_india_parameters_and_the_claimed_city():
    params = check_job_listings_params("Graduate Engineer Trainee", "Pune", "Contoso")
    assert params == {
        "engine": "google_jobs",
        "q": "Graduate Engineer Trainee Contoso",
        **GOOGLE_INDIA,
        "location": "Pune, India",
        "json_restrictor": RESTRICTORS["google_jobs"],
    }


def test_jobs_query_drops_the_role_aside_and_legal_words_and_can_leave_the_company_out():
    with_company = check_job_listings_params(
        "Data Entry Executive (WFH)", "Noida", "Zorvanta Support Services Pvt. Ltd"
    )
    assert with_company["q"] == "Data Entry Executive Zorvanta Support Services"
    assert check_job_listings_params("Customer Support Executive", "Indore")["q"] == (
        "Customer Support Executive"
    )


def test_maps_builder_is_a_search_at_the_city_with_location_and_zoom():
    params = check_office_params("Zorvanta Support Services Pvt. Ltd", "Indore")
    assert params == {
        "engine": "google_maps",
        "type": "search",
        "q": "Zorvanta Support Services",
        **GOOGLE_INDIA,
        "location": "Indore, India",
        "z": MAPS_ZOOM,
        "json_restrictor": RESTRICTORS["google_maps"],
    }


def test_news_builder_takes_no_google_domain_or_location():
    params = check_scam_reports_params("Brand Pvt Ltd")
    assert params == {
        "engine": "google_news",
        "q": '"Brand" job offer (fake OR scam OR fraud)',
        "gl": "in",
        "hl": "en",
        "json_restrictor": RESTRICTORS["google_news"],
    }


def test_reviews_builder_takes_no_gl_or_location():
    params = scan_office_reviews_params("ChIJsyntheticContosoPune01", "fee")
    assert params == {
        "engine": "google_maps_reviews",
        "place_id": "ChIJsyntheticContosoPune01",
        "hl": "en",
        "query": "fee",
        "json_restrictor": RESTRICTORS["google_maps_reviews"],
    }


@pytest.mark.parametrize(
    ("engine", "fields"),
    [
        ("google", ["knowledge_graph.{title,website}", "organic_results[].{title,link,snippet}"]),
        (
            "google_jobs",
            [
                "jobs_results[]",
                "title",
                "company_name",
                "location",
                "detected_extensions.salary",
                "extensions",
                "description",
                "apply_options[].link",
            ],
        ),
        (
            "google_maps",
            ["local_results[]", "place_results", "place_id", "type", "address", "rating"],
        ),
        ("google_news", ["news_results[]", "title", "link", "source.name", "date"]),
        ("google_maps_reviews", ["reviews[]", "snippet", "rating", "iso_date", "link"]),
    ],
)
def test_each_restrictor_keeps_the_fields_its_reader_uses(engine, fields):
    assert all(field in RESTRICTORS[engine] for field in fields)


def test_the_reviews_restrictor_never_fetches_the_reviewer():
    restrictor = RESTRICTORS["google_maps_reviews"]
    assert not re.search(r"user|contributor|profile|thumbnail|name", restrictor)


def test_no_builder_adds_an_api_key():
    built = [build("Pune") for build in GOOGLE_BUILDERS] + [
        check_job_listings_params("Trainee", "Pune", "Contoso"),
        check_office_params("Contoso", "Pune"),
        check_scam_reports_params("Contoso"),
        scan_office_reviews_params("ChIJsyntheticContosoPune01", "fraud"),
    ]
    assert all("api_key" not in params for params in built)


def test_site_searches_are_scoped_to_the_official_domain():
    assert find_fraud_notice_params("brand.example")["q"].startswith("site:brand.example ")
    confirm = confirm_sender_domain_params("brand.example", "hr.onboarding@brand-careers.example")
    assert confirm["q"] == 'site:brand.example "brand-careers.example"'


def test_contact_footprint_searches_the_exact_contact():
    assert check_contact_footprint_params(email("hr.desk@quickhire.example"))["q"] == (
        '"hr.desk@quickhire.example" (scam OR fraud OR fake)'
    )
    phone = {"kind": "phone", "value": "+91 " + "0" * 5 + " " + "0" * 4 + "7"}
    assert check_contact_footprint_params(phone)["q"].startswith('"' + "0" * 9 + '7"')


@pytest.mark.parametrize(
    ("company", "name"),
    [
        ("Zorvanta Support Services Pvt. Ltd", "Zorvanta Support Services"),
        ("Brand Private Limited", "Brand"),
        ("Contoso", "Contoso"),
        ("Hexavara Foods LLP.", "Hexavara Foods"),
        ("Ltd", "Ltd"),
    ],
)
def test_search_name_drops_trailing_legal_words(company, name):
    assert search_name(company) == name


# ---- names and roles ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Contoso", "Contoso India Pvt Ltd", True),
        ("Contoso", "Contoso Pune Office", True),
        ("Brand Inc.", "Brand", True),
        ("Tata & Sons", "Tata and Sons", True),
        ("Support Services", "Zorvanta Support Services", False),
        ("Brand", "Brandix Apparel", False),
        ("Pvt Ltd", "Pvt Ltd", False),
        ("Contoso", "Contoso Pizza", False),
        ("Zorvanta", "Zorvanta Support Services Pvt. Ltd", False),
        ("Contoso", "Contoso Technologies Pvt Ltd", True),
        ("Contoso", "Contoso Regional Office, Navi Mumbai", True),
    ],
)
def test_names_match(a, b, expected):
    assert names_match(a, b) is expected
    assert names_match(b, a) is expected


def test_a_locality_after_the_name_matches_only_where_the_place_is():
    assert names_match("Contoso Kharadi", "Contoso", "Kharadi, Pune, Maharashtra 411014")
    assert not names_match("Contoso Kharadi", "Contoso")


def test_mentions_needs_the_whole_name_in_order():
    assert mentions("Fake job offers in Brand's name", "Brand")
    assert mentions("Zorvanta Support Services - Indore | Directory", "Zorvanta Support Services")
    assert not mentions("Support Services Companies in Indore", "Zorvanta Support Services")


@pytest.mark.parametrize(
    ("title", "role", "expected"),
    [
        ("Graduate Engineer Trainee - Mechanical", "Graduate Engineer Trainee", True),
        ("Customer Support Associate", "Customer Support Executive", True),
        ("Data Entry Executive", "Data Entry Executive (WFH)", True),
        ("Sales Executive", "Data Entry Executive (WFH)", False),
        ("Software Tester", "Software Engineer", False),
    ],
)
def test_role_matches(title, role, expected):
    assert role_matches(title, role) is expected


# ---- google: lookup_official_site ---------------------------------------------------------


def read_sample(sample, name):
    claims = claims_of(sample)
    company, city = claims["company"]["value"], claims["city"]["value"]
    return read_official_site(
        fixture(name),
        params=lookup_official_site_params(company, city),
        retrieved_at=AT,
        company=company,
        contacts=claims["contacts"],
        links=claims["links"],
    )


def official_site(name, company, contacts=(), links=()):
    return read_official_site(
        fixture(name) if isinstance(name, str) else name,
        params=lookup_official_site_params(company),
        retrieved_at=AT,
        company=company,
        contacts=list(contacts),
        links=list(links),
    )


def test_sample_a_knowledge_graph_names_the_domain_and_the_sender_is_a_lookalike():
    reading = read_sample("a.txt", "google/official_site_brand.json")
    assert reading.facts["officialDomain"] == "brand.example"
    assert reading.facts["via"] == "knowledge_graph"
    assert reading.facts["domains"] == {"brand-careers.example": "lookalike"}
    assert rules_of(reading) == ["sender_lookalike"]
    signal = only(reading, "sender_lookalike")
    assert signal["domain"] == "brand-careers.example"
    assert "contains the brand token 'brand'" in signal["detail"]
    assert signal["evidence"] == {
        "engine": "google",
        "query": "Brand",
        "title": "Brand",
        "link": "https://www.brand.example/",
        "snippet": None,
        "date": None,
        "retrievedAt": AT,
    }
    assert reading.note.startswith("official domain brand.example (knowledge graph)")


def test_sample_b_sender_on_the_official_domain_and_a_subdomain_link():
    reading = read_sample("b.txt", "google/official_site_contoso.json")
    assert reading.facts["officialDomain"] == "contoso.example"
    assert rules_of(reading) == ["sender_official"]
    assert only(reading, "sender_official")["domain"] == "contoso.example"


def test_sample_c_no_knowledge_graph_and_no_matching_result_is_no_web_footprint():
    reading = read_sample("c.txt", "google/official_site_no_footprint.json")
    assert reading.facts == {
        "officialDomain": None,
        "via": None,
        "knowledgeGraph": False,
        "footprint": False,
        "domains": {},
    }
    assert rules_of(reading) == ["no_web_footprint"]
    evidence = only(reading, "no_web_footprint")["evidence"]
    assert evidence["query"] == "Zorvanta Support Services"
    assert evidence["link"] is None and evidence["snippet"] is None
    assert reading.note == "no official domain found: no web footprint"


def test_without_a_knowledge_graph_the_top_organic_domain_naming_the_company_is_official():
    reading = official_site(
        "google/official_site_organic_only.json",
        "Hexavara Foods",
        contacts=[email("jobs@hexavara.example")],
    )
    assert reading.facts["officialDomain"] == "hexavara.example"
    assert reading.facts["via"] == "organic"
    assert reading.note.startswith("official domain hexavara.example (top organic result)")
    # An organic guess anchors look-alike checks but never vouches for a sender.
    assert rules_of(reading) == []


def test_an_organic_domain_anchors_a_lookalike():
    reading = official_site(
        "google/official_site_organic_only.json",
        "Hexavara Foods",
        contacts=[email("hr@hexavara-careers.example")],
    )
    assert rules_of(reading) == ["sender_lookalike"]
    assert only(reading, "sender_lookalike")["evidence"]["link"] == (
        "https://hexavara.example/about"
    )


def test_a_knowledge_graph_about_another_entity_is_ignored():
    response = fixture("google/official_site_brand.json")
    response["knowledge_graph"] = {
        "title": "Brandywine Records",
        "website": "https://brandywine.example/",
    }
    reading = official_site(response, "Brand")
    assert reading.facts["knowledgeGraph"] is False
    assert reading.facts["officialDomain"] == "brand.example"
    assert reading.facts["via"] == "organic"


def test_a_knowledge_graph_website_on_a_social_platform_is_not_the_official_domain():
    response = {
        "knowledge_graph": {"title": "Zorvanta", "website": "https://www.facebook.com/zorvanta"},
        "organic_results": [],
    }
    reading = official_site(response, "Zorvanta", contacts=[email("hr@zorvanta.example")])
    assert reading.facts["officialDomain"] is None
    assert reading.facts["footprint"] is True
    assert rules_of(reading) == []


def test_free_mail_sender_while_the_company_has_a_domain():
    reading = official_site(
        "google/official_site_brand.json",
        "Brand",
        contacts=[email("hr@gmail.com")],
        links=[link("https://careers.brand.example/jobs")],
    )
    assert rules_of(reading) == ["sender_free_mail"]
    assert only(reading, "sender_free_mail")["domain"] == "gmail.com"


def test_an_official_link_does_not_vouch_for_an_email_sent_from_elsewhere():
    reading = official_site(
        "google/official_site_brand.json",
        "Brand",
        contacts=[email("desk@recruit-hub.example")],
        links=[link("https://careers.brand.example/jobs")],
    )
    assert reading.facts["domains"] == {
        "recruit-hub.example": "unrelated",
        "brand.example": "subdomain",
    }
    assert rules_of(reading) == []


def test_an_official_link_alone_is_an_official_sender():
    reading = official_site(
        "google/official_site_brand.json",
        "Brand",
        links=[link("https://careers.brand.example/jobs")],
    )
    assert rules_of(reading) == ["sender_official"]
    assert "link" in only(reading, "sender_official")["detail"]


def test_a_lookalike_link_domain():
    reading = official_site(
        "google/official_site_brand.json", "Brand", links=[link("https://brnad.example/apply")]
    )
    signal = only(reading, "sender_lookalike")
    assert signal["domain"] == "brnad.example"
    assert "link" in signal["detail"] and "1 edit" in signal["detail"]


def test_each_lookalike_domain_is_its_own_signal():
    reading = official_site(
        "google/official_site_brand.json",
        "Brand",
        contacts=[email("hr@brand-careers.example")],
        links=[link("https://brnad.example/apply"), link("https://brand-careers.example/x")],
    )
    assert [s["domain"] for s in reading.signals] == ["brand-careers.example", "brnad.example"]


def test_no_sender_rule_without_an_official_domain():
    reading = official_site(
        "google/official_site_no_footprint.json",
        "Zorvanta Support Services",
        contacts=[email("hr@gmail.com"), email("joinus@zorvanta-support.example")],
    )
    assert rules_of(reading) == ["no_web_footprint"]


def test_a_result_titled_with_the_company_is_a_footprint_without_a_domain():
    response = {
        "organic_results": [
            {
                "title": "Zorvanta Support Services - Indore | Directory",
                "link": "https://directory.example/indore/zorvanta",
                "snippet": "Listed business.",
            }
        ]
    }
    reading = official_site(response, "Zorvanta Support Services Pvt. Ltd")
    assert reading.facts["officialDomain"] is None
    assert reading.facts["footprint"] is True
    assert rules_of(reading) == []
    assert reading.note == "no official domain found"


QUILLON_ORGANIC = [
    {
        "title": "Quillon Hospitals - Multispeciality Care in Pune",
        "link": "https://www.quillon.example/",
        "snippet": "A fictional hospital.",
    },
    {
        "title": "Quillon Support Services | Customer support outsourcing",
        "link": "https://quillonsupportservices.example/",
        "snippet": "A fictional support firm.",
    },
]


def test_the_whole_name_domain_wins_over_an_earlier_first_word_domain():
    reading = official_site(
        {"organic_results": QUILLON_ORGANIC},
        "Quillon Support Services Pvt Ltd",
        contacts=[email("hr@quillonsupportservices.example")],
    )
    assert reading.facts["officialDomain"] == "quillonsupportservices.example"
    assert reading.facts["domains"] == {"quillonsupportservices.example": "official"}
    assert rules_of(reading) == []


def test_a_first_word_domain_needs_a_title_that_names_the_company():
    reading = official_site(
        {"organic_results": QUILLON_ORGANIC[:1]}, "Quillon Support Services Pvt Ltd"
    )
    assert reading.facts["officialDomain"] is None
    assert rules_of(reading) == ["no_web_footprint"]


def test_a_knowledge_graph_about_another_business_sharing_the_first_word_is_ignored():
    response = {
        "knowledge_graph": {"title": "Contoso Pizza", "website": "https://contosopizza.example/"}
    }
    reading = official_site(response, "Contoso", contacts=[email("hr@contosopizza.example")])
    assert reading.facts["knowledgeGraph"] is False
    assert reading.facts["officialDomain"] is None
    assert rules_of(reading) == ["no_web_footprint"]


def test_a_knowledge_graph_site_on_a_site_builder_is_official_for_itself_alone():
    response = {
        "knowledge_graph": {
            "title": "Contoso Staffing",
            "website": "https://contosostaffing.wixsite.com/home",
        }
    }
    reading = official_site(
        response, "Contoso Staffing", links=[link("https://quick-jobs-2026.wixsite.com/apply")]
    )
    assert reading.facts["officialDomain"] == "contosostaffing.wixsite.com"
    assert reading.facts["domains"] == {"quick-jobs-2026.wixsite.com": "unrelated"}
    assert rules_of(reading) == []


# ---- google: find_fraud_notice ------------------------------------------------------------


def fraud_notice(response, fee_requested=True):
    return read_fraud_notice(
        fixture(response) if isinstance(response, str) else response,
        params=find_fraud_notice_params("brand.example", "Noida"),
        retrieved_at=AT,
        official_domain="brand.example",
        fee_requested=fee_requested,
    )


def notice(snippet, *, title="Recruitment fraud alert | Brand", url=None):
    return {
        "organic_results": [
            {"title": title, "link": url or "https://www.brand.example/fraud", "snippet": snippet}
        ]
    }


def test_a_no_fee_notice_on_the_official_domain_contradicts_the_fee():
    reading = fraud_notice("google/fraud_notice_fee.json")
    signal = only(reading, "fee_contradicts_employer")
    assert signal["source"] == "google"
    assert signal["evidence"]["link"] == "https://careers.brand.example/fraud-alert"
    assert signal["evidence"]["snippet"].startswith("Brand never charges candidates any fee")
    assert signal["evidence"]["query"].startswith("site:brand.example ")
    assert reading.facts["feePhrase"] == "never charges"
    assert reading.note == 'brand.example has a recruitment-fraud notice: "never charges"'


def test_a_notice_without_a_fee_phrase_is_employer_fraud_notice_exists():
    reading = fraud_notice("google/fraud_notice_no_fee_phrase.json")
    assert rules_of(reading) == ["employer_fraud_notice_exists"]
    assert reading.facts["feePhrase"] is None


def test_without_a_fee_asked_the_notice_is_quoted_but_fires_nothing():
    reading = fraud_notice("google/fraud_notice_fee.json", fee_requested=False)
    assert rules_of(reading) == []
    assert reading.facts["notice"]["link"] == "https://careers.brand.example/fraud-alert"


def test_a_fraud_page_about_something_other_than_recruitment_is_not_a_notice():
    # A bank's fixed-deposit fraud page carries "fraud" and "deposit" but is no statement
    # about what the employer charges candidates.
    reading = fraud_notice("google/fraud_notice_not_recruitment.json")
    assert rules_of(reading) == []
    assert reading.facts["notice"] is None
    assert reading.note == "no recruitment-fraud notice on brand.example"


def test_a_bank_page_on_fake_loan_or_deposit_offers_is_not_a_recruitment_notice():
    # "offers" alone is no recruitment word, so neither page is quoted as the employer's
    # statement on fees, though both carry a fee phrase.
    reading = fraud_notice("google/fraud_notice_customer_offers.json")
    assert rules_of(reading) == []
    assert reading.facts["notice"] is None


def test_a_recruitment_word_away_from_the_fraud_term_does_not_make_a_notice():
    page = notice(
        "Beware of fake loan offers. We are hiring relationship managers: see our careers page.",
        title="Loan fraud alert | Brand Bank",
    )
    assert rules_of(fraud_notice(page)) == []


def test_fake_offer_letters_are_a_recruitment_notice():
    page = notice("Brand never charges candidates.", title="Beware of fake offer letters | Brand")
    assert rules_of(fraud_notice(page)) == ["fee_contradicts_employer"]


def test_a_third_party_page_is_never_the_employers_notice():
    response = fixture("google/fraud_notice_fee.json")
    for result in response["organic_results"]:
        result["link"] = result["link"].replace("careers.brand.example", "news.example")
    assert rules_of(fraud_notice(response)) == []


def test_no_results_means_no_notice():
    assert rules_of(fraud_notice({})) == []


# ---- google: confirm_sender_domain --------------------------------------------------------


def confirm(name, candidate, contacts=()):
    return read_confirm_sender_domain(
        fixture(name) if isinstance(name, str) else name,
        params=confirm_sender_domain_params("brand.example", candidate),
        retrieved_at=AT,
        official_domain="brand.example",
        candidate=candidate,
        contacts=list(contacts),
    )


def test_the_official_site_naming_the_candidate_as_fake_replaces_the_lookalike():
    reading = confirm("google/confirm_named_in_notice.json", "brand-careers.example")
    signal = only(reading, "domain_named_in_fraud_notice")
    assert signal["domain"] == "brand-careers.example"
    assert signal["evidence"]["link"] == "https://www.brand.example/fraud-alert"
    assert signal["evidence"]["query"] == 'site:brand.example "brand-careers.example"'
    assert reading.facts == {
        "domain": "brand-careers.example",
        "outcome": "domain_named_in_fraud_notice",
        "emailsElsewhere": [],
    }


def test_the_official_site_mentioning_the_candidate_clears_it_to_sender_official():
    reading = confirm("google/confirm_second_official.json", "brand-hiring.example")
    assert only(reading, "sender_official")["domain"] == "brand-hiring.example"
    assert "withdrawn" in reading.note


def test_no_mention_leaves_the_lookalike_at_moderate():
    reading = confirm("google/confirm_no_mention.json", "brand-careers.example")
    assert reading.signals == ()
    assert reading.facts["outcome"] == "sender_lookalike"
    assert "stays moderate" in reading.note


def test_a_candidate_given_as_an_email_is_confirmed_by_its_domain():
    reading = confirm("google/confirm_named_in_notice.json", "hr.onboarding@brand-careers.example")
    assert only(reading, "domain_named_in_fraud_notice")["domain"] == "brand-careers.example"


def test_a_cleared_domain_vouches_for_a_sender_writing_from_it_or_from_nothing_else():
    for contacts in ([], [email("hr@brand-hiring.example")], [email("hr@brand.example")]):
        reading = confirm("google/confirm_second_official.json", "brand-hiring.example", contacts)
        assert "setAside" not in only(reading, "sender_official")
        assert reading.facts["emailsElsewhere"] == []


def test_a_cleared_link_domain_does_not_vouch_for_an_email_sent_from_elsewhere():
    reading = confirm(
        "google/confirm_second_official.json",
        "brand-hiring.example",
        [email("recruiter@talentbridge-hr.example")],
    )
    signal = only(reading, "sender_official")
    assert signal["domain"] == "brand-hiring.example"
    assert signal["setAside"] == (
        "the recruiter writes from talentbridge-hr.example, "
        "not from brand.example or brand-hiring.example"
    )
    assert reading.facts["emailsElsewhere"] == ["talentbridge-hr.example"]
    assert reading.note.endswith("so the sender is not vouched for")


def numbered(*readings):
    signals = [s for reading in readings for s in reading.signals]
    return [{**s, "id": f"sig_{i}"} for i, s in enumerate(signals, 1)]


def test_a_second_official_domain_is_trusted_no_more_than_the_primary_one():
    contacts = [email("recruiter@talentbridge-hr.example")]
    office_in_noida = office(
        {"local_results": [{"title": "Brand", "place_id": "p1", "address": "Sector 62, Noida"}]},
        company="Brand",
        city="Noida",
    )

    second = official_site(
        "google/official_site_brand.json",
        "Brand",
        contacts,
        [link("https://brand-hiring.example/apply/123")],
    )
    assert rules_of(second) == ["sender_lookalike"]
    cleared = confirm("google/confirm_second_official.json", "brand-hiring.example", contacts)
    via_second = decide(numbered(second, cleared, office_in_noida))

    primary = official_site(
        "google/official_site_brand.json",
        "Brand",
        contacts,
        [link("https://careers.brand.example/apply/123")],
    )
    assert rules_of(primary) == []
    via_primary = decide(numbered(primary, office_in_noida))

    assert via_second.band == via_primary.band == "unverified"
    assert (
        "sender_lookalike set aside: replaced by sender_official for brand-hiring.example"
        in via_second.reasons
    )
    assert not any(r.startswith("sender_official (") for r in via_second.reasons)


# ---- google: check_contact_footprint ------------------------------------------------------


def footprint(response, contact):
    return read_contact_footprint(
        response,
        params=check_contact_footprint_params(contact),
        retrieved_at=AT,
        contact=contact,
    )


def test_a_contact_on_a_page_next_to_a_scam_report():
    contact = email("hr.desk@quickhire.example")
    reading = footprint(fixture("google/contact_footprint_reported.json"), contact)
    signal = only(reading, "contact_reported")
    assert signal["evidence"]["link"] == "https://forum.example/t/job-scam-hr-desk"
    assert reading.facts == {"reports": 1}


def test_a_contact_on_a_page_with_no_fraud_term_is_not_reported():
    contact = email("hr.desk@quickhire.example")
    response = fixture("google/contact_footprint_reported.json")
    response["organic_results"][1]["title"] = "HR contacts"
    response["organic_results"][1]["snippet"] = "Write to hr.desk@quickhire.example for jobs."
    assert rules_of(footprint(response, contact)) == []


def test_a_phone_matches_in_any_spacing_with_or_without_the_country_code():
    # Built at run time from zeros: no subscriber number appears in the tree.
    body = "0" * 9 + "7"
    contact = {"kind": "phone", "value": f"+91 {body[:5]} {body[5:]}", "synthetic": False}
    for written in (
        f"+91-{body}",
        f"{body[:5]} {body[5:]}",
        f"0{body}",
        f"(91) {body[:5]}-{body[5:]}",
    ):
        page = {"title": "Scam number", "link": "https://forum.example/t/1", "snippet": written}
        assert rules_of(footprint({"organic_results": [page]}, contact)) == ["contact_reported"]
    other = {"title": "Scam number", "link": "https://forum.example/t/2", "snippet": body + "1"}
    assert rules_of(footprint({"organic_results": [other]}, contact)) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "Fraudsters send offers from hr@contoso.example.careers.example, a fake domain.",
        "Fake offers came from fakehr@contoso.example",
        "Scam mails from hr@contoso.example-jobs.example",
        "Beware of hr.team+hr@contoso.example scam",
    ],
)
def test_a_longer_address_holding_the_recruiters_is_not_the_recruiters(snippet):
    page = {"title": "Scam alert", "link": "https://forum.example/t/3", "snippet": snippet}
    assert rules_of(footprint({"organic_results": [page]}, email("hr@contoso.example"))) == []


@pytest.mark.parametrize(
    "snippet", ["Scam alert: hr@contoso.example.", "Fake HR (HR@Contoso.example) asks for fees"]
)
def test_the_recruiters_whole_address_in_any_case_is_reported(snippet):
    page = {"title": "Scam alert", "link": "https://forum.example/t/4", "snippet": snippet}
    contact = email("hr@contoso.example")
    assert rules_of(footprint({"organic_results": [page]}, contact)) == ["contact_reported"]


# ---- google_jobs --------------------------------------------------------------------------


def jobs(name, *, company, role, city, official, offered, with_company=True):
    response = fixture(name) if isinstance(name, str) else name
    params = check_job_listings_params(role, city, company if with_company else None)
    return read_job_listings(
        response,
        params=params,
        retrieved_at=AT,
        company=company,
        role=role,
        city=city,
        official_domain=official,
        offered_monthly_inr=offered,
    )


def sample_b_jobs(response="google_jobs/listings_contoso.json", official="contoso.example"):
    return jobs(
        response,
        company="Contoso",
        role="Graduate Engineer Trainee",
        city="Pune",
        official=official,
        offered=35_000,
    )


def sample_c_jobs(response="google_jobs/listings_customer_support_indore.json", offered=42_000):
    return jobs(
        response,
        company="Zorvanta Support Services Pvt. Ltd",
        role="Customer Support Executive",
        city="Indore",
        official=None,
        offered=offered,
        with_company=False,
    )


def test_sample_b_listing_applies_on_the_official_domain():
    reading = sample_b_jobs()
    assert rules_of(reading) == ["listing_match"]
    evidence = only(reading, "listing_match")["evidence"]
    assert (
        evidence["link"] == "https://careers.contoso.example/jobs/graduate-engineer-trainee-pune"
    )
    assert evidence["title"] == "Graduate Engineer Trainee"
    assert evidence["engine"] == "google_jobs"
    assert evidence["query"] == "Graduate Engineer Trainee Contoso"
    assert reading.facts["payCoverage"] == "pay found in 3 of 3 listings"
    assert "Contoso's listing applies on contoso.example" in reading.note


def test_sample_c_no_listing_and_pay_far_above_the_city_median():
    reading = sample_c_jobs()
    assert rules_of(reading) == ["no_listing_match", "pay_outlier"]
    assert reading.facts["payCoverage"] == "pay found in 4 of 6 listings"
    assert reading.facts["medianMonthlyInr"] == 17_250
    assert reading.facts["enoughPayData"] is True
    outlier = only(reading, "pay_outlier")
    assert "₹42,000" in outlier["detail"] and "2.4x" in outlier["detail"]
    assert "median ₹17,250" in outlier["detail"]
    # Each of the three pay sources contributes.
    for source in ("(detected_extensions.salary)", "(extensions)", "(description)"):
        assert source in outlier["evidence"]["snippet"]
    assert only(reading, "no_listing_match")["evidence"]["link"] is None


def test_pay_at_exactly_twice_the_median_is_not_an_outlier():
    assert "pay_outlier" not in rules_of(sample_c_jobs(offered=34_500))
    assert "pay_outlier" in rules_of(sample_c_jobs(offered=34_501))


def test_too_few_listings_with_pay_says_not_enough_pay_data():
    reading = sample_c_jobs("google_jobs/listings_sparse_pay.json")
    assert rules_of(reading) == ["no_listing_match"]
    assert reading.facts["payCoverage"] == "pay found in 2 of 3 listings"
    assert reading.note.endswith("pay found in 2 of 3 listings: not enough pay data")


def test_empty_jobs_results_is_no_listing_and_no_pay_data():
    reading = sample_c_jobs("google_jobs/listings_empty.json")
    assert rules_of(reading) == ["no_listing_match"]
    assert reading.facts["listings"] == 0
    assert reading.facts["medianMonthlyInr"] is None
    assert "pay found in 0 of 0 listings: not enough pay data" in reading.note


def test_a_listing_that_applies_only_on_a_job_portal_fires_neither_listing_rule():
    response = fixture("google_jobs/listings_contoso.json")
    response["jobs_results"][0]["apply_options"] = [
        {"link": "https://jobs.portal.example/contoso-get-pune"}
    ]
    reading = sample_b_jobs(response)
    assert rules_of(reading) == []
    assert reading.facts["companyListings"][0]["company_name"] == "Contoso"
    assert "no apply option on its official domain" in reading.note


def test_without_an_official_domain_a_company_listing_is_never_a_match():
    assert rules_of(sample_b_jobs(official=None)) == []


def test_no_offered_pay_means_no_outlier_check():
    assert rules_of(sample_c_jobs(offered=None)) == ["no_listing_match"]


def test_the_companys_listing_in_another_city_is_not_near_the_claimed_one():
    response = {
        "jobs_results": [
            {
                "title": "Graduate Engineer Trainee",
                "company_name": "Contoso",
                "location": "Bengaluru, Karnataka, India",
                "apply_options": [{"link": "https://careers.contoso.example/jobs/get-blr"}],
            }
        ]
    }
    reading = sample_b_jobs(response)
    assert rules_of(reading) == ["no_listing_match"]
    assert only(reading, "no_listing_match")["detail"] == (
        "no listing by Contoso for Graduate Engineer Trainee among 0 listings near Pune"
    )
    assert reading.facts["nearCity"] == 0
    assert reading.note.startswith("1 listings, 0 of them near Pune;")


def test_a_remote_listing_is_not_near_the_claimed_city():
    response = fixture("google_jobs/listings_contoso.json")
    response["jobs_results"][0]["location"] = "Anywhere"
    assert rules_of(sample_b_jobs(response)) == ["no_listing_match"]


def test_a_suburb_the_city_list_does_not_know_is_near_the_city():
    response = fixture("google_jobs/listings_contoso.json")
    response["jobs_results"][0]["location"] = "Hinjewadi, Maharashtra, India"
    assert rules_of(sample_b_jobs(response)) == ["listing_match"]


def test_listings_in_another_city_or_remote_leave_the_pay_benchmark():
    response = fixture("google_jobs/listings_customer_support_indore.json")
    response["jobs_results"][0]["location"] = "Mumbai, Maharashtra, India"
    response["jobs_results"][3]["location"] = "Anywhere"
    reading = sample_c_jobs(response)
    assert rules_of(reading) == ["no_listing_match"]
    assert reading.facts["payCoverage"] == "pay found in 2 of 4 listings"
    assert reading.facts["enoughPayData"] is False
    assert reading.note.startswith("6 listings, 4 of them near Indore;")


def test_without_a_claimed_city_every_listing_counts():
    reading = jobs(
        "google_jobs/listings_customer_support_indore.json",
        company="Zorvanta Support Services",
        role="Customer Support Executive",
        city=None,
        official=None,
        offered=None,
        with_company=False,
    )
    assert reading.facts["nearCity"] == reading.facts["listings"] == 6
    assert only(reading, "no_listing_match")["detail"].endswith("among 6 listings in India")


# ---- google_maps --------------------------------------------------------------------------


def office(name, company="Contoso", city="Pune"):
    return read_office(
        fixture(name) if isinstance(name, str) else name,
        params=check_office_params(company, city),
        retrieved_at=AT,
        company=company,
        city=city,
    )


def test_local_results_find_the_company_in_the_claimed_city():
    reading = office("google_maps/office_contoso_local.json")
    signal = only(reading, "office_found")
    assert reading.facts == {
        "title": "Contoso Pune Office",
        "place_id": "ChIJsyntheticContosoPune01",
        "type": "Corporate office",
        "address": "Hinjewadi Phase 2, Pune, Maharashtra 411057",
        "rating": 4.2,
        "reviews": 318,
    }
    assert signal["evidence"]["link"] == maps_link("ChIJsyntheticContosoPune01")
    assert signal["evidence"]["snippet"] == "Hinjewadi Phase 2, Pune, Maharashtra 411057"
    assert signal["evidence"]["engine"] == "google_maps"


def test_place_results_for_one_exact_match():
    reading = office("google_maps/office_place_results.json")
    assert rules_of(reading) == ["office_found"]
    assert reading.facts["place_id"] == "ChIJsyntheticContosoPune02"


def test_no_results_is_office_not_found():
    reading = office("google_maps/office_empty.json")
    assert rules_of(reading) == ["office_not_found"]
    assert reading.facts["place_id"] is None


def test_sample_c_no_place_named_after_the_firm():
    reading = office(
        "google_maps/office_not_found_indore.json", "Zorvanta Support Services Pvt. Ltd", "Indore"
    )
    signal = only(reading, "office_not_found")
    assert signal["detail"] == (
        "no place named Zorvanta Support Services in Indore among 2 Maps results"
    )
    assert signal["evidence"]["query"] == "Zorvanta Support Services"


def test_a_place_of_the_company_in_another_city_is_not_the_claimed_office():
    assert rules_of(office("google_maps/office_contoso_local.json", city="Nagpur")) == [
        "office_not_found"
    ]


def test_an_old_city_name_in_the_address_is_the_same_city():
    response = {
        "local_results": [
            {
                "title": "Contoso",
                "place_id": "ChIJsyntheticContosoBlr001",
                "address": "Whitefield, Bangalore, Karnataka 560066",
            }
        ]
    }
    assert rules_of(office(response, city="Bengaluru")) == ["office_found"]


def test_a_business_that_only_shares_the_first_word_is_not_the_office():
    response = {"local_results": [{"title": "Contoso Pizza", "place_id": "p1", "address": "Pune"}]}
    assert rules_of(office(response)) == ["office_not_found"]
    lone_word = {"local_results": [{"title": "Zorvanta", "place_id": "p2", "address": "Indore"}]}
    assert rules_of(office(lone_word, "Zorvanta Support Services Pvt. Ltd", "Indore")) == [
        "office_not_found"
    ]


def test_a_place_named_with_its_locality_is_the_office():
    response = {
        "local_results": [
            {"title": "Contoso Kharadi", "place_id": "p3", "address": "Kharadi, Pune 411014"}
        ]
    }
    assert rules_of(office(response)) == ["office_found"]


def test_a_matching_place_without_a_place_id_is_the_office_with_no_maps_link():
    reading = office({"local_results": [{"title": "Contoso", "address": "Baner, Pune"}]})
    signal = only(reading, "office_found")
    assert signal["evidence"]["link"] is None
    assert reading.facts["place_id"] is None


# ---- google_maps_reviews ------------------------------------------------------------------


def reviews(name, term="fee"):
    return read_office_reviews(
        fixture(name) if isinstance(name, str) else name,
        params=scan_office_reviews_params("ChIJsyntheticContosoPune01", term),
        retrieved_at=AT,
        term=term,
    )


def test_reviews_that_mention_the_fee():
    reading = reviews("google_maps_reviews/reviews_fee.json")
    evidence = only(reading, "reviews_mention_fees")["evidence"]
    assert evidence["snippet"].startswith("They asked me to pay a registration fee")
    assert evidence["date"] == "2026-08-14T09:30:00Z"
    assert evidence["link"].endswith("synthetic-review-002")
    assert evidence["query"] == "fee"
    assert reading.facts == {"matching": 2, "reviews": 3}


def test_feedback_is_not_a_fee():
    assert rules_of(reviews("google_maps_reviews/reviews_feedback_only.json")) == []


def test_the_fraud_term():
    response = {"reviews": [{"snippet": "Total fraud, avoid.", "rating": 1, "link": "x"}]}
    assert rules_of(reviews(response, "fraud")) == ["reviews_mention_fees"]
    assert rules_of(reviews(response, "fee")) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "These people are fraudsters, they took Rs 3000 from me.",
        "Fraudulent company, asked registration charges.",
        "Frauds everywhere.",
    ],
)
def test_the_fraud_term_reads_as_a_word_start(snippet):
    response = {"reviews": [{"snippet": snippet, "rating": 1, "link": "x"}]}
    assert rules_of(reviews(response, "fraud")) == ["reviews_mention_fees"]


# ---- google_news --------------------------------------------------------------------------


def news(name, company="Brand"):
    return read_scam_reports(
        fixture(name),
        params=check_scam_reports_params(company),
        retrieved_at=AT,
        company=company,
    )


def test_news_of_fake_offers_in_the_company_name():
    reading = news("google_news/reports_brand.json")
    signal = only(reading, "impersonation_reports")
    assert signal["evidence"] == {
        "engine": "google_news",
        "query": '"Brand" job offer (fake OR scam OR fraud)',
        "title": "Fake job offers in Brand's name: police warn job seekers in Noida",
        "link": "https://times.example/2026/09/fake-brand-job-offers",
        "snippet": "The Example Times",
        "date": "09/12/2026, 05:30 AM, +0000 UTC",
        "retrievedAt": AT,
    }
    assert [r["source"] for r in reading.facts["reports"]] == [
        "The Example Times",
        "Example City News",
    ]


def test_company_news_and_unrelated_fraud_news_are_not_reports():
    reading = news("google_news/reports_none.json")
    assert rules_of(reading) == []
    assert reading.note == "no news reports of fake offers in Brand's name"


def test_empty_news_results():
    assert rules_of(news("google_news/reports_empty.json")) == []


def test_reports_about_another_company_do_not_count():
    assert rules_of(news("google_news/reports_brand.json", "Contoso")) == []


def headline(title, company="Contoso"):
    return read_scam_reports(
        {"news_results": [{"title": title, "link": "https://news.example/a"}]},
        params=check_scam_reports_params(company),
        retrieved_at=AT,
        company=company,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Fake Contoso job offers ask graduates for a joining fee, company warns",
        "Fraudsters posing as Contoso recruiters dupe 40 freshers",
        "Contoso warns of recruitment fraud in its name",
        "Job offer scam in Contoso's name busted in Pune",
        "Beware of fake Contoso offer letters, police say",
    ],
)
def test_headlines_that_report_fake_offers(title):
    assert rules_of(headline(title)) == ["impersonation_reports"]


@pytest.mark.parametrize(
    "title",
    [
        "Contoso shares fall after accounting fraud probe; job cuts feared",
        "Contoso shares fall after accounting fraud probe and job cuts",
        "Contoso warns customers of fake discount offers",
        "Contoso CEO says fake news about layoffs is false; hiring continues",
    ],
)
def test_fraud_headlines_about_something_other_than_fake_offers(title):
    assert rules_of(headline(title)) == []


def test_a_longer_company_name_can_sit_between_the_fraud_and_the_job_word():
    title = "Fake Zorvanta Support Services job offers doing the rounds"
    assert rules_of(headline(title, "Zorvanta Support Services")) == ["impersonation_reports"]


# ---- every fixture ------------------------------------------------------------------------

FIXTURE_FILES = sorted(
    p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*.json") if p.name != ROUTES_FILE
)


def test_there_is_a_fixture_per_engine():
    assert {f.split("/")[0] for f in FIXTURE_FILES} >= set(RESTRICTORS)


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_every_fixture_is_marked_synthetic_and_holds_no_personal_data(name):
    text = (FIXTURES / name).read_text()
    data = json.loads(text)
    assert data["_synthetic"].startswith("Synthetic fixture, not a real SerpApi response")
    assert "api_key" not in text
    assert all(e.endswith(".example") for e in re.findall(r"[\w.+-]+@([\w.-]+)", text))
    for review in data.get("reviews", []):
        assert set(review) <= {"snippet", "rating", "iso_date", "link"}


def all_readings():
    b, a = claims_of("b.txt"), claims_of("a.txt")
    return [
        read_sample("a.txt", "google/official_site_brand.json"),
        read_sample("b.txt", "google/official_site_contoso.json"),
        read_sample("c.txt", "google/official_site_no_footprint.json"),
        official_site("google/official_site_brand.json", "Brand", [email("hr@gmail.com")]),
        fraud_notice("google/fraud_notice_fee.json"),
        fraud_notice("google/fraud_notice_no_fee_phrase.json"),
        confirm("google/confirm_named_in_notice.json", a["contacts"][0]["value"]),
        confirm("google/confirm_second_official.json", "brand-hiring.example"),
        footprint(
            fixture("google/contact_footprint_reported.json"), email("hr.desk@quickhire.example")
        ),
        sample_b_jobs(),
        sample_c_jobs(),
        office("google_maps/office_contoso_local.json", b["company"]["value"]),
        office("google_maps/office_not_found_indore.json", "Zorvanta Support Services", "Indore"),
        reviews("google_maps_reviews/reviews_fee.json"),
        news("google_news/reports_brand.json"),
    ]


def test_every_signal_cites_its_evidence():
    signals = [s for reading in all_readings() for s in reading.signals]
    assert {s["rule"] for s in signals} == SEARCH_RULES
    for s in signals:
        rule = RULES[s["rule"]]
        assert s["evidence"].keys() == EVIDENCE_KEYS
        assert s["source"] == s["evidence"]["engine"] == rule.engine
        assert (s["direction"], s["severity"]) == (rule.direction, rule.severity)
        assert (s["id"], s["stale"]) == (None, False)
        assert s["evidence"]["retrievedAt"] == AT
        assert s["evidence"]["query"]
        assert s["detail"]


def test_every_reading_is_plain_json():
    for reading in all_readings():
        json.dumps([reading.signals, reading.facts, reading.note])
