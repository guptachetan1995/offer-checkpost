import pytest

from offer_checkpost.domains import (
    FRAUD_TERMS,
    FREE_MAIL,
    classify_domain,
    confirm_outcome,
    damerau_levenshtein,
    host_of,
    is_free_mail,
    is_official,
    lookalike_reason,
    registrable_domain,
)


def organic(link, title="", snippet=""):
    return {"title": title, "link": link, "snippet": snippet}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("brand.com", "brand.com"),
        ("careers.brand.com", "brand.com"),
        ("a.b.careers.brand.com", "brand.com"),
        ("WWW.Brand.COM", "brand.com"),
        ("brand.com.", "brand.com"),
        ("https://careers.brand.com:443/jobs?id=7#apply", "brand.com"),
        ("careers.brand.com/jobs/7", "brand.com"),
        ("hr.onboarding@brand-careers.example", "brand-careers.example"),
        ("mailto:hr.onboarding@brand-careers.example", "brand-careers.example"),
        ("brand.co.in", "brand.co.in"),
        ("careers.brand.co.in", "brand.co.in"),
        ("https://www.brand.co.in/", "brand.co.in"),
        ("hr@jobs.brand.co.in.example", "in.example"),
        ("yahoo.co.in", "yahoo.co.in"),
        ("careers.brand.co.uk", "brand.co.uk"),
        ("brand-careers.github.io", "brand-careers.github.io"),
        ("apply.brand-careers.netlify.app", "brand-careers.netlify.app"),
        ("https://contosostaffing.wixsite.com/home", "contosostaffing.wixsite.com"),
        ("brand-careers.wordpress.com", "brand-careers.wordpress.com"),
    ],
)
def test_registrable_domain(value, expected):
    assert registrable_domain(value) == expected


@pytest.mark.parametrize(
    "value",
    ["co.in", "www.co.in", "github.io", "com", "localhost", "192.0.2.1", "[::1]", "", "a b.com"],
)
def test_no_registrable_domain(value):
    assert registrable_domain(value) is None


def test_host_keeps_subdomains_but_drops_www():
    assert host_of("https://www.careers.brand.com/x") == "careers.brand.com"
    assert host_of("https://www.brand.com/") == "brand.com"


def test_careers_subdomain_counts_as_official():
    assert is_official("careers.brand.com", "brand.com")
    assert classify_domain("careers.brand.com", "brand.com") == "subdomain"
    assert lookalike_reason("careers.brand.com", "brand.com") is None


def test_official_domain_given_as_the_knowledge_graph_website():
    website = "https://www.brand.example/in/"
    assert classify_domain("hr@brand.example", website) == "official"
    assert classify_domain("hr@careers.brand.example", website) == "subdomain"
    assert classify_domain("https://www.brand.example/jobs", website) == "official"


def test_co_in_official_domain_and_its_subdomains():
    assert classify_domain("brand.co.in", "https://www.brand.co.in/") == "official"
    assert classify_domain("mail.brand.co.in", "brand.co.in") == "subdomain"
    assert not is_official("other.co.in", "brand.co.in")


def test_co_in_is_never_mistaken_for_a_registrable_domain():
    assert not is_official("fake.co.in", "brand.co.in")
    assert classify_domain("co.in", "brand.co.in") is None


@pytest.mark.parametrize("suffix", ["com.in", "biz.in", "info.in", "pro.in", "io.in", "ai.in"])
def test_the_other_in_second_level_suffixes_are_public(suffix):
    assert registrable_domain(f"careers.brand.{suffix}") == f"brand.{suffix}"
    assert registrable_domain(suffix) is None
    assert not is_official(f"hr@scam.{suffix}", f"https://www.brand.{suffix}")


def test_two_com_in_domains_are_not_official_to_each_other():
    assert classify_domain("hr@scam.com.in", "https://www.brand.com.in") == "unrelated"
    assert classify_domain("hr@careers.brand.com.in", "brand.com.in") == "subdomain"
    assert classify_domain("hr@brand.com.in", "brand.com") == "lookalike"


@pytest.mark.parametrize(
    ("value", "official"),
    [
        ("hr.onboarding@brand-careers.example", "brand.com"),
        ("brand-careers.example", "brand.com"),
        ("brnad.com", "brand.com"),
        ("brand.co.in", "brand.com"),
        ("brand-hr.co.in", "brand.com"),
        ("https://brand-offers.github.io/apply", "brand.com"),
    ],
)
def test_lookalike_candidates(value, official):
    assert classify_domain(value, official) == "lookalike"


def test_lookalike_reasons_read_as_trace_lines():
    assert (
        lookalike_reason("brand-careers.example", "brand.com")
        == "'brand-careers' contains the brand token 'brand'"
    )
    assert lookalike_reason("brnad.com", "brand.com") == "'brnad' is 1 edit from 'brand'"
    assert lookalike_reason("abdcefgih.example", "abcdefghi.com") == (
        "'abdcefgih' is 2 edits from 'abcdefghi'"
    )


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        # A Cyrillic "\u0430", and an accented "\u00e4", in place of the Latin "a".
        ("http://br\u0430nd.com/", "'br\u0430nd' is 'brand' written with look-alike letters"),
        ("hr@br\u00e4nd.com", "'br\u00e4nd' is 'brand' written with look-alike letters"),
        ("hr@xn--brnd-6qa.com", "'br\u00f6nd' is 1 edit from 'brand'"),
    ],
)
def test_unicode_and_punycode_hosts_are_compared_as_look_alikes(value, reason):
    assert classify_domain(value, "brand.com") == "lookalike"
    assert lookalike_reason(value, "brand.com") == reason


def test_a_unicode_host_is_kept_in_its_punycode_form():
    assert host_of("https://br\u0430nd.com/apply") == "xn--brnd-63d.com"
    assert registrable_domain("hr@careers.br\u00e4nd.com") == "xn--brnd-moa.com"


def test_tvs_is_never_a_lookalike_of_tcs():
    assert damerau_levenshtein("tvs", "tcs") == 1
    assert lookalike_reason("tvs.com", "tcs.com") is None
    assert classify_domain("tvs.com", "tcs.com") == "unrelated"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("tcs-careers.example", True),
        ("hr-tcs.example", True),
        ("jobs-tcs-india.example", True),
        ("tcs.co.in", True),
        ("mytcs.example", False),
        ("tcsjobs.example", False),
        ("tcs2026.example", False),
        ("tcsion.com", False),
    ],
)
def test_short_brand_token_matches_only_as_a_delimited_word(value, expected):
    assert (lookalike_reason(value, "tcs.com") is not None) is expected


@pytest.mark.parametrize(
    ("official", "value", "expected"),
    [
        # 4 characters or fewer: no fuzzy match at any distance.
        ("abcd.com", "abdc.example", False),
        ("abcd.com", "abce.example", False),
        # 5-8 characters: distance 1.
        ("brand.com", "brnad.example", True),
        ("brand.com", "bran.example", True),
        ("brand.com", "bxanx.example", False),
        ("abcdefgh.com", "abcdefhg.example", True),
        ("abcdefgh.com", "abdcefhg.example", False),
        # 9+ characters: distance 2.
        ("abcdefghi.com", "abcdefgh.example", True),
        ("abcdefghi.com", "abdcefgih.example", True),
        ("abcdefghi.com", "badcefgih.example", False),
    ],
)
def test_distance_bands_by_official_label_length(official, value, expected):
    assert (lookalike_reason(value, official) is not None) is expected


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [
        ("", "", 0),
        ("", "abc", 3),
        ("brand", "brand", 0),
        ("brnad", "brand", 1),
        ("kitten", "sitting", 3),
        ("ca", "abc", 2),
        ("abdcefgih", "abcdefghi", 2),
    ],
)
def test_damerau_levenshtein(a, b, distance):
    assert damerau_levenshtein(a, b) == distance
    assert damerau_levenshtein(b, a) == distance


@pytest.mark.parametrize(
    "value",
    ["gmail.com", "yahoo.co.in", "rediffmail.com", "outlook.com", "https://mail.gmail.com/"],
)
def test_free_mail(value):
    assert is_free_mail(value)
    assert classify_domain(value, "brand.com") == "free_mail"


@pytest.mark.parametrize("value", ["gmail-hr.example", "brand.com", "gmail.example", "co.in"])
def test_not_free_mail(value):
    assert not is_free_mail(value)


def test_free_mail_list_holds_only_registrable_domains():
    assert all(registrable_domain(domain) == domain for domain in FREE_MAIL)


def test_free_mail_outranks_lookalike():
    assert lookalike_reason("yahoo.co.in", "yahoo.com") is not None
    assert classify_domain("yahoo.co.in", "yahoo.com") == "free_mail"


def test_unrelated_domain():
    assert classify_domain("recruit-hub.example", "brand.com") == "unrelated"


@pytest.mark.parametrize(
    ("candidate", "official", "page"),
    [
        (
            "amazon.jobs",
            "amazon.com",
            "https://www.amazon.com/jobs-help",
        ),
        (
            "infosysbpm.com",
            "infosys.com",
            "https://www.infosys.com/careers/help.html",
        ),
    ],
)
def test_parent_company_second_domain_is_a_candidate_that_confirmation_clears(
    candidate, official, page
):
    assert classify_domain(candidate, official) == "lookalike"
    mention = organic(
        page,
        title="Careers help (synthetic fixture)",
        snippet=f"Openings are also posted on {candidate}. Apply there with your profile.",
    )
    assert confirm_outcome(candidate, official, [mention]) == ("sender_official", mention)


def test_tcsion_is_never_flagged_against_tcs():
    assert classify_domain("tcsion.com", "tcs.com") == "unrelated"


def test_candidate_named_on_the_fraud_page_becomes_named_in_fraud_notice():
    notice = organic(
        "https://www.brand.com/recruitment-fraud",
        title="Recruitment fraud advisory",
        snippet="Emails from brand-careers.example are fake and not from us. We never "
        "charge candidates a fee.",
    )
    assert confirm_outcome("hr.onboarding@brand-careers.example", "brand.com", [notice]) == (
        "domain_named_in_fraud_notice",
        notice,
    )


@pytest.mark.parametrize(
    "sentence",
    [f"brand-careers.example: {term}" for term in FRAUD_TERMS]
    + [
        "Beware of messages from brand-careers.example",
        "Scammers use brand-careers.example to ask for fees",
        "Fraudulent offers arrive from brand-careers.example",
        "brand-careers.example is NOT   AFFILIATED with Brand",
    ],
)
def test_each_fraud_term_next_to_the_candidate(sentence):
    page = organic("https://careers.brand.com/notice", snippet=sentence)
    rule, _ = confirm_outcome("brand-careers.example", "brand.com", [page])
    assert rule == "domain_named_in_fraud_notice"


def test_candidate_in_the_title_next_to_a_fraud_term():
    page = organic("https://brand.com/n", title="Beware of brand-careers.example", snippet="")
    rule, _ = confirm_outcome("brand-careers.example", "brand.com", [page])
    assert rule == "domain_named_in_fraud_notice"


@pytest.mark.parametrize(
    "snippet",
    [
        "Brand recruits only through brand.com and brand-jobs.example, so beware of fake "
        "emails from any other domain.",
        "Offers from brand-jobs.example are genuine, not fake.",
        "Beware of fake emails from any domain other than careers.brand.com and "
        "brand-jobs.example.",
        "Our official hiring site brand-jobs.example never asks for fees, so beware of fakes.",
    ],
)
def test_a_sentence_vouching_for_the_domains_it_lists_does_not_condemn_them(snippet):
    # The fraud term in these sentences is about other domains: the employer lists its
    # genuine ones next to it, with the official domain or a word that vouches before it.
    page = organic(
        "https://www.brand.com/fraud-advisory",
        title="Recruitment fraud advisory",
        snippet=snippet,
    )
    assert confirm_outcome("brand-jobs.example", "brand.com", [page]) == (
        "sender_lookalike",
        None,
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "brand-careers.example is a fake domain, not our official site.",
        "Beware: brand-careers.example is not Brand's official site.",
        "Fraudsters write from brand-careers.example, brand-hr.example and brand-jobs.example.",
    ],
)
def test_a_vouching_word_after_the_fraud_term_still_condemns(snippet):
    page = organic("https://www.brand.com/fraud-advisory", snippet=snippet)
    rule, _ = confirm_outcome("brand-careers.example", "brand.com", [page])
    assert rule == "domain_named_in_fraud_notice"


def test_a_fraud_page_that_names_a_domain_away_from_the_fraud_term_keeps_it_moderate():
    # An employer's fraud page may list its genuine domains too, so this mention neither
    # clears the candidate nor condemns it.
    fraud_page = organic(
        "https://www.brand.com/recruitment-fraud",
        title="Recruitment fraud advisory",
        snippet="Beware of fake recruiters. Our emails come only from brand.com or "
        "brand-jobs.example ... We never ask for money.",
    )
    assert confirm_outcome("brand-jobs.example", "brand.com", [fraud_page]) == (
        "sender_lookalike",
        None,
    )


@pytest.mark.parametrize(
    ("candidate", "title", "snippet"),
    [
        (
            "brand-careers.example",
            "Recruitment Fraud Alert | Brand",
            "Some candidates have received offer letters from hr@brand-careers.example. "
            "These offers are not from Brand.",
        ),
        (
            "brand-careers.example",
            "Beware of recruitment fraud",
            "Beware! Emails from brand-careers.example are not from us.",
        ),
        (
            "brand-careers.example",
            "Fake job offers",
            "Fraudulent job offers ... Candidates received mails from brand-careers.example "
            "asking for a fee ...",
        ),
        (
            "brnad.example",
            "Recruitment Fraud Alert | Brand",
            "Emails sent from brnad.example offering jobs did not come from Brand. Brand "
            "never asks candidates for money.",
        ),
        (
            "brnad.example",
            "Beware of fake job offers",
            "Candidates have received offers from brnad.example. Brand never charges any fee.",
        ),
        (
            "brnad.example",
            "Fraudulent job offers",
            "The following domains do not belong to Brand: brnad.example, brand-jobs.example.",
        ),
        (
            "brnad.example",
            "Recruitment scam warning",
            "brnad.example; brand-hr.example; brandcareers.example",
        ),
        ("brnad.example", "Beware!", "Emails from brnad.example are not from us"),
    ],
)
def test_a_domain_on_a_fraud_page_is_never_cleared_as_official(candidate, title, snippet):
    page = organic("https://www.brand.com/fraud-alert", title=title, snippet=snippet)
    assert confirm_outcome(candidate, "brand.com", [page]) == ("sender_lookalike", None)


def test_a_mention_on_a_fraud_page_outweighs_a_plain_mention_elsewhere():
    plain = organic("https://brand.com/jobs", snippet="Openings are also on brand-jobs.example.")
    fraud_page = organic(
        "https://brand.com/fraud",
        title="Fraud advisory",
        snippet="Offers come only from brand-jobs.example.",
    )
    assert confirm_outcome("brand-jobs.example", "brand.com", [plain, fraud_page]) == (
        "sender_lookalike",
        None,
    )


def test_fraud_mention_wins_over_a_plain_mention_in_another_result():
    plain = organic("https://brand.com/a", snippet="See brand-careers.example for details.")
    fraud = organic("https://brand.com/b", snippet="brand-careers.example is a scam.")
    assert confirm_outcome("brand-careers.example", "brand.com", [plain, fraud]) == (
        "domain_named_in_fraud_notice",
        fraud,
    )


def test_a_third_party_page_is_never_the_employers_statement():
    third_party = organic(
        "https://news.example/story", snippet="brand-careers.example is a scam, say readers."
    )
    assert confirm_outcome("brand-careers.example", "brand.com", [third_party]) == (
        "sender_lookalike",
        None,
    )


def test_no_mention_leaves_the_lookalike_moderate():
    unrelated = organic("https://brand.com/careers", snippet="Beware of fake offers.")
    assert confirm_outcome("brand-careers.example", "brand.com", [unrelated]) == (
        "sender_lookalike",
        None,
    )
    assert confirm_outcome("brand-careers.example", "brand.com", []) == ("sender_lookalike", None)


@pytest.mark.parametrize(
    "snippet",
    [
        "Fake offers use my-brand-careers.example, a different site.",
        "Fake offers use brand-careers.example.org, a different site.",
        "Fake offers use brand-careers.examples, a different site.",
    ],
)
def test_only_the_whole_candidate_domain_counts_as_a_mention(snippet):
    page = organic("https://brand.com/n", snippet=snippet)
    assert confirm_outcome("brand-careers.example", "brand.com", [page]) == (
        "sender_lookalike",
        None,
    )


def test_a_subdomain_of_the_candidate_counts_as_a_mention():
    page = organic(
        "https://brand.com/n", snippet="Fake offers come from hr.brand-careers.example."
    )
    rule, _ = confirm_outcome("brand-careers.example", "brand.com", [page])
    assert rule == "domain_named_in_fraud_notice"
