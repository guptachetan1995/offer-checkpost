import re
from pathlib import Path

import pytest

from offer_checkpost.extract import TEXT_RULES, extract_claims, text_rules

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "offers"
NO_AMOUNT = "fee named, no amount"

# Sample A's employer "Brand" and Sample B's "Contoso" are placeholders, and so is Sample C's
# city: a one-off live SerpApi check picks the real ones, and these rows change with the text.
SAMPLES = {
    "a.txt": dict(
        company="Brand",
        role="Data Entry Executive (WFH)",
        city="Noida",
        pay=38000,
        fee=2499,
        contacts=["hr.onboarding@brand-careers.example"],
        links=[],
        rules=["fee_requested"],
    ),
    "b.txt": dict(
        company="Contoso",
        role="Graduate Engineer Trainee",
        city="Pune",
        pay=35000,
        fee=None,
        contacts=["campus.hiring@contoso.example"],
        links=["careers.contoso.example"],
        rules=[],
    ),
    "c.txt": dict(
        company="Zorvanta Support Services Pvt. Ltd",
        role="Customer Support Executive",
        city="Indore",
        pay=42000,
        fee=None,
        contacts=["+91 9XXXX XXXXX", "joinus@zorvanta-support.example"],
        links=[],
        rules=["sensitive_docs_early", "chat_only_interview"],
    ),
    "a-forwarded.txt": dict(
        company="Brand",
        role="Data Entry Executive (WFH)",
        city="Noida",
        pay=38000,
        fee=2499,
        contacts=["hr.onboarding@brand-careers.example"],
        links=[],
        rules=["fee_requested"],
    ),
    "hinglish-registration-fee.txt": dict(
        company="Quillnest Retail Services",
        role="Back Office Assistant",
        city="Lucknow",
        pay=18000,
        fee=1999,
        contacts=["+91 7XXXX XXXXX"],
        links=[],
        rules=["fee_requested"],
    ),
    "hinglish-security-deposit.txt": dict(
        company="Tarvolin Infotech",
        role="Chat Support Executive (Work From Home)",
        city=None,
        pay=25000,
        fee=3500,
        contacts=["+91 8XXXX XXXXX"],
        links=[],
        rules=["fee_requested", "sensitive_docs_early", "chat_only_interview"],
    ),
    "hinglish-joining-fees-rupees.txt": dict(
        company="Morvesta Logistics",
        role="Warehouse Picker",
        city="Faridabad",
        pay=15000,
        fee=2500,
        contacts=["+91 9XXXX XXXXX"],
        links=[],
        rules=["fee_requested"],
    ),
    "hinglish-telegram-processing.txt": dict(
        company="Vornaxa Telecom Services",
        role="Telecaller (Hindi)",
        city="Patna",
        pay=12000,
        fee=799,
        contacts=["+91 7XXXX XXXXX"],
        links=[],
        rules=["fee_requested", "chat_only_interview"],
    ),
    "task-per-like.txt": dict(
        company=None,
        role=None,
        city=None,
        pay=60000,
        fee=None,
        contacts=[],
        links=["likepay-tasks.example"],
        rules=["task_scam_pattern"],
    ),
    "task-prepaid-deposit.txt": dict(
        company=None,
        role=None,
        city=None,
        pay=240000,
        fee=1000,
        contacts=[],
        links=[],
        rules=["fee_requested", "task_scam_pattern"],
    ),
    "task-hinglish-reviews.txt": dict(
        company=None,
        role=None,
        city=None,
        pay=90000,
        fee=None,
        contacts=[],
        links=[],
        rules=["task_scam_pattern"],
    ),
    "task-daily-earning.txt": dict(
        company=None,
        role=None,
        city=None,
        pay=120000,
        fee=None,
        contacts=[],
        links=["quick-earn.example"],
        rules=["task_scam_pattern"],
    ),
    "task-hinglish-no-claims.txt": dict(
        company=None,
        role=None,
        city=None,
        pay=45000,
        fee=None,
        contacts=[],
        links=[],
        rules=["task_scam_pattern"],
    ),
    "no-company-training-fee.txt": dict(
        company=None,
        role="Data Entry Operators",
        city=None,
        pay=22000,
        fee=1500,
        contacts=["+91 8XXXX XXXXX"],
        links=[],
        rules=["fee_requested", "chat_only_interview"],
    ),
    "fee-without-amount.txt": dict(
        company="Plindora Analytics",
        role="Junior Data Analyst",
        city="Hyderabad",
        pay=29167,
        fee=NO_AMOUNT,
        contacts=["careers@plindora-analytics.example"],
        links=[],
        rules=[],
    ),
    "lookalike-kit-charges.txt": dict(
        company="Brand",
        role="Operations Associate",
        city="Gurugram",
        pay=45000,
        fee=4500,
        contacts=["careers@brnad.example"],
        links=[],
        rules=["fee_requested"],
    ),
    "otp-request.txt": dict(
        company="Kestrova Marketing Pvt Ltd",
        role="Field Sales Executive",
        city="Jaipur",
        pay=28500,
        fee=None,
        contacts=["+91 6XXXX XXXXX"],
        links=[],
        rules=["sensitive_docs_early"],
    ),
    "walk-in-genuine-shape.txt": dict(
        company="Hexavara Foods",
        role="Store Supervisor",
        city="Chennai",
        pay=18000,
        fee=None,
        contacts=[],
        links=[],
        rules=[],
    ),
    "training-fee-bangalore.txt": dict(
        company="Zentrivo Softlabs",
        role="Junior Software Developer",
        city="Bengaluru",
        pay=37500,
        fee=5500,
        contacts=["onboarding@zentrivo-softlabs.example"],
        links=["zentrivo-softlabs.example"],
        rules=["fee_requested"],
    ),
    "bank-details-email.txt": dict(
        company="Dravelin Consultants",
        role="HR Recruiter",
        city="Kolkata",
        pay=32000,
        fee=None,
        contacts=["+91 9XXXX XXXXX", "offers@dravelin-consultants.example"],
        links=[],
        rules=["sensitive_docs_early"],
    ),
    "internship-stipend.txt": dict(
        company="Brivona Media",
        role="Social Media Intern",
        city=None,
        pay=12000,
        fee=999,
        contacts=[],
        links=["www.brivona-media.example"],
        rules=["fee_requested"],
    ),
}


def sample(name: str) -> str:
    return (SAMPLES_DIR / name).read_text(encoding="utf-8")


def dialable(masked: str) -> str:
    # Built at runtime so the repository never holds a number anyone could dial.
    return masked.replace("X", "0")


def deliverable(local: str, label: str) -> str:
    # Built at runtime so the repository never holds an address outside a reserved domain.
    return local + "@" + label + ".in"


# ---- the samples --------------------------------------------------------------------------


def test_every_sample_file_has_an_expectation_and_there_are_at_least_15_variants():
    files = {p.name for p in SAMPLES_DIR.glob("*.txt")}
    assert files == set(SAMPLES)
    assert {"a.txt", "b.txt", "c.txt"} <= files
    assert len(files - {"a.txt", "b.txt", "c.txt"}) >= 15


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_sample_claims_and_text_rules(name):
    text, want = sample(name), SAMPLES[name]
    claims = extract_claims(text)

    def value(key):
        return claims[key] and claims[key]["value"]

    assert value("company") == want["company"]
    assert value("role") == want["role"]
    assert value("city") == want["city"]
    assert (claims["pay"] and claims["pay"]["monthlyInr"]) == want["pay"]
    if want["fee"] == NO_AMOUNT:
        assert claims["fee"]["amountInr"] is None
    else:
        assert (claims["fee"] and claims["fee"]["amountInr"]) == want["fee"]
    assert [c["value"] for c in claims["contacts"]] == want["contacts"]
    assert [link["host"] for link in claims["links"]] == want["links"]
    assert [hit["rule"] for hit in text_rules(text)] == want["rules"]


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_sample_spans_are_exact(name):
    text = sample(name)
    claims = extract_claims(text)

    def sliced(claim):
        start, end = claim["span"]
        return text[start:end]

    for key in ("company", "role"):
        if claims[key]:
            assert sliced(claims[key]) == claims[key]["value"]
    for key in ("pay", "fee"):
        if claims[key]:
            assert sliced(claims[key]) == claims[key]["raw"]
    if claims["city"]:
        assert extract_claims(sliced(claims["city"]))["city"]["value"] == claims["city"]["value"]
    for contact in claims["contacts"]:
        assert extract_claims(sliced(contact))["contacts"][0]["value"] == contact["value"]
    for link in claims["links"]:
        assert sliced(link) == link["value"]
    for hit in text_rules(text):
        assert text[hit["span"][0] : hit["span"][1]] == hit["quote"]


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_sample_contacts_and_links_are_all_synthetic(name):
    text = sample(name)
    claims = extract_claims(text)
    assert all(c["synthetic"] for c in claims["contacts"])
    assert all(link["synthetic"] for link in claims["links"])
    for domain in re.findall(r"@((?:[\w-]+\.)+[a-z]+)", text):
        assert domain.endswith(".example")
    assert not re.search(r"\d(?:[ .-]?\d){9}", text), "an unmasked 10-digit number"


def test_sample_a_matches_the_worked_example():
    claims = extract_claims(sample("a.txt"))
    assert claims["pay"]["raw"] == "₹38,000/month"
    assert claims["fee"]["raw"] == "refundable registration fee of ₹2,499"
    assert claims["contacts"] == [
        {
            "kind": "email",
            "value": "hr.onboarding@brand-careers.example",
            "span": claims["contacts"][0]["span"],
            "synthetic": True,
        }
    ]


def test_claims_start_unconfirmed():
    claims = extract_claims(sample("a.txt"))
    for key in ("company", "role", "city", "pay", "fee"):
        assert claims[key]["confirmed"] is False


def test_a_message_with_nothing_in_it_has_no_claims_and_no_rules():
    assert extract_claims("hello") == {
        "company": None,
        "role": None,
        "city": None,
        "pay": None,
        "fee": None,
        "contacts": [],
        "links": [],
    }
    assert text_rules("hello") == []


# ---- pay ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "raw", "monthly"),
    [
        ("Salary: ₹38,000/month", "₹38,000/month", 38000),
        ("Salary: Rs. 38000/-", "Rs. 38000/-", 38000),
        ("Salary 38k pm", "38k pm", 38000),
        ("CTC: 4.2 LPA", "4.2 LPA", 35000),
        ("Package: 3.5 lakh per annum", "3.5 lakh per annum", 29167),
        ("Salary ₹1,20,000 per annum", "₹1,20,000 per annum", 10000),
        ("Salary: INR 40,000", "INR 40,000", 40000),
        ("CTC Rs 3,60,000", "Rs 3,60,000", 30000),
        ("Salary ₹25,000 - 35,000 per month", "₹25,000 - 35,000 per month", 25000),
        ("Salary 25-30k per month", "25-30k per month", 25000),
        ("Salary 15,000 se 20,000 tak har mahine", "15,000 se 20,000 tak har mahine", 15000),
        ("Salary ₹25,000 mahina", "₹25,000 mahina", 25000),
        ("Earn ₹2,000 daily", "₹2,000 daily", 60000),
        ("Roz ₹3000 tak kamao", "Roz ₹3000", 90000),
        ("Stipend ₹5,000 per week", "₹5,000 per week", 21667),
        ("Monthly salary: ₹22,000", "Monthly salary: ₹22,000", 22000),
        ("Package: ₹3.5L per annum", "₹3.5L per annum", 29167),
        ("Salary ₹8L p.a.", "₹8L p.a.", 66667),
        ("Salary ₹4.2L", "₹4.2L", 35000),
        ("Salary ₹2.4L - ₹3L per annum", "₹2.4L - ₹3L per annum", 20000),
        ("Salary 8L per annum", "8L per annum", 66667),
        ("Salary: 1.2 crore per annum", "1.2 crore per annum", 1000000),
        ("Salary ₹20 hazar per month", "₹20 hazar per month", 20000),
        ("salary 20 hazar mahina", "20 hazar mahina", 20000),
        ("Salary: ₹25 thousand per month", "₹25 thousand per month", 25000),
        ("Salary Rs25,000 per month", "Rs25,000 per month", 25000),
        ("Salary INR25000 per month", "INR25000 per month", 25000),
        ("salary 25000 per month", "25000 per month", 25000),
        ("Hiring Shift In-charge: ₹22,000 in hand.", "₹22,000", 22000),
        ("Hiring Store In-charge at our Chennai outlet, salary ₹18,000.", "₹18,000", 18000),
        ("Training is provided free of charge, and you get a stipend of ₹8,000.", "₹8,000", 8000),
    ],
)
def test_pay_formats_are_normalised_to_monthly_inr(text, raw, monthly):
    pay = extract_claims(text)["pay"]
    assert (pay["raw"], pay["monthlyInr"]) == (raw, monthly)
    assert text[pay["span"][0] : pay["span"][1]] == raw


@pytest.mark.parametrize(
    "text",
    [
        "Like videos and get ₹50 per like.",
        "Har review pe ₹150 milenge.",
        "Deposit ₹1,000 and get ₹1,300 back.",
        "Joining fee ₹999 only.",
        "Walk-in 10 am to 1 pm, 12 October 2026, 2nd floor.",
        "Salary: competitive",
        "Salary ₹20",
        "Pay ₹2,499 to confirm your slot.",
        "Earn up to ₹10,000 incentive.",
        "Achieve a target of 1500 per month.",
        "Earn ₹100 for each 5-star rating you give.",
        "Like YouTube videos and get ₹50 each.",
        "Call " + dialable("9XXXXXXXXX") + " daily",
    ],
)
def test_amounts_that_are_not_pay(text):
    assert extract_claims(text)["pay"] is None


@pytest.mark.parametrize(
    "text",
    [
        "Pay ₹999 to confirm your seat. Salary ₹25,000 per month.",
        "You need to pay ₹1,500 for the ID card. Salary ₹25,000 per month.",
        "Earn up to ₹10,000 incentive. Salary ₹25,000 per month.",
        "Openings ₹12,000 - ₹14,000 elsewhere. Salary ₹25,000 per month.",
    ],
)
def test_a_figure_with_a_stated_period_wins_over_an_earlier_one(text):
    pay = extract_claims(text)["pay"]
    assert (pay["raw"], pay["monthlyInr"]) == ("₹25,000 per month", 25000)


def test_pay_is_never_read_across_a_line_break():
    assert extract_claims("Salary ₹20,000\nDaily 4 hours")["pay"]["monthlyInr"] == 20000


# ---- fee ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "raw", "amount"),
    [
        (
            "Pay a refundable registration fee of ₹2,499 today.",
            "refundable registration fee of ₹2,499",
            2499,
        ),
        ("registration ke liye ₹1,999 bhejo", "registration ke liye ₹1,999", 1999),
        ("Joining fees 2500 rupees, joining ke din.", "Joining fees 2500 rupees", 2500),
        ("Pay ₹999 for registration to confirm.", "₹999 for registration", 999),
        ("laptop security deposit Rs. 3,500 jama karo", "security deposit Rs. 3,500", 3500),
        ("Processing charge ₹799 lagega.", "Processing charge ₹799", 799),
        ("Kit charges: INR 1,200", "Kit charges: INR 1,200", 1200),
        ("Training fee Rs 1500, refundable.", "Training fee Rs 1500", 1500),
        ("Deposit ₹1,000 before joining.", "Deposit ₹1,000", 1000),
        ("A one-time fee of ₹499 applies.", "fee of ₹499", 499),
        ("Training fee ₹1.5L", "Training fee ₹1.5L", 150000),
        ("Pay Rs999 for registration.", "Rs999 for registration", 999),
        ("Registration fee 2000 only.", "Registration fee 2000", 2000),
        ("Registration fee: 1999.", "Registration fee: 1999", 1999),
        ("Pay registration fee of 2500 to confirm your seat.", "registration fee of 2500", 2500),
        ("Pay ₹2,499 to confirm your slot.", "Pay ₹2,499", 2499),
        ("Kindly pay Rs 2,000 for your ID card and uniform.", "pay Rs 2,000", 2000),
        ("No interview direct joining registration fee ₹999 only", "registration fee ₹999", 999),
        ("Do not miss this chance registration fee ₹500 pay today", "registration fee ₹500", 500),
        (
            "Registration fee ₹500 will be reimbursed after joining.",
            "Registration fee ₹500",
            500,
        ),
    ],
)
def test_fee_with_an_amount_fires_fee_requested(text, raw, amount):
    fee = extract_claims(text)["fee"]
    assert (fee["raw"], fee["amountInr"]) == (raw, amount)
    assert text[fee["span"][0] : fee["span"][1]] == raw
    assert [hit["rule"] for hit in text_rules(text)] == ["fee_requested"]


@pytest.mark.parametrize(
    ("text", "raw"),
    [
        ("A nominal registration fee applies; details on the call.", "registration fee"),
        ("Security deposit lagega, HR batayegi.", "Security deposit"),
    ],
)
def test_fee_without_an_amount_is_a_claim_but_does_not_fire(text, raw):
    fee = extract_claims(text)["fee"]
    assert (fee["raw"], fee["amountInr"]) == (raw, None)
    assert text_rules(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "There is no registration fee.",
        "Koi registration fee nahi hai.",
        "We never charge a joining fee.",
        "No fee.",
        "Fee details will follow.",
        "Please carry your documents for verification at the venue.",
        "We do not charge any registration fee.",
        "Registration fee nahi lagegi.",
        "Registration fee is not required.",
        "Do not pay ₹500 to anyone for this job.",
        "Training is provided free of charge, and you get a stipend of ₹8,000.",
        "Hiring Store In-charge at our Chennai outlet, salary ₹18,000.",
        "Hiring Shift In-charge: ₹22,000 in hand.",
        "Relocation charges of ₹10,000 will be paid by the company.",
        "Salary ₹25,000 per month; we reimburse exam fees up to ₹5,000.",
    ],
)
def test_no_fee_is_read_from_a_denial_or_a_bare_mention(text):
    assert extract_claims(text)["fee"] is None
    assert text_rules(text) == []


def test_a_plain_number_is_a_fee_only_right_after_a_named_fee():
    assert extract_claims("Processing charge 2 days after joining.")["fee"]["amountInr"] is None
    assert extract_claims("Training fee 50% of first salary")["fee"]["amountInr"] is None
    assert extract_claims("A fee 2000 applies.")["fee"] is None


def test_a_fee_never_takes_the_salary_as_its_amount():
    claims = extract_claims("Registration fee applies, salary ₹20,000.")
    assert (claims["fee"]["raw"], claims["fee"]["amountInr"]) == ("Registration fee", None)
    assert claims["pay"]["monthlyInr"] == 20000


def test_a_fee_does_not_take_an_amount_from_another_sentence():
    claims = extract_claims("Registration fee applies. Salary ₹20,000 per month.")
    assert (claims["fee"]["raw"], claims["fee"]["amountInr"]) == ("Registration fee", None)
    assert claims["pay"]["monthlyInr"] == 20000


# ---- phones -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [
        "+91 9XXXX XXXXX",
        "+91-9XXXX-XXXXX",
        "+919XXXXXXXXX",
        "+91 9XXXXXXXXX",
        "(+91) 9XXXX XXXXX",
        "91 9XXXX XXXXX",
        "09XXXXXXXXX",
        "9XXXX XXXXX",
        "9XXXXXXXXX",
        "+91 9xxxx xxxxx",
        "+91 9**** *****",
    ],
)
def test_plus_91_phone_formats_and_masks(written):
    text = f"Call HR on {written} today."
    (phone,) = extract_claims(text)["contacts"]
    assert phone["kind"] == "phone"
    assert phone["value"] == "+91 9XXXX XXXXX"
    assert phone["synthetic"] is True
    assert text[phone["span"][0] : phone["span"][1]] == written


def test_a_partly_masked_phone_keeps_its_digits_and_stays_synthetic():
    (phone,) = extract_claims("WhatsApp +91 98XXX XXXXX")["contacts"]
    assert (phone["value"], phone["synthetic"]) == ("+91 98XXX XXXXX", True)


def test_an_unmasked_phone_is_not_synthetic():
    written = dialable("+91-9XXXX-XXXXX")
    (phone,) = extract_claims(f"Call {written}")["contacts"]
    assert phone["value"] == dialable("+91 9XXXX XXXXX")
    assert phone["synthetic"] is False


@pytest.mark.parametrize(
    "text",
    ["Ref 5XXXX XXXXX", "A/c 9XXXXXXXXXXX", "Batch 2026-10-09", "Salary ₹38,000 per month"],
)
def test_not_a_phone(text):
    assert extract_claims(text)["contacts"] == []


def test_the_same_phone_twice_is_one_contact():
    claims = extract_claims("Call +91 9XXXX XXXXX or WhatsApp 09XXXXXXXXX")
    assert [c["value"] for c in claims["contacts"]] == ["+91 9XXXX XXXXX"]


# ---- emails and links ---------------------------------------------------------------------


def test_emails_on_a_reserved_domain_or_masked_are_synthetic():
    masked = deliverable("ta****", "mailbox")
    live = deliverable("hr.onboarding", "brand-careers")
    text = f"Write to HR.Onboarding@Brand-Careers.example, {masked} or {live}."
    contacts = extract_claims(text)["contacts"]
    assert [(c["value"], c["synthetic"]) for c in contacts] == [
        ("hr.onboarding@brand-careers.example", True),
        (masked, True),
        (live, False),
    ]
    assert text[contacts[2]["span"][0] : contacts[2]["span"][1]] == live


@pytest.mark.parametrize(
    ("text", "value", "host", "synthetic"),
    [
        (
            "Apply at https://careers.contoso.example/jobs/42.",
            "https://careers.contoso.example/jobs/42",
            "careers.contoso.example",
            True,
        ),
        (
            "Form: www.brand-careers.example/apply!",
            "www.brand-careers.example/apply",
            "www.brand-careers.example",
            True,
        ),
        (
            "Fill brand-careers.example/form now",
            "brand-careers.example/form",
            "brand-careers.example",
            True,
        ),
        ("Openings on amazon.jobs", "amazon.jobs", "amazon.jobs", False),
    ],
)
def test_links(text, value, host, synthetic):
    (link,) = extract_claims(text)["links"]
    assert (link["value"], link["host"], link["synthetic"]) == (value, host, synthetic)
    assert text[link["span"][0] : link["span"][1]] == value


@pytest.mark.parametrize(
    ("written", "value", "synthetic"),
    [
        ("email: hr@brand-careers.example.Thanks", "hr@brand-careers.example", True),
        (f"mail {deliverable('hr', 'brnad')}.Thanks", deliverable("hr", "brnad"), False),
        ("mail hr@brand-careers.example.Regards, HR", "hr@brand-careers.example", True),
        ("mail hr@br\u0430nd.example", "hr@br\u0430nd.example", True),
    ],
)
def test_an_email_stops_at_its_domain(written, value, synthetic):
    (email,) = extract_claims(written)["contacts"]
    assert (email["value"], email["synthetic"]) == (value, synthetic)
    assert written[email["span"][0] : email["span"][1]].lower() == value


def test_links_on_unicode_and_capitalised_hosts():
    claims = extract_claims("Apply at https://br\u0430nd.example/apply or QUICKJOBS.EXAMPLE now")
    assert [(link["host"], link["synthetic"]) for link in claims["links"]] == [
        ("br\u0430nd.example", True),
        ("quickjobs.example", True),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Mail hr@brand-careers.example",
        "Zorvanta Pvt.Ltd, Rs.42,000",
        "Eligibility: B.Tech / B.Com graduates, 2026 pass-outs.",
        "Skills: ASP.NET, C#, SQL.",
        "Qualification: M.Tech or B.Sc, VB.NET experience",
        "Stack: asp.net and vb.net",
    ],
)
def test_not_a_link(text):
    assert extract_claims(text)["links"] == []


# ---- city ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("Office in Bangalore", "Bengaluru"),
        ("Office in Gurgaon", "Gurugram"),
        ("Office in new delhi", "Delhi"),
        ("Office in Navi Mumbai", "Navi Mumbai"),
        ("Walk-in at our Pune office\nLocation: Nagpur", "Nagpur"),
        ("Pan India hiring", None),
        ("Mail hr@pune-jobs.example", None),
        # Letters that case-fold to ASCII under Unicode rules are not the ASCII city names.
        ("Office in \u0130ndore", None),
        ("Office in \u017furat", None),
    ],
)
def test_city(text, city):
    assert (extract_claims(text)["city"] or {}).get("value") == city


# ---- company and role ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "company"),
    [
        ("*Company:* Quillnest Retail Services\nPost: Picker", "Quillnest Retail Services"),
        ("Greetings from Brand! You are shortlisted.", "Brand"),
        ("Greetings from Contoso. You are shortlisted.", "Contoso"),
        ("On behalf of Dravelin Consultants, we offer you", "Dravelin Consultants"),
        ("Plindora Analytics is hiring a Junior Data Analyst", "Plindora Analytics"),
        ("Zentrivo Softlabs has shortlisted you", "Zentrivo Softlabs"),
        ("Morvesta Logistics mein warehouse job", "Morvesta Logistics"),
        ("Thank you for applying to Contoso.", "Contoso"),
        ("Selected for the role of Operations Associate at Brand, Gurgaon", "Brand"),
        ("This is the desk at Kestrova Marketing Pvt Ltd.", "Kestrova Marketing Pvt Ltd"),
        ("Regards,\nBrand HR Onboarding Team", "Brand"),
        ("Greetings from HR Team!", None),
        ("Like YouTube videos and earn daily", None),
        ("Job opening in Pune", None),
    ],
)
def test_company(text, company):
    claim = extract_claims(text)["company"]
    assert (claim and claim["value"]) == company


@pytest.mark.parametrize(
    ("text", "role"),
    [
        ("Post: Back Office Assistant\nLocation: Lucknow", "Back Office Assistant"),
        ("for the post of Data Entry Executive (WFH) with us", "Data Entry Executive (WFH)"),
        ("for the Graduate Engineer Trainee role at our office", "Graduate Engineer Trainee"),
        (
            "for the Customer Support Executive (Night Shift) role",
            "Customer Support Executive (Night Shift)",
        ),
        ("Urgent hiring for Data Entry Operators.", "Data Entry Operators"),
        ("Tarvolin Infotech ko chahiye Chat Support Executive", "Chat Support Executive"),
        ("Work as a Social Media Intern from home", "Social Media Intern"),
        (
            "Brand is hiring for Work From Home Customer Service Associates. Location: Pune.",
            "Work From Home Customer Service Associates",
        ),
        (
            "We are hiring Work From Home Data Entry Operators.",
            "Work From Home Data Entry Operators",
        ),
        ("Hiring Store In-charge at our Chennai outlet", "Store In-charge"),
        ("We are hiring freshers!", None),
        ("Pay for your first task now", None),
        ("Internship for 3 months", None),
    ],
)
def test_role(text, role):
    claim = extract_claims(text)["role"]
    assert (claim and claim["value"]) == role


# ---- text rules ---------------------------------------------------------------------------


def test_text_rules_are_the_four_in_table_order():
    assert TEXT_RULES == (
        "fee_requested",
        "task_scam_pattern",
        "sensitive_docs_early",
        "chat_only_interview",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Earn ₹50 per like on YouTube videos.",
        "Get paid for each hotel review you post.",
        "Har review pe ₹150 milenge.",
        "Videos like karne ke ₹30 milenge.",
        "Complete prepaid tasks for commission.",
        "Deposit ₹500 to unlock merchant tasks.",
        "Earn ₹4,000 daily by completing simple online tasks.",
        "Roz ₹2000 kamao, ghar baithe online kaam.",
        "Earn money by liking YouTube videos, ₹50 per video.",
        "Earn ₹100 for each 5-star rating you give.",
        "Get ₹30 for each YouTube channel you subscribe to.",
        "Get ₹150 for every 5-star review on Google Maps.",
        "₹150 per 5-star review",
        "Earn ₹120 per 5-star rating",
        "Earn ₹100 per 5 star review",
        "₹40/like",
        "₹40 / review",
        "Like YouTube videos and get ₹50 each.",
        "Give 5 star reviews and earn ₹150 each",
        "Rate hotels and earn daily ₹3000.",
    ],
)
def test_task_scam_pattern(text):
    assert "task_scam_pattern" in [hit["rule"] for hit in text_rules(text)]


@pytest.mark.parametrize(
    "text",
    [
        "Working hours: 4 hours a day.",
        "Earn incentives of up to ₹5,000 per month on sales.",
        "Daily standups with the team, then customer calls.",
        "Review our job description before the interview.",
        "Salary revision after every performance review.",
        "Increment after every annual review.",
        "You will handle investment research tasks for our clients.",
        "Tasks: prepaid and postpaid connections, recharges and billing.",
        "Earn up to ₹1,000 a day as a delivery partner. Apply online.",
        "Video editor needed, ₹500 per video.",
    ],
)
def test_not_a_task_scam(text):
    assert "task_scam_pattern" not in [hit["rule"] for hit in text_rules(text)]


@pytest.mark.parametrize(
    "text",
    [
        "Send your Aadhaar and PAN photo now.",
        "Share the OTP you receive with us.",
        "Please share your bank account number and IFSC for salary setup.",
        "Aadhaar card ki photo bhejo.",
        "Upload a cancelled cheque before the interview.",
        "Before the interview, share your bank details; bring the originals on the first day.",
        "Send your Aadhaar and PAN photo on WhatsApp now, and carry the originals on joining.",
        "Documents required: Aadhaar card, PAN card, resume.",
    ],
)
def test_sensitive_docs_early(text):
    assert [hit["rule"] for hit in text_rules(text)] == ["sensitive_docs_early"]


@pytest.mark.parametrize(
    "text",
    [
        "Please carry your Aadhaar card to the interview.",
        "Bring original PAN and Aadhaar on the day.",
        "Submit your PAN card copy on the joining day.",
        "We will collect bank details after selection.",
        "Pan India hiring, send your resume.",
        "Walk-in interview at our office.\nDocuments required: Aadhaar card, PAN card, resume.",
    ],
)
def test_documents_asked_for_in_person_or_after_selection_are_not_early(text):
    assert text_rules(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Interview on WhatsApp chat only.",
        "Interview Telegram pe hoga.",
        "The first round is a chat interview.",
    ],
)
def test_chat_only_interview(text):
    assert [hit["rule"] for hit in text_rules(text)] == ["chat_only_interview"]


@pytest.mark.parametrize(
    "text",
    [
        "Interview on a WhatsApp video call.",
        "Interview at our office; we confirm the slot on WhatsApp.",
        "Join our Telegram group for updates.",
        "We will confirm your interview slot on WhatsApp. The interview is at our Pune office.",
        "Interview schedule will be shared on WhatsApp. Venue: Pune office.",
        "You will receive the interview details on WhatsApp.",
    ],
)
def test_not_a_chat_only_interview(text):
    assert text_rules(text) == []


def test_each_rule_quotes_the_sentence_it_fired_on():
    hits = text_rules(sample("c.txt"))
    assert [(hit["rule"], hit["quote"]) for hit in hits] == [
        (
            "sensitive_docs_early",
            "Before the interview, send a clear photo of your Aadhaar card (front and back) and"
            " your bank passbook or a cancelled cheque, so we can set up your salary account.",
        ),
        ("chat_only_interview", "The interview will be on Telegram chat only."),
    ]
    hits = text_rules(sample("a.txt"))
    assert hits[0]["quote"] == (
        "To confirm your slot, pay a refundable registration fee of ₹2,499 within 2 hours."
    )
