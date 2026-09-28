import pytest

from offer_checkpost.salary import (
    ListingPay,
    Pay,
    PayBenchmark,
    is_pay_outlier,
    listing_pay,
    parse_pay,
    pay_benchmark,
)


def listing(salary=None, extensions=(), description=None, **detected):
    job = {"title": "Customer Support Executive", "company_name": "Fictional Listings Co"}
    if salary is not None:
        detected["salary"] = salary
    if detected:
        job["detected_extensions"] = detected
    if extensions:
        job["extensions"] = list(extensions)
    if description is not None:
        job["description"] = description
    return job


def paid(*monthly):
    return [listing(salary=f"₹{m:,} a month") for m in monthly]


@pytest.mark.parametrize(
    ("text", "monthly"),
    [
        ("₹15K–₹20K a month", 17_500),
        ("₹25,000 a month", 25_000),
        ("₹3L–₹4.5L a year", 31_250),
        ("₹2,50,000–₹3,50,000 a year", 25_000),
        ("₹1.2 Cr a year", 1_000_000),
        ("15K–20K a month", 17_500),
        ("₹15K–₹20K a month", 17_500),
        ("Rs. 18,000 per month", 18_000),
        ("INR 20,000 - 25,000 per month", 22_500),
        ("₹20,000 to ₹30,000 per month", 25_000),
        ("₹38,000/month", 38_000),
        ("38k pm", 38_000),
        ("25000 rupees per month", 25_000),
        ("₹20,000 in-hand per month", 20_000),
        ("4.2 LPA", 35_000),
        ("3-5 LPA", 33_333),
        ("3.5 lakh per annum", 29_167),
        ("₹4 lakhs p.a.", 33_333),
        ("Monthly salary: ₹18,000", 18_000),
        ("CTC: ₹3.6 lakh", 30_000),
        ("CTC ₹25,000 per month", 25_000),
        ("₹15,000 - ₹18,000", 16_500),
        ("₹2,40,000 - ₹3,00,000", 22_500),
    ],
)
def test_listing_pay_formats_normalise_to_monthly_inr(text, monthly):
    assert parse_pay(text).monthly_inr == monthly


@pytest.mark.parametrize(
    ("text", "monthly"),
    [
        ("20 se 25 hazar mahina", 22_500),
        ("mahine ke 18 hazaar", 18_000),
        ("₹22,000 har mahine", 22_000),
        ("₹3 lakh saal ka", 25_000),
        ("salana 4.8 lakh", 40_000),
    ],
)
def test_hinglish_pay_forms(text, monthly):
    assert parse_pay(text).monthly_inr == monthly


@pytest.mark.parametrize(
    "text",
    [
        "₹600–₹700 a day",
        "₹500 an hour",
        "₹5K a week",
        "3 days ago",
        "Full-time",
        "2-4 years experience",
        "Joining bonus ₹5,000",
        "Health insurance cover of ₹2 lakh",
        "Shift 2 PM - 11 PM",
        "₹20,000 - 3 openings",
        "",
    ],
)
def test_text_without_a_monthly_or_yearly_figure_is_not_pay(text):
    assert parse_pay(text) is None


def test_raw_is_the_exact_pay_phrase_and_a_lone_bonus_before_it_is_skipped():
    pay = parse_pay("Joining bonus ₹2,000. Salary ₹15,000 - ₹18,000 per month. Apply now.")
    assert pay.raw == "₹15,000 - ₹18,000 per month"
    assert pay.monthly_inr == 16_500


def test_detected_extensions_salary_comes_first():
    job = listing(
        salary="₹20K–₹30K a month",
        extensions=["Full-time", "₹40K a month"],
        description="Salary ₹50,000 per month",
    )
    expected = ListingPay("detected_extensions.salary", "₹20K–₹30K a month", 25_000)
    assert listing_pay(job) == expected


def test_missing_salary_falls_back_to_the_first_extension_with_pay():
    job = listing(
        extensions=["3 days ago", "Full-time", "₹18K–₹22K a month", "Health insurance"],
        description="Salary ₹50,000 per month",
        posted_at="3 days ago",
        schedule_type="Full-time",
    )
    assert listing_pay(job) == ListingPay("extensions", "₹18K–₹22K a month", 20_000)


def test_no_salary_and_no_pay_in_extensions_falls_back_to_the_description():
    job = listing(
        extensions=["3 days ago", "Full-time"],
        description="Handle customer calls. Salary: ₹15,000 - ₹18,000 per month. Walk-in welcome.",
        schedule_type="Full-time",
    )
    assert listing_pay(job) == ListingPay("description", "₹15,000 - ₹18,000 per month", 16_500)


def test_a_daily_salary_falls_through_to_the_next_source():
    job = listing(
        salary="₹600–₹700 a day",
        extensions=["₹600–₹700 a day"],
        description="Pay ₹15,000 - ₹17,000 per month after training.",
    )
    assert listing_pay(job) == ListingPay("description", "₹15,000 - ₹17,000 per month", 16_000)


@pytest.mark.parametrize(
    "description",
    [
        "Salary as per industry standards. Attractive incentives up to ₹5,000 per month.",
        "Night shift allowance of ₹2,000 per month. Cab facility.",
        "Achieve a target of 1500 per month.",
        "Salary ₹24,000 per month",
        "Age 18-35. Shift 9-6. Incentives ₹2,000-₹5,000. Salary 15000 per month.",
        "Working hours 9-6, 20-25 per month",
    ],
)
def test_a_description_gives_pay_only_as_a_marked_range(description):
    assert listing_pay(listing(description=description)) is None


def test_a_description_range_after_an_incentive_range_is_the_pay():
    job = listing(description="Age 18-35. Incentives ₹2,000-₹5,000. Salary ₹15K-₹18K a month.")
    assert listing_pay(job) == ListingPay("description", "₹15K-₹18K a month", 16_500)


def test_incentives_and_allowances_never_pull_the_median_down():
    listings = [
        listing(salary="₹20K a month"),
        listing(
            description="Salary as per industry standards. Attractive incentives up to "
            "₹5,000 per month."
        ),
        listing(description="Night shift allowance of ₹2,000 per month. Cab facility."),
    ]
    benchmark = pay_benchmark(listings)
    assert benchmark.coverage == "pay found in 1 of 3 listings"
    assert not is_pay_outlier(12_000, benchmark)


@pytest.mark.parametrize(
    ("text", "raw", "monthly"),
    [
        ("Incentives ₹2,000–₹5,000. Salary ₹15,000 per month", "₹15,000 per month", 15_000),
        ("Referral reward ₹2,000–₹5,000. Salary ₹15,000 per month", "₹15,000 per month", 15_000),
        ("Performance bonus ₹10K a month. CTC 3.6 LPA", "CTC 3.6 LPA", 30_000),
    ],
)
def test_a_figure_labelled_as_something_else_is_skipped(text, raw, monthly):
    assert parse_pay(text) == Pay(raw, monthly)


def test_a_stated_period_wins_over_an_earlier_range_without_one():
    pay = parse_pay("Openings at ₹12,000–₹14,000. Offer: ₹16,000 per month")
    assert (pay.raw, pay.monthly_inr) == ("₹16,000 per month", 16_000)


def test_a_listing_with_no_pay_anywhere():
    assert listing_pay(listing(extensions=["Full-time"], description="Great team.")) is None
    assert listing_pay({"title": "Customer Support Executive"}) is None


def test_pay_found_in_n_of_m_listings():
    listings = [
        listing(salary="₹20K a month"),
        listing(extensions=["₹22K a month"]),
        listing(description="Salary ₹22,000 - ₹26,000 per month"),
        listing(extensions=["Full-time"]),
        listing(description="No pay mentioned."),
    ]
    benchmark = pay_benchmark(listings)
    assert (benchmark.found, benchmark.listings) == (3, 5)
    assert benchmark.coverage == "pay found in 3 of 5 listings"
    assert [p.source for p in benchmark.pays] == [
        "detected_extensions.salary",
        "extensions",
        "description",
    ]
    assert benchmark.median_monthly_inr == 22_000


def test_median_of_an_even_count_is_the_mean_of_the_middle_two():
    assert pay_benchmark(paid(10_000, 20_000, 30_000, 40_000)).median_monthly_inr == 25_000


def test_no_listings_means_no_median():
    benchmark = pay_benchmark([])
    assert benchmark == PayBenchmark(listings=0, pays=())
    assert benchmark.coverage == "pay found in 0 of 0 listings"
    assert benchmark.median_monthly_inr is None
    assert not benchmark.enough


def test_pay_outlier_at_exactly_2x_does_not_fire_and_at_2_01x_does():
    benchmark = pay_benchmark(paid(18_000, 20_000, 22_000))
    assert benchmark.median_monthly_inr == 20_000
    assert not is_pay_outlier(40_000, benchmark)
    assert is_pay_outlier(40_200, benchmark)


def test_pay_outlier_needs_three_data_points():
    two = pay_benchmark(paid(15_000, 15_000))
    three = pay_benchmark(paid(15_000, 15_000, 15_000))
    assert not two.enough and three.enough
    assert not is_pay_outlier(100_000, two)
    assert is_pay_outlier(100_000, three)


def test_data_points_count_listings_that_carry_pay_not_listings():
    benchmark = pay_benchmark([*paid(15_000, 15_000), listing(extensions=["Full-time"])])
    assert benchmark.coverage == "pay found in 2 of 3 listings"
    assert not is_pay_outlier(100_000, benchmark)


def test_pay_below_the_median_is_never_an_outlier():
    assert not is_pay_outlier(5_000, pay_benchmark(paid(15_000, 20_000, 25_000)))
