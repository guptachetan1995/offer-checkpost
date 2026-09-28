import itertools
import re
from pathlib import Path

import pytest

from offer_checkpost.checks import FEE_PHRASES, find_fraud_notice_params, read_fraud_notice
from offer_checkpost.extract import TEXT_RULES, extract_claims, text_rules
from offer_checkpost.rules import (
    BAND_TITLES,
    BANDS,
    REPLACEABLE,
    RULES,
    TEXT_SOURCE,
    decide,
    is_decisive,
    signal_for,
    text_signal,
)

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "offers"

# The decision table, row by row: rule, engine (None: the message text), direction, severity.
TABLE = [
    ("fee_requested", None, "red", "strong"),
    ("task_scam_pattern", None, "red", "strong"),
    ("sensitive_docs_early", None, "red", "moderate"),
    ("chat_only_interview", None, "red", "weak"),
    ("sender_lookalike", "google", "red", "moderate"),
    ("domain_named_in_fraud_notice", "google", "red", "strong"),
    ("sender_free_mail", "google", "red", "moderate"),
    ("fee_contradicts_employer", "google", "red", "strong"),
    ("employer_fraud_notice_exists", "google", "red", "weak"),
    ("no_web_footprint", "google", "red", "moderate"),
    ("pay_outlier", "google_jobs", "red", "moderate"),
    ("no_listing_match", "google_jobs", "red", "weak"),
    ("office_not_found", "google_maps", "red", "moderate"),
    ("reviews_mention_fees", "google_maps_reviews", "red", "moderate"),
    ("impersonation_reports", "google_news", "red", "weak"),
    ("contact_reported", "google", "red", "strong"),
    ("recruiter_site_new", "google_about_this_result", "red", "moderate"),
    ("sender_official", "google", "green", None),
    ("listing_match", "google_jobs", "green", None),
    ("office_found", "google_maps", "green", None),
]


def of(direction, severity=None, *, search):
    return [
        name
        for name, engine, d, s in TABLE
        if d == direction
        and (severity is None or s == severity)
        and (engine is not None) == search
    ]


STRONG_SEARCH = of("red", "strong", search=True)
MODERATE_SEARCH = of("red", "moderate", search=True)
WEAK_REDS = of("red", "weak", search=True) + of("red", "weak", search=False)
SERIOUS_REDS = [n for n, _, d, s in TABLE if d == "red" and s != "weak"]
GREENS = of("green", search=True)

# Rules that need another rule to count, and the one given alongside them in these tests.
NEEDS = {
    "fee_contradicts_employer": "fee_requested",
    "employer_fraud_notice_exists": "fee_requested",
}

_ids = itertools.count(1)


def sig(rule, *, domain=None, stale=False, signal_id=None):
    """A hand-built signal, as a reader or the text rules would produce it."""
    r = RULES[rule]
    evidence = (
        {
            "engine": r.engine,
            "query": f"synthetic query for {rule}",
            "title": "Synthetic result",
            "link": "https://results.example/page",
            "snippet": "Synthetic snippet.",
            "retrievedAt": "2026-10-02T10:00:00+05:30",
        }
        if r.from_search
        else {"quote": "synthetic quote", "span": [0, 15]}
    )
    s = signal_for(
        rule, signal_id or f"sig_{next(_ids)}", evidence, domain=domain or f"{rule}.example"
    )
    s["stale"] = stale
    return s


def sigs(*rules):
    return [sig(rule) for rule in rules]


def band(*rules):
    return decide(sigs(*rules)).band


def sample(name):
    return (SAMPLES_DIR / name).read_text(encoding="utf-8")


# ---- the rule table -----------------------------------------------------------------------


def test_the_rule_table_is_the_decision_table_row_for_row():
    assert [(r.name, r.engine, r.direction, r.severity) for r in RULES.values()] == TABLE


def test_the_text_rules_are_exactly_the_ones_extraction_fires():
    assert [n for n, r in RULES.items() if not r.from_search] == list(TEXT_RULES)


def test_every_red_rule_has_a_severity_and_no_green_rule_does():
    for rule in RULES.values():
        assert (rule.severity is None) == (rule.direction == "green"), rule.name


def test_only_the_confirming_rules_replace_and_only_sender_lookalike_is_replaced():
    replacing = {n: r.replaces for n, r in RULES.items() if r.replaces}
    assert replacing == {
        "domain_named_in_fraud_notice": ("sender_lookalike",),
        "sender_official": ("sender_lookalike",),
    }
    assert REPLACEABLE == {"sender_lookalike"}


def test_only_the_fraud_notice_rules_need_a_fee_asked():
    assert {n: r.requires for n, r in RULES.items() if r.requires} == NEEDS


def test_only_the_site_age_rule_is_stretch():
    assert [n for n, r in RULES.items() if r.stretch] == ["recruiter_site_new"]


def test_labels_are_unique_and_list_cleanly():
    labels = [r.label for r in RULES.values()]
    assert len(set(labels)) == len(labels)
    assert not [label for label in labels if "," in label or ";" in label]


def test_the_bands_never_certify_an_offer():
    assert BANDS == ("high_risk", "consistent_with_genuine", "unverified")
    assert set(BAND_TITLES) == set(BANDS)
    assert not {"genuine", "safe"} & set(BANDS)


# ---- signals ------------------------------------------------------------------------------


def test_signal_for_takes_direction_severity_and_source_from_the_table():
    s = signal_for("fee_contradicts_employer", "sig_3", {"engine": "google"})
    assert s == {
        "id": "sig_3",
        "rule": "fee_contradicts_employer",
        "direction": "red",
        "severity": "strong",
        "source": "google",
        "stale": False,
        "evidence": {"engine": "google"},
    }
    green = signal_for("sender_official", "sig_1", {}, domain="brand.example")
    assert (green["direction"], green["severity"], green["domain"]) == (
        "green",
        None,
        "brand.example",
    )


def test_text_signal_quotes_the_sentence_the_rule_fired_on():
    text = sample("a.txt")
    [hit] = text_rules(text)
    s = text_signal(hit, "sig_1")
    assert (s["id"], s["rule"], s["direction"], s["severity"]) == (
        "sig_1",
        "fee_requested",
        "red",
        "strong",
    )
    assert s["source"] == TEXT_SOURCE and s["stale"] is False
    start, end = s["evidence"]["span"]
    assert text[start:end] == s["evidence"]["quote"]
    assert "₹2,499" in s["evidence"]["quote"]


def test_the_table_not_the_signal_decides_severity():
    promoted = sig("sender_lookalike")
    promoted["severity"] = "strong"
    assert decide([sig("fee_requested"), promoted]).band == "unverified"


def test_a_text_rule_passed_as_a_signal_and_as_a_hit_counts_once():
    hits = text_rules(sample("a.txt"))
    signals = [text_signal(hits[0], "sig_1"), sig("sender_lookalike")]
    assert decide(signals, hits).band == "unverified"


# ---- band 1: high_risk ------------------------------------------------------------------


def test_two_strong_text_rules_alone_stay_unverified():
    assert band("fee_requested", "task_scam_pattern") == "unverified"


@pytest.mark.parametrize("rule", STRONG_SEARCH)
def test_a_strong_search_signal_with_a_strong_text_rule_is_high_risk(rule):
    d = decide(sigs("fee_requested", rule))
    assert d.band == "high_risk"
    assert d.summary.startswith("2 strong red signals, 1 of them from a search result: ")


@pytest.mark.parametrize("rule", STRONG_SEARCH)
def test_one_strong_search_signal_alone_is_not_enough(rule):
    assert band(rule) == "unverified"


def test_two_strong_search_signals_are_high_risk():
    d = decide(sigs("domain_named_in_fraud_notice", "contact_reported"))
    assert d.band == "high_risk"
    assert d.summary.startswith("2 strong red signals, 2 of them from search results: ")


@pytest.mark.parametrize(
    ("from_text", "from_search", "expected"),
    [
        (["sensitive_docs_early"], ["no_web_footprint"], "unverified"),
        (["sensitive_docs_early"], ["no_web_footprint", "office_not_found"], "high_risk"),
        (["fee_requested", "sensitive_docs_early"], ["office_not_found"], "unverified"),
        ([], ["no_web_footprint", "office_not_found"], "unverified"),
        ([], ["no_web_footprint", "office_not_found", "pay_outlier"], "high_risk"),
        (["fee_requested"], ["no_web_footprint", "office_not_found"], "high_risk"),
    ],
)
def test_three_serious_red_signals_need_two_from_search(from_text, from_search, expected):
    assert band(*from_text, *from_search) == expected


def test_the_second_high_risk_path_names_itself():
    d = decide(sigs("fee_requested", "no_web_footprint", "office_not_found"))
    assert d.summary == (
        "3 red signals of moderate or strong severity, 2 of them from search results: "
        "fee asked, no web footprint, office not on Maps"
    )


@pytest.mark.parametrize("rule", MODERATE_SEARCH)
def test_every_moderate_search_rule_counts_toward_high_risk(rule):
    other = "no_web_footprint" if rule == "office_not_found" else "office_not_found"
    assert band("sensitive_docs_early", rule, other) == "high_risk"
    assert band("sensitive_docs_early", rule) == "unverified"


@pytest.mark.parametrize("weak", WEAK_REDS)
def test_a_weak_red_never_makes_the_third_serious_signal(weak):
    # Were the weak signal counted, each of these would be 3 serious with 2 from search.
    if RULES[weak].from_search:
        two_serious = ["fee_requested", "office_not_found"]
    else:
        two_serious = ["no_web_footprint", "office_not_found"]
    assert band(*two_serious, weak) == "unverified"


def test_green_signals_never_override_high_risk():
    assert band("fee_requested", "fee_contradicts_employer", *GREENS) == "high_risk"


# ---- band 2: consistent_with_genuine ------------------------------------------------------


@pytest.mark.parametrize("pair", list(itertools.combinations(GREENS, 2)))
def test_two_green_search_signals_are_consistent_with_genuine(pair):
    d = decide(sigs(*pair))
    assert d.band == "consistent_with_genuine"
    assert d.summary.startswith(
        "2 green signals from search results and no strong or moderate red signal: "
    )


@pytest.mark.parametrize("green", GREENS)
def test_one_green_signal_is_not_enough(green):
    assert band(green) == "unverified"


@pytest.mark.parametrize("weak", [w for w in WEAK_REDS if w not in NEEDS])
def test_a_weak_red_does_not_block_consistent_with_genuine(weak):
    d = decide(sigs("listing_match", "office_found", weak))
    assert d.band == "consistent_with_genuine"
    assert d.summary.endswith(f"; weak red signals noted: {RULES[weak].label}")


@pytest.mark.parametrize("red", SERIOUS_REDS)
def test_any_moderate_or_strong_red_blocks_consistent_with_genuine(red):
    extra = [NEEDS[red]] if red in NEEDS else []
    assert band(*GREENS, red, *extra) != "consistent_with_genuine"


def test_a_rule_counts_once_however_many_signals_fired_it():
    # A recruiter email and a job link both on the official domain are one green, not two.
    two_contacts = [
        sig("sender_official", domain="contoso.example"),
        sig("sender_official", domain="contoso.example"),
    ]
    assert decide(two_contacts).band == "unverified"
    two_lookalikes = [
        sig("sensitive_docs_early"),
        sig("sender_lookalike", domain="brand-careers.example"),
        sig("sender_lookalike", domain="brnad.example"),
    ]
    assert decide(two_lookalikes).band == "unverified"


# ---- band 3: unverified -------------------------------------------------------------------


def test_no_signals_is_not_enough_data():
    assert decide() == decide([], [])
    assert decide().band == "unverified"
    assert decide().summary == "not enough data: no rule fired"
    assert decide(company_claimed=False).summary == (
        "not enough data: no rule fired; nothing checkable was claimed"
    )


def test_a_task_scam_alone_is_unverified_with_the_text_only_summary():
    d = decide(text_hits=text_rules(sample("task-per-like.txt")), company_claimed=False)
    assert d.band == "unverified"
    assert d.summary == "text-only red flags: task-scam pattern; nothing checkable was claimed"


def test_the_text_only_summary_lists_every_text_flag_strongest_first():
    d = decide(text_hits=text_rules(sample("task-prepaid-deposit.txt")), company_claimed=False)
    assert d.summary == (
        "text-only red flags: fee asked, task-scam pattern; nothing checkable was claimed"
    )


def test_all_four_text_rules_together_stay_unverified():
    d = decide(sigs(*TEXT_RULES))
    assert d.band == "unverified"
    assert d.summary == (
        "text-only red flags: fee asked, task-scam pattern, documents asked for before any "
        "interview, chat-only interview; no search result confirms or contradicts the claims"
    )


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES_DIR.glob("*.txt")))
def test_no_sample_rises_above_unverified_on_its_text_alone(name):
    text = sample(name)
    hits = text_rules(text)
    d = decide(text_hits=hits, company_claimed=extract_claims(text)["company"] is not None)
    assert d.band == "unverified"
    if hits:
        assert d.summary.startswith("text-only red flags: ")


def test_a_mixed_unverified_case_lists_both_sides():
    d = decide(sigs("chat_only_interview", "office_not_found", "sender_official"))
    assert d.band == "unverified"
    assert d.summary == (
        "not enough for another band; red: office not on Maps, chat-only interview; "
        "green: sender on the official domain"
    )


# ---- set aside: replaced, stale, missing its fee ------------------------------------------


def test_a_domain_named_in_the_fraud_notice_replaces_its_lookalike():
    signals = [
        sig("fee_requested", signal_id="sig_1"),
        sig("sender_lookalike", domain="brand-careers.example", signal_id="sig_2"),
        sig("domain_named_in_fraud_notice", domain="brand-careers.example", signal_id="sig_4"),
    ]
    d = decide(signals)
    assert d.band == "high_risk"
    assert d.evidence_ids == ("sig_1", "sig_4")
    assert d.reasons[-1] == (
        "sender_lookalike set aside: replaced by domain_named_in_fraud_notice "
        "for brand-careers.example"
    )
    assert not any(r.startswith("sender_lookalike (") for r in d.reasons)


def test_a_confirming_sender_official_withdraws_its_lookalike():
    base = [
        sig("sensitive_docs_early"),
        sig("sender_lookalike", domain="brand.jobs"),
        sig("office_not_found"),
    ]
    assert decide(base).band == "high_risk"
    confirmed = decide([sig("sender_official", domain="brand.jobs"), *base])
    assert confirmed.band == "unverified"
    assert (
        "sender_lookalike set aside: replaced by sender_official for brand.jobs"
        in confirmed.reasons
    )


def test_a_set_aside_sender_official_withdraws_its_lookalike_without_counting():
    reason = "the recruiter writes from elsewhere.example, not from brand.example or brand.jobs"
    withdrawal = {**sig("sender_official", domain="brand.jobs"), "setAside": reason}
    signals = [sig("sender_lookalike", domain="brand.jobs"), withdrawal, sig("office_found")]
    d = decide(signals)
    assert d.band == "unverified"
    assert d.summary == "not enough for another band; green: office found on Maps"
    assert d.reasons[-2:] == (
        "sender_lookalike set aside: replaced by sender_official for brand.jobs",
        f"sender_official set aside: {reason}",
    )
    assert decide([*signals, sig("sender_official")]).band == "consistent_with_genuine"


def test_sender_official_on_another_domain_leaves_the_lookalike_standing():
    signals = [
        sig("sensitive_docs_early"),
        sig("sender_official", domain="brand.example"),
        sig("sender_lookalike", domain="brand-careers.example"),
        sig("office_not_found"),
    ]
    assert decide(signals).band == "high_risk"


def test_stale_signals_are_set_aside():
    c = [
        sig("sensitive_docs_early"),
        sig("no_web_footprint"),
        sig("office_not_found", stale=True),
    ]
    d = decide(c)
    assert d.band == "unverified"
    assert "office_not_found set aside: stale, a claim it depended on was corrected" in d.reasons


def test_a_stale_replacement_replaces_nothing():
    signals = [
        sig("sensitive_docs_early"),
        sig("sender_lookalike", domain="brnad.example"),
        sig("sender_official", domain="brnad.example", stale=True),
        sig("office_not_found"),
    ]
    assert decide(signals).band == "high_risk"


@pytest.mark.parametrize("rule", sorted(NEEDS))
def test_a_fraud_notice_rule_without_a_fee_asked_is_set_aside(rule):
    d = decide(sigs(rule, "contact_reported"))
    assert d.band == "unverified"
    assert f"{rule} set aside: it needs fee_requested, which did not fire" in d.reasons
    stale_fee = [sig("fee_requested", stale=True), *sigs(rule, "contact_reported")]
    assert decide(stale_fee).band == "unverified"


def test_evidence_ids_are_the_deciding_signals_in_the_order_given():
    signals = [
        sig("sender_official", signal_id="sig_9"),
        sig("office_found", signal_id="sig_2"),
        sig("impersonation_reports", signal_id="sig_5"),
        sig("listing_match", signal_id="sig_3"),
    ]
    assert decide(signals).evidence_ids == ("sig_9", "sig_2", "sig_3")
    unverified = decide([sig("office_not_found", signal_id="sig_7")], text_rules(sample("c.txt")))
    assert unverified.evidence_ids == ("sig_7",)


# ---- decisive -----------------------------------------------------------------------------


def test_decisive_only_when_high_risk():
    assert not is_decisive(sigs("sensitive_docs_early", "no_web_footprint"))
    assert is_decisive(sigs("sensitive_docs_early", "no_web_footprint", "office_not_found"))
    assert is_decisive(sigs("fee_requested", "fee_contradicts_employer", *GREENS))


def test_a_lookalike_still_open_to_confirmation_is_not_decisive_evidence():
    signals = [
        sig("sensitive_docs_early"),
        sig("sender_lookalike", domain="brand.jobs"),
        sig("office_not_found"),
    ]
    assert decide(signals).band == "high_risk"
    assert not is_decisive(signals, open_domains=["brand.jobs"])
    assert is_decisive(signals, open_domains=[])


# ---- the three demo samples ---------------------------------------------------------------


def test_sample_a_is_high_risk_after_two_searches():
    [fee] = [text_signal(h, "sig_1") for h in text_rules(sample("a.txt"))]
    step0 = [fee]
    d = decide(step0)
    assert d.band == "unverified"
    assert d.summary == (
        "text-only red flags: fee asked; no search result confirms or contradicts the claims"
    )

    lookalike = sig("sender_lookalike", domain="brand-careers.example", signal_id="sig_2")
    step1 = [*step0, lookalike]
    assert decide(step1).band == "unverified"
    assert not is_decisive(step1, open_domains=["brand-careers.example"])

    step2 = [*step1, sig("fee_contradicts_employer", signal_id="sig_3")]
    d = decide(step2)
    assert d.band == "high_risk"
    assert d.evidence_ids == ("sig_1", "sig_3")
    assert d.summary == (
        "2 strong red signals, 1 of them from a search result: "
        "fee asked, employer says it charges no fee"
    )
    # Decisive even before confirm_sender_domain runs, so the other 4 searches are saved.
    assert is_decisive(step2, open_domains=["brand-careers.example"])


@pytest.mark.parametrize(
    "remaining",
    [[], ["no_listing_match"], ["pay_outlier"], ["listing_match", "impersonation_reports"]],
)
def test_sample_c_is_high_risk_after_two_searches_whatever_the_remaining_checks_add(remaining):
    text = [text_signal(h, f"sig_{i}") for i, h in enumerate(text_rules(sample("c.txt")), 1)]
    assert [s["rule"] for s in text] == ["sensitive_docs_early", "chat_only_interview"]

    step1 = [*text, sig("no_web_footprint")]
    assert decide(step1).band == "unverified"
    assert not is_decisive(step1)

    step2 = [*step1, sig("office_not_found")]
    d = decide(step2)
    assert d.band == "high_risk"
    assert d.summary == (
        "3 red signals of moderate or strong severity, 2 of them from search results: "
        "documents asked for before any interview, no web footprint, office not on Maps"
    )
    assert is_decisive(step2)

    assert decide([*step2, *sigs(*remaining)]).band == "high_risk"


def test_sample_b_is_consistent_with_genuine_after_four_searches():
    assert text_rules(sample("b.txt")) == []
    # Step 1: the recruiter email and the listing link are both on the official domain.
    step1 = [
        sig("sender_official", domain="contoso.example"),
        sig("sender_official", domain="contoso.example"),
    ]
    assert decide(step1).band == "unverified"

    step2 = [*step1, sig("listing_match")]
    assert decide(step2).band == "consistent_with_genuine"
    step3 = [*step2, sig("office_found")]
    assert decide(step3).band == "consistent_with_genuine"
    step4 = [*step3, sig("impersonation_reports")]
    d = decide(step4)
    assert d.band == "consistent_with_genuine"
    assert d.summary == (
        "3 green signals from search results and no strong or moderate red signal: "
        "sender on the official domain, listing applies on the official domain, "
        "office found on Maps; weak red signals noted: news of fake offers in the company's name"
    )
    assert not is_decisive(step4)


# ---- over every small combination -------------------------------------------------------

_COMBOS = [c for k in range(4) for c in itertools.combinations(RULES, k)]
_RANK = {"high_risk": 0, "unverified": 1, "consistent_with_genuine": 2}


@pytest.mark.parametrize("k", range(4))
def test_no_combination_says_genuine_or_safe(k):
    for combo in (c for c in _COMBOS if len(c) == k):
        d = decide(sigs(*combo))
        assert d.band in BANDS
        text = " ".join([d.summary, *d.reasons])
        assert not re.search(r"\b(?:genuine|safe)\b", text, re.IGNORECASE), combo


def test_adding_a_green_never_lowers_high_risk_and_a_red_never_makes_genuine():
    for combo in _COMBOS:
        before = band(*combo)
        for extra in RULES:
            if extra in combo:
                continue
            after = band(*combo, extra)
            if RULES[extra].direction == "green" and before == "high_risk":
                assert after == "high_risk", (combo, extra)
            if RULES[extra].direction == "red" and after == "consistent_with_genuine":
                assert before == "consistent_with_genuine", (combo, extra)


# ---- the employer's fraud notice ----------------------------------------------------------
# Which of the two fraud-notice rules fires is decided by the find_fraud_notice reader, the one
# place that reads a notice; these tests hold it to the decision table's fee phrases.

OFFICIAL = "brand.example"
NOTICE_TITLE = "Beware of recruitment fraud | Brand Careers"
FEE_SNIPPETS = {
    "never charge": "Brand will never charge candidates at any stage of hiring.",
    "no fee": "There is no fee to apply for any role at Brand.",
    "does not charge": "Brand does not charge any amount for recruitment.",
    "not ask for money": "Brand recruiters do not ask for money at any stage.",
    "not ask for payment": "Our recruiters will not ask for payment of any kind.",
    "deposit": "Anyone asking you for a security deposit is not from Brand.",
}


def result(link, title=NOTICE_TITLE, snippet=""):
    return {"title": title, "link": link, "snippet": snippet}


def fired(*results, fee_requested=True):
    """The fraud-notice rules fired on these ``site:`` results, each with the link it quotes."""
    reading = read_fraud_notice(
        {"organic_results": list(results)},
        params=find_fraud_notice_params(OFFICIAL, "Noida"),
        retrieved_at="2026-10-02T10:00:00+05:30",
        official_domain=OFFICIAL,
        fee_requested=fee_requested,
    )
    return [(s["rule"], s["evidence"]["link"]) for s in reading.signals]


def test_every_fee_phrase_is_covered():
    assert set(FEE_SNIPPETS) == set(FEE_PHRASES)


@pytest.mark.parametrize("phrase", FEE_PHRASES)
def test_a_notice_with_a_fee_phrase_contradicts_the_fee(phrase):
    notice = result("https://www.brand.example/fraud-alert", snippet=FEE_SNIPPETS[phrase])
    assert phrase in notice["snippet"]
    assert fired(notice) == [("fee_contradicts_employer", notice["link"])]


@pytest.mark.parametrize(
    "snippet",
    [
        "Brand never charges a fee to candidates.",
        "Brand is never charging candidates for interviews.",
        "Brand doesn't charge candidates for interviews.",
        "Brand does not ask candidates for any money.",
        "Our recruiters never ask for payments.",
        "We charge no registration fees.",
        "Brand never asks candidates for any fees.",
        "We do not seek any payment from candidates.",
        "Scam alert: never pay a security deposit for a job offer.",
    ],
)
def test_fee_phrases_read_through_inflections_and_contractions(snippet):
    notice = result("https://careers.brand.example/beware", snippet=snippet)
    assert fired(notice) == [("fee_contradicts_employer", notice["link"])]


def test_a_fee_phrase_in_the_title_counts():
    notice = result(
        "https://brand.example/notice",
        title="Fraud alert: Brand never charges candidates",
        snippet="Read our advisory.",
    )
    assert fired(notice) == [("fee_contradicts_employer", notice["link"])]


@pytest.mark.parametrize(
    "snippet",
    [
        "Fake job offers are circulating; share your feedback with our team.",
        "Beware of fraudsters who charge a registration fee in our name.",
        "Fake job offers are circulating. Verify on our careers site.",
    ],
)
def test_a_notice_without_a_fee_phrase_is_only_a_notice(snippet):
    notice = result("https://brand.example/fraud", snippet=snippet)
    assert fired(notice) == [("employer_fraud_notice_exists", notice["link"])]


def test_a_fee_phrase_notice_wins_over_an_earlier_plain_one():
    plain = result("https://brand.example/fraud", snippet="Beware of fake offer letters.")
    fees = result("https://brand.example/faq", snippet=FEE_SNIPPETS["no fee"])
    assert fired(plain, fees) == [("fee_contradicts_employer", fees["link"])]


def test_no_fee_asked_means_neither_fraud_notice_rule():
    notice = result("https://brand.example/fraud", snippet=FEE_SNIPPETS["never charge"])
    assert fired(notice, fee_requested=False) == []


def test_a_third_party_page_is_never_the_employers_notice():
    elsewhere = result("https://news.example/brand-scam", snippet=FEE_SNIPPETS["never charge"])
    lookalike = result("https://brand-careers.example/fraud", snippet=FEE_SNIPPETS["no fee"])
    assert fired(elsewhere, lookalike) == []


def test_a_page_without_a_fraud_term_is_not_a_notice():
    careers = result(
        "https://brand.example/careers", title="Careers at Brand", snippet="No fee to apply."
    )
    assert fired(careers) == []
    assert fired() == []
