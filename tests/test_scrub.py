"""The recording scrubber. Every response here is synthetic and fictional: shaped like an
untrimmed SerpApi response (more fields than ``json_restrictor`` would return), so the
whitelist has something to remove. Email addresses are on ``.example``, and a phone number
to be masked is written masked and filled in at runtime (``unmasked``), so this file holds no
dialable number."""

import copy
import hashlib
import itertools
import re

import pytest

from offer_checkpost.salary import listing_pay
from offer_checkpost.scrub import (
    ACCOUNT_FIELDS,
    WHITELIST,
    clean_text,
    find_emails,
    find_phones,
    is_people_profile,
    mask_emails,
    mask_phones,
    pay_sentences,
    scrub,
    scrub_account,
)


def unmasked(masked: str) -> str:
    """A masked number with each X filled in by the next of 0, 1, 2, ... 9, 0, 1, ..."""
    digits = itertools.cycle("0123456789")
    return re.sub("X", lambda _: next(digits), masked)


RAW_GOOGLE = {
    "search_metadata": {"id": "synthetic", "status": "Success", "total_time_taken": 1.2},
    "search_parameters": {"engine": "google", "q": "Brand careers", "gl": "in"},
    "knowledge_graph": {
        "title": "Brand",
        "website": "https://www.brand.example/",
        "type": "Company",
        "description": "A fictional employer.",
        "profiles": [{"name": "LinkedIn", "link": "https://www.linkedin.com/company/brand"}],
        "customer_service": unmasked("18XX XXX XXXX"),
    },
    "organic_results": [
        {
            "position": 1,
            "title": "Careers at Brand",
            "link": "https://careers.brand.example/",
            "displayed_link": "careers.brand.example",
            "favicon": "https://serpapi.example/favicon.png",
            "snippet": "Explore open roles at Brand.",
            "sitelinks": {
                "inline": [{"title": "Jobs", "link": "https://careers.brand.example/j"}]
            },
        },
        {
            "position": 2,
            "title": "A Recruiter - Brand | LinkedIn",
            "link": "https://in.linkedin.com/in/a-recruiter-000",
            "snippet": "Talent acquisition at Brand.",
        },
    ],
    "related_questions": [{"question": "Is Brand hiring?"}],
    "serpapi_pagination": {"next": "https://serpapi.example/search?start=10"},
}

RAW_JOBS = {
    "search_metadata": {"id": "synthetic"},
    "jobs_results": [
        {
            "title": "Customer Support Executive",
            "company_name": "Velmora Services",
            "location": "Indore, Madhya Pradesh, India",
            "via": "Portal",
            "share_link": "https://www.google.example/share",
            "thumbnail": "https://img.example/logo.png",
            "extensions": ["2 days ago", "Full-time", "₹15K–₹18K a month"],
            "detected_extensions": {
                "posted_at": "2 days ago",
                "schedule_type": "Full-time",
                "salary": "₹15K–₹18K a month",
            },
            "description": (
                "Handle customer calls for an e-commerce client. Salary: Rs. 15,000 - 18,000 "
                f"per month. Walk in or call {unmasked('9XXXX XXXXX')} to apply.\n"
                "Graduates preferred."
            ),
            "job_highlights": [{"title": "Qualifications", "items": ["Graduate"]}],
            "apply_options": [
                {"title": "Portal", "link": "https://jobs.portal.example/velmora"},
            ],
            "job_id": "synthetic-job-id",
        },
        {
            "title": "Support Associate",
            "company_name": "Tarsik BPO",
            "location": "Indore, Madhya Pradesh, India",
            "description": "Voice process, rotational shifts.",
        },
    ],
}

RAW_MAPS_LOCAL = {
    "search_metadata": {"id": "synthetic"},
    "local_results": [
        {
            "position": 1,
            "title": "Contoso Pune Office",
            "place_id": "ChIJ-synthetic-place-0001",
            "data_id": "0x0:0x1",
            "gps_coordinates": {"latitude": 18.5, "longitude": 73.7},
            "rating": 4.2,
            "reviews": 118,
            "type": "Corporate office",
            "address": "Hinjewadi Phase 1, Pune, Maharashtra 411057",
            "phone": unmasked("+91 8XXXX XXXXX"),
            "website": "https://www.contoso.example/",
            "thumbnail": "https://img.example/office.png",
        }
    ],
}

RAW_MAPS_PLACE = {
    "place_results": {
        "title": "Contoso Kharadi",
        "place_id": "ChIJ-synthetic-place-0002",
        "type": "Corporate office",
        "address": "Kharadi, Pune, Maharashtra 411014",
        "rating": 4.0,
        "reviews": 64,
        "phone": unmasked("02X XXXX XXXX"),
        "user_reviews": {"most_relevant": [{"username": "A Person", "rating": 5}]},
    }
}

RAW_NEWS = {
    "news_results": [
        {
            "position": 1,
            "title": "Fake Brand job offers doing the rounds, police warn",
            "link": "https://news.example/fake-brand-offers",
            "source": {
                "name": "News Example",
                "icon": "https://news.example/i.png",
                "authors": ["A Writer"],
            },
            "date": "09/12/2026, 07:00 AM, +0000 UTC",
            "thumbnail": "https://news.example/t.png",
        },
        {
            "position": 2,
            "title": "Someone posted about Brand",
            "link": "https://twitter.com/someone/status/1",
            "source": {"name": "X"},
        },
    ]
}

RAW_REVIEWS = {
    "place_info": {"title": "Contoso Pune Office", "address": "Pune"},
    "reviews": [
        {
            "link": "https://www.google.example/maps/reviews/synthetic-1",
            "rating": 1,
            "date": "a month ago",
            "iso_date": "2026-08-14T09:30:00Z",
            "snippet": (
                "They asked me for a registration fee. "
                f"Call {unmasked('7XXXX XXXXX')} or mail a.b@mail.example"
            ),
            "user": {
                "name": "A Reviewer",
                "link": "https://www.google.example/maps/contrib/0",
                "contributor_id": "0",
                "thumbnail": "https://img.example/face.png",
                "local_guide": True,
                "reviews": 10,
            },
            "likes": 3,
            "images": ["https://img.example/r.png"],
            "response": {"date": "a month ago", "snippet": "Please contact HR."},
        }
    ],
}


def test_google_keeps_only_the_knowledge_graph_title_and_website_and_organic_text():
    assert scrub("google", RAW_GOOGLE) == {
        "knowledge_graph": {"title": "Brand", "website": "https://www.brand.example/"},
        "organic_results": [
            {
                "title": "Careers at Brand",
                "link": "https://careers.brand.example/",
                "snippet": "Explore open roles at Brand.",
            }
        ],
    }


def test_google_jobs_keeps_the_listing_fields_and_only_the_pay_sentence_of_the_description():
    assert scrub("google_jobs", RAW_JOBS) == {
        "jobs_results": [
            {
                "title": "Customer Support Executive",
                "company_name": "Velmora Services",
                "location": "Indore, Madhya Pradesh, India",
                "extensions": ["2 days ago", "Full-time", "₹15K–₹18K a month"],
                "detected_extensions": {"salary": "₹15K–₹18K a month"},
                "description": "Salary: Rs. 15,000 - 18,000 per month.",
                "apply_options": [{"link": "https://jobs.portal.example/velmora"}],
            },
            {
                "title": "Support Associate",
                "company_name": "Tarsik BPO",
                "location": "Indore, Madhya Pradesh, India",
            },
        ]
    }


def test_google_maps_keeps_the_place_fields_from_local_results_and_place_results():
    assert scrub("google_maps", RAW_MAPS_LOCAL) == {
        "local_results": [
            {
                "title": "Contoso Pune Office",
                "place_id": "ChIJ-synthetic-place-0001",
                "type": "Corporate office",
                "address": "Hinjewadi Phase 1, Pune, Maharashtra 411057",
                "rating": 4.2,
                "reviews": 118,
            }
        ]
    }
    assert scrub("google_maps", RAW_MAPS_PLACE) == {
        "place_results": {
            "title": "Contoso Kharadi",
            "place_id": "ChIJ-synthetic-place-0002",
            "type": "Corporate office",
            "address": "Kharadi, Pune, Maharashtra 411014",
            "rating": 4.0,
            "reviews": 64,
        }
    }


def test_google_news_keeps_title_link_source_name_and_date_and_drops_profile_posts():
    assert scrub("google_news", RAW_NEWS) == {
        "news_results": [
            {
                "title": "Fake Brand job offers doing the rounds, police warn",
                "link": "https://news.example/fake-brand-offers",
                "source": {"name": "News Example"},
                "date": "09/12/2026, 07:00 AM, +0000 UTC",
            }
        ]
    }


def test_google_maps_reviews_never_keeps_the_reviewer_and_masks_the_text():
    assert scrub("google_maps_reviews", RAW_REVIEWS) == {
        "reviews": [
            {
                "snippet": (
                    "They asked me for a registration fee. "
                    "Call 7XXXX XXXXX or mail ***@mail.example"
                ),
                "rating": 1,
                "iso_date": "2026-08-14T09:30:00Z",
                "link": "https://www.google.example/maps/reviews/synthetic-1",
            }
        ]
    }


def test_the_whitelist_covers_every_engine_a_check_reads():
    assert set(WHITELIST) == {
        "google",
        "google_jobs",
        "google_maps",
        "google_news",
        "google_maps_reviews",
    }


def test_an_engine_without_a_whitelist_is_refused():
    with pytest.raises(ValueError, match="no field whitelist for engine 'bing'"):
        scrub("bing", {"organic_results": []})


def test_scrub_leaves_its_input_alone_and_is_idempotent():
    before = copy.deepcopy(RAW_JOBS)
    once = scrub("google_jobs", RAW_JOBS)
    assert RAW_JOBS == before
    assert scrub("google_jobs", once) == once


def test_missing_and_oddly_typed_fields_are_dropped_not_guessed():
    raw = {
        "knowledge_graph": "not an object",
        "organic_results": [
            "not an object",
            {"title": {"nested": "object"}, "link": "https://a.example/"},
            {"position": 3},
        ],
    }
    assert scrub("google", raw) == {"organic_results": [{"link": "https://a.example/"}]}
    assert scrub("google_maps", {"place_results": [{"title": "a list, not an object"}]}) == {}


def test_account_keeps_only_the_five_counts():
    account = {
        "account_id": "synthetic",
        "api_key": hashlib.sha256(b"not a key").hexdigest(),
        "account_email": "owner@mail.example",
        "account_status": "Active",
        "plan_id": "free",
        "plan_name": "Free Plan",
        "plan_monthly_price": 0.0,
        "searches_per_month": 250,
        "plan_searches_left": 240,
        "extra_credits": 0,
        "total_searches_left": 240,
        "this_month_usage": 10,
        "this_hour_searches": 2,
        "last_hour_searches": 2,
        "account_rate_limit_per_hour": 50,
    }
    assert scrub_account(account) == {
        "plan_searches_left": 240,
        "searches_per_month": 250,
        "this_month_usage": 10,
        "this_hour_searches": 2,
        "account_rate_limit_per_hour": 50,
    }
    assert set(ACCOUNT_FIELDS) == set(scrub_account(account))


@pytest.mark.parametrize(
    ("url", "profile"),
    [
        ("https://in.linkedin.com/in/a-recruiter-000", True),
        ("https://www.linkedin.com/in/a-recruiter", True),
        ("https://www.linkedin.com/pub/a-recruiter/0/0/0", True),
        ("https://www.linkedin.com/company/brand", False),
        ("https://www.linkedin.com/jobs/view/0", False),
        ("https://www.facebook.com/brand", True),
        ("https://m.facebook.com/someone", True),
        ("https://www.instagram.com/someone/", True),
        ("https://x.com/someone", True),
        ("https://mobile.twitter.com/someone", True),
        ("https://www.truecaller.com/search/in/0", True),
        ("https://careers.brand.example/", False),
        ("https://news.example/fake-brand-offers", False),
    ],
)
def test_people_profile_hosts(url, profile):
    assert is_people_profile(url) is profile


@pytest.mark.parametrize(
    "masked",
    [
        "Call +91 9XXXX XXXXX now",
        "+91-9XXXXXXXXX",
        "+919XXXXXXXXX",
        "(+91) 9XXXX XXXXX",
        "0091 9XXXX XXXXX",
        "+91 8X XXXX XXXX",
        "09XXXXXXXXX",
        "9XXXX-XXXXX",
        "9XX.XXX.XXXX",
        "landline 08X-XXXXXXXX",
        "UPI 9XXXXXXXXX@upi",
        "Salary details: call 9XXXX-XXXXX",
        "919XXXXXXXXX",
        "WhatsApp 919XXXXXXXXX to join",
        "+91 9XXXX–XXXXX",
        "9XXXX–XXXXX",
        "9XXXX—XXXXX",
        "9XXXX - XXXXX",
        "HR: 9XXXX – XXXXX",
        "9XXXX  XXXXX",
        "+91 9XXXX  XXXXX",
        "9XXXX/XXXXX",
        "+919XXXX_XXXXX",
        "9XXXX\u00a0XXXXX",
    ],
)
def test_phone_numbers_keep_only_their_first_digit(masked):
    text = unmasked(masked)
    assert find_phones(text)
    assert mask_phones(text) == masked
    assert find_phones(masked) == []


@pytest.mark.parametrize(
    "text",
    [
        "Salary ₹15000-20000 per month",
        "Salary: 65000-75000",
        "65000-75000 per month",
        "Rs. 12000/- per month",
        "₹15,000–₹20,000 a month",
        "CTC 3-4 LPA",
        "Maharashtra 411057",
        "https://news.example/articleshow/112345678.cms",
        "rated 4.5 by 1,234 people",
        "+91 9XXXX XXXXX",
        "2026-08-14T09:30:00Z",
        "Salary ₹15000 – 20000 per month",
        "09/12/2026, 07:00 AM, +0000 UTC",
    ],
)
def test_pay_figures_dates_codes_and_masked_numbers_are_not_phones(text):
    assert find_phones(text) == []
    assert mask_phones(text) == text


@pytest.mark.parametrize(
    "link",
    [
        "https://careers.contoso.example/jobs/1234567890?src=google",
        "https://www.linkedin.com/jobs/view/4012345678",
        "https://news.example/articleshow/1234567890.cms",
        "https://news.example/story?id=1234567890&ref=5123456789",
        "https://jobs.portal.example/listing-150925012345",
    ],
)
def test_an_id_in_a_link_is_not_a_phone(link):
    assert find_phones(link) == []
    assert clean_text(link) == link
    assert clean_text(f"Apply at {link} today") == f"Apply at {link} today"


@pytest.mark.parametrize(
    "masked",
    [
        "https://wa.me/919XXXXXXXXX",
        "https://api.whatsapp.com/send?phone=919XXXXXXXXX&text=Hi",
        "https://jobs.example/apply?phone=%2B919XXXXXXXXX",
        "https://jobs.example/apply?mobile=9XXXXXXXXX",
        "https://jobs.example/apply?contact=+91-9XXXX-XXXXX",
        "tel:+91-9XXXX-XXXXX",
        "https://jobs.example/contact/9XXXXXXXXX",
        "https://jobs.example/hr-09XXXXXXXXX",
    ],
)
def test_a_phone_written_into_a_link_keeps_only_its_first_digit(masked):
    text = unmasked(masked)
    assert find_phones(text)
    assert mask_phones(text) == masked
    assert find_phones(masked) == []


def test_links_keep_their_ids_through_the_scrub():
    raw = {
        "jobs_results": [
            {
                "title": "Graduate Engineer Trainee",
                "extensions": [unmasked("HR: 9XXXX – XXXXX")],
                "description": unmasked(
                    "Salary Rs 15,000 per month, WhatsApp 919XXXXXXXXX to join."
                ),
                "apply_options": [
                    {"link": "https://careers.contoso.example/jobs/1234567890?src=google"},
                    {"link": "https://www.linkedin.com/jobs/view/4012345678"},
                    {"link": unmasked("https://wa.me/919XXXXXXXXX")},
                ],
            }
        ]
    }
    assert scrub("google_jobs", raw)["jobs_results"][0] == {
        "title": "Graduate Engineer Trainee",
        "extensions": ["HR: 9XXXX – XXXXX"],
        "description": "Salary Rs 15,000 per month, WhatsApp 919XXXXXXXXX to join.",
        "apply_options": [
            {"link": "https://careers.contoso.example/jobs/1234567890?src=google"},
            {"link": "https://www.linkedin.com/jobs/view/4012345678"},
            {"link": "https://wa.me/919XXXXXXXXX"},
        ],
    }
    news = {
        "news_results": [{"title": "A", "link": "https://news.example/articleshow/1234567890.cms"}]
    }
    assert scrub("google_news", news) == news


def test_emails_keep_their_domain_and_lose_their_local_part():
    text = "Write to hr.team@brand.example, not to brand.hr+jobs@free-mail.example."
    assert mask_emails(text) == "Write to ***@brand.example, not to ***@free-mail.example."
    assert find_emails(mask_emails(text)) == []
    assert find_emails(text) == ["hr.team@brand.example", "brand.hr+jobs@free-mail.example"]


def test_an_email_written_into_a_link_or_run_into_a_word_is_masked():
    text = (
        "https://site.example/apply?email=a.person%40freemail.test and "
        "a.person@freemail.test-based scams"
    )
    assert find_emails(text) == ["a.person%40freemail.test", "a.person@freemail.test"]
    assert mask_emails(text) == (
        "https://site.example/apply?email=***%40freemail.test and ***@freemail.test-based scams"
    )
    assert find_emails(mask_emails(text)) == []


def test_key_shaped_values_are_removed():
    key = hashlib.sha256(b"synthetic").hexdigest()
    text = f"https://serpapi.example/search.json?q=a&api_key=abc123&x=1 and {key.upper()}"
    cleaned = clean_text(text)
    assert cleaned == (
        "https://serpapi.example/search.json?q=a&api_key=[removed]&x=1 and [removed]"
    )
    assert key not in cleaned.lower()


def test_masking_reaches_every_kept_string_including_lists():
    raw = {
        "jobs_results": [
            {
                "title": unmasked("Call 9XXXX XXXXX"),
                "extensions": ["hr@firm.example", "₹15K–₹18K a month"],
                "description": unmasked("Salary ₹15K–₹18K a month; call 9XXXX XXXXX."),
            }
        ]
    }
    job = scrub("google_jobs", raw)["jobs_results"][0]
    assert job["title"] == "Call 9XXXX XXXXX"
    assert job["extensions"] == ["***@firm.example", "₹15K–₹18K a month"]
    assert job["description"] == "Salary ₹15K–₹18K a month; call 9XXXX XXXXX."


@pytest.mark.parametrize(
    "description",
    [
        "Great team. Salary: ₹16,000 - ₹20,000 per month plus incentives. Apply now.",
        unmasked("Night shifts.\nCTC 3-4 LPA\nCall 9XXXX XXXXX."),
        "Stipend Rs. 10,000 to 12,000 a month. Incentives ₹2,000-₹5,000 on top.",
        "Pay 18k-22k pm, in hand. Freshers welcome!",
    ],
)
def test_a_trimmed_description_gives_the_pay_benchmark_the_same_figure(description):
    trimmed = pay_sentences(description)
    assert len(trimmed) < len(description)
    before = listing_pay({"description": description})
    after = listing_pay({"description": trimmed})
    assert before is not None
    assert (after.raw, after.monthly_inr) == (before.raw, before.monthly_inr)


def test_a_description_with_no_pay_is_dropped():
    assert pay_sentences("Voice process, rotational shifts. Graduates preferred.") == ""
