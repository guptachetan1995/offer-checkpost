"""The secret and personal-data scans.

1. Nothing in the repository's tree is key-shaped: no 64-hex run (lock-file hashes aside) and
   no ``SERPAPI_KEY=`` with a value. The tree is what git would commit: the directories and
   files the entry's ``.gitignore`` leaves out are skipped, ``.env`` is never opened.
2. Nothing under ``recordings/`` or ``tests/fixtures/`` identifies a person: no reviewer field
   under ``reviews``, no result on a people-profile host, no email address outside
   ``.example``, no phone number that still shows its digits.
3. The scrubber's output passes scan 2 whatever SerpApi sent.

Every violation planted below is built at runtime, so this file passes its own scans."""

import hashlib
import itertools
import json
import os
import re
from pathlib import Path

import pytest

from offer_checkpost.scrub import find_emails, find_phones, is_people_profile, scrub, scrub_account

ENTRY = Path(__file__).resolve().parents[1]
SKIPPED_DIRS = {
    ".git",
    ".venv",
    ".venv-tools",
    ".cache",
    "out",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
HEX64 = re.compile(rb"[0-9a-fA-F]{64}")
LOCK_HASH = re.compile(rb"--hash=sha256:[0-9a-f]{64}")
KEY_WITH_VALUE = re.compile(rb"SERPAPI_KEY[ \t]*=[ \t]*[\"']?[A-Za-z0-9]")
REVIEWER_KEYS = {"user", "contributor_id", "profile", "thumbnail"}


def unmasked(masked: str) -> str:
    digits = itertools.cycle("0123456789")
    return re.sub("X", lambda _: next(digits), masked)


def is_local_env(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and name != ".env.example")


def tree_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
        for name in sorted(filenames):
            if not is_local_env(name) and not name.endswith(".pyc"):
                yield Path(dirpath) / name


def key_shaped(path: Path) -> list[str]:
    content = path.read_bytes()
    if path.suffix == ".lock":
        content = LOCK_HASH.sub(b"", content)
    found = [f"64-hex run at byte {m.start()}" for m in HEX64.finditer(content)]
    found += [
        f"SERPAPI_KEY with a value at byte {m.start()}" for m in KEY_WITH_VALUE.finditer(content)
    ]
    return found


def personal_data(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".json":
        return _text_problems(text)
    problems: list[str] = []
    _walk(json.loads(text), False, problems)
    return problems


def _walk(node, under_reviews: bool, problems: list[str]) -> None:
    if isinstance(node, dict):
        if under_reviews and (reviewer := REVIEWER_KEYS & set(node)):
            problems.append(f"reviewer fields under reviews: {sorted(reviewer)}")
        link = node.get("link")
        if isinstance(link, str) and is_people_profile(link):
            problems.append(f"a result on a people-profile host: {link}")
        for name, value in node.items():
            _walk(value, under_reviews or name == "reviews", problems)
    elif isinstance(node, list):
        for value in node:
            _walk(value, under_reviews, problems)
    elif isinstance(node, str):
        problems += _text_problems(node)


def _text_problems(text: str) -> list[str]:
    emails = [e for e in find_emails(text) if not e.lower().endswith(".example")]
    return [f"email outside .example: {e}" for e in emails] + [
        f"unmasked phone number: {p}" for p in find_phones(text)
    ]


def data_files():
    for folder in (ENTRY / "recordings", ENTRY / "tests" / "fixtures"):
        if folder.is_dir():
            yield from tree_files(folder)


# ---- the real tree --------------------------------------------------------------------------


def test_the_tree_holds_nothing_key_shaped():
    files = list(tree_files(ENTRY))
    names = {f.relative_to(ENTRY).as_posix() for f in files}
    assert {"README.md", ".env.example", "requirements.lock", "pyproject.toml"} <= names
    assert not any(n.startswith((".venv/", ".cache/")) or n == ".env" for n in names)
    found = {f.relative_to(ENTRY).as_posix(): key_shaped(f) for f in files}
    assert {name: problems for name, problems in found.items() if problems} == {}


def test_the_skipped_directories_are_the_ones_git_ignores():
    ignored = {
        line.strip().rstrip("/") for line in (ENTRY / ".gitignore").read_text().splitlines()
    }
    assert SKIPPED_DIRS - {".git"} <= ignored
    assert {".env", ".env.*", "!.env.example"} <= ignored


def test_recordings_and_fixtures_identify_nobody():
    found = {f.relative_to(ENTRY).as_posix(): personal_data(f) for f in data_files()}
    assert {name: problems for name, problems in found.items() if problems} == {}


# ---- the scans catch what they look for -----------------------------------------------------


def test_the_key_scan_catches_a_key_and_skips_lock_hashes_and_the_local_env(tmp_path):
    key = hashlib.sha256(b"planted").hexdigest()
    (tmp_path / "config.py").write_text(f"KEY = '{key}'\n")
    (tmp_path / "notes.md").write_text("SERPAPI_" + "KEY=" + "abc123\n")
    (tmp_path / "placeholder.md").write_text("SERPAPI_KEY=\nSERPAPI_KEY=…\n`SERPAPI_KEY=`\n")
    (tmp_path / "requirements.lock").write_text(f"serpapi==1.1.2 \\\n    --hash=sha256:{key}\n")
    (tmp_path / "stray.lock").write_text(f"# pasted by mistake: {key}\n")
    (tmp_path / ".env").write_text(f"SERPAPI_KEY={key}\n")
    (tmp_path / ".env.local").write_text(f"SERPAPI_KEY={key}\n")
    (tmp_path / ".env.example").write_text("SERPAPI_KEY=\n")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / f"{key}.json").write_text("{}")

    flagged = {f.name for f in tree_files(tmp_path) if key_shaped(f)}
    assert flagged == {"config.py", "notes.md", "stray.lock"}
    assert ".env" not in {f.name for f in tree_files(tmp_path)}


# Every way of writing a phone number that the scan must catch, masked; each is planted with
# its digits filled in.
PHONE_SPELLINGS = [
    "call +91 9XXXX XXXXX",
    "919XXXXXXXXX",
    "WhatsApp 919XXXXXXXXX",
    "+91 9XXXX–XXXXX",
    "9XXXX–XXXXX",
    "9XXXX - XXXXX",
    "9XXXX  XXXXX",
    "+91 9XXXX  XXXXX",
    "9XXXX/XXXXX",
    "+919XXXX_XXXXX",
]
PHONE_LINKS = [
    "https://wa.me/919XXXXXXXXX",
    "https://api.whatsapp.com/send?phone=919XXXXXXXXX",
    "https://jobs.example/apply?phone=%2B919XXXXXXXXX",
    "tel:+91-9XXXX-XXXXX",
    "https://jobs.example/contact/9XXXXXXXXX",
]


def test_the_personal_data_scan_catches_each_kind(tmp_path):
    planted = {
        "reviewer.json": {"reviews": [{"snippet": "ok", "user": {"name": "A Reviewer"}}]},
        "thumbnail.json": {"reviews": [{"snippet": "ok", "thumbnail": "https://img.example/a"}]},
        "profile.json": {"organic_results": [{"link": "https://in.linkedin.com/in/someone"}]},
        "email.json": {"organic_results": [{"snippet": "mail a.person@freemail.test"}]},
        "email-in-link.json": {
            "organic_results": [
                {"link": "https://site.example/apply?email=a.person%40freemail.test"}
            ]
        },
        "email-run-on.json": {"organic_results": [{"snippet": "a.person@freemail.test-based"}]},
        "notice.md": unmasked("Call 9XXXX-XXXXX"),
        **{
            f"phone-{i}.json": {"organic_results": [{"snippet": unmasked(spelling)}]}
            for i, spelling in enumerate(PHONE_SPELLINGS)
        },
        **{
            f"phone-link-{i}.json": {
                "jobs_results": [{"apply_options": [{"link": unmasked(url)}]}]
            }
            for i, url in enumerate(PHONE_LINKS)
        },
    }
    clean = {
        "clean.json": {
            "reviews": [{"snippet": "They asked for a fee.", "rating": 1}],
            "local_results": [{"title": "Office", "reviews": 40}],
            "organic_results": [
                {"snippet": "Mail hr@brand.example or ***@brand.test, call +91 9XXXX XXXXX."},
                {"link": "https://www.linkedin.com/company/brand"},
                {"link": "https://careers.contoso.example/jobs/1234567890?src=google"},
            ],
            "jobs_results": [
                {
                    "extensions": ["₹15000-20000 per month", "₹15000 – 20000 per month"],
                    "apply_options": [
                        {"link": "https://www.linkedin.com/jobs/view/4012345678"},
                        {"link": "https://wa.me/919XXXXXXXXX"},
                    ],
                }
            ],
            "news_results": [{"link": "https://news.example/articleshow/1234567890.cms"}],
        }
    }
    for name, content in {**planted, **clean}.items():
        text = content if isinstance(content, str) else json.dumps(content)
        (tmp_path / name).write_text(text)
    flagged = {f.name for f in tree_files(tmp_path) if personal_data(f)}
    assert flagged == set(planted)


# ---- scrubbed output passes -----------------------------------------------------------------

UNSCRUBBED = {
    "google": {
        "search_parameters": {"api_key_hint": "api_key=abc", "q": "Brand"},
        "knowledge_graph": {"title": "Brand", "website": "https://www.brand.example/"},
        "organic_results": [
            {"title": "A Person | LinkedIn", "link": "https://www.linkedin.com/in/a-person"},
            {"title": "Brand", "link": "https://www.facebook.com/brand"},
            {
                "title": "Beware of fake offers",
                "link": "https://www.brand.example/fraud",
                "snippet": unmasked(
                    "Mail careers@brand.test or call +91 9XXXX–XXXXX, or 9XXXX  XXXXX, or "
                    "+919XXXX_XXXXX. Not a.person@freemail.test-based."
                ),
            },
        ],
    },
    "google_jobs": {
        "jobs_results": [
            {
                "title": "Support Executive",
                "thumbnail": "https://img.example/logo.png",
                "extensions": [
                    unmasked("Call 8XXXXXXXXX"),
                    "hr.desk@firm.test",
                    unmasked("HR: 9XXXX – XXXXX"),
                    unmasked("9XXXX/XXXXX"),
                ],
                "description": unmasked(
                    "Salary ₹15,000-₹18,000 per month, WhatsApp 919XXXXXXXXX to join. "
                    "Contact Ms. A at 7XXXX XXXXX."
                ),
                "apply_options": [
                    {"link": "https://www.facebook.com/jobs/1"},
                    {"link": unmasked("https://wa.me/919XXXXXXXXX")},
                    {"link": unmasked("https://api.whatsapp.com/send?phone=919XXXXXXXXX")},
                    {"link": "https://site.example/apply?email=a.person%40freemail.test"},
                ],
            }
        ]
    },
    "google_maps": {
        "local_results": [
            {
                "title": "Office",
                "phone": unmasked("+91 8XXXX XXXXX"),
                "address": unmasked("Sector 62, Noida; call 9XXXXXXXXX"),
                "thumbnail": "https://img.example/office.png",
            }
        ]
    },
    "google_news": {
        "news_results": [
            {"title": "Scam alert", "link": "https://x.com/someone/status/1"},
            {
                "title": "Fake offers reported",
                "link": "https://news.example/a",
                "source": {"name": "N"},
            },
        ]
    },
    "google_maps_reviews": {
        "reviews": [
            {
                "snippet": unmasked(
                    "They took my fee. Mail me at a.person@freemail.test, 6XXXXXXXXX"
                ),
                "user": {"name": "A Reviewer", "contributor_id": "1", "thumbnail": "t"},
                "profile": "https://maps.example/contrib/1",
                "link": "https://maps.example/review/1",
            }
        ]
    },
}


@pytest.mark.parametrize("engine", sorted(UNSCRUBBED))
def test_scrubbed_responses_identify_nobody(tmp_path, engine):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(UNSCRUBBED[engine]))
    assert personal_data(raw), "the planted response should fail the scan"

    scrubbed = tmp_path / "scrubbed.json"
    scrubbed.write_text(json.dumps(scrub(engine, UNSCRUBBED[engine])))
    assert personal_data(scrubbed) == []
    assert key_shaped(scrubbed) == []


def test_scrubbed_account_holds_no_key_and_no_email():
    key = hashlib.sha256(b"account").hexdigest()
    account = {
        "api_key": key,
        "account_email": "owner@mail.example",
        "account_id": "1",
        "plan_name": "Free Plan",
        "plan_searches_left": 240,
        "searches_per_month": 250,
        "this_month_usage": 10,
        "this_hour_searches": 1,
        "last_hour_searches": 1,
        "account_rate_limit_per_hour": 50,
    }
    counts = scrub_account(account)
    assert sorted(counts) == [
        "account_rate_limit_per_hour",
        "plan_searches_left",
        "searches_per_month",
        "this_hour_searches",
        "this_month_usage",
    ]
    assert key not in json.dumps(counts) and "owner" not in json.dumps(counts)
