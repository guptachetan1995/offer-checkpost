"""Trims a SerpApi response to what a reader uses before it is written into ``recordings/``.

``json_restrictor`` already asks SerpApi for only these fields, so reviewer identity is never
fetched. ``scrub`` applies the same per-engine whitelist again, because a recording is
published with the repository and a restrictor string can drift from the readers. After the
whitelist it:

- drops every result whose ``link`` is a person's profile (LinkedIn ``/in/``, Facebook,
  Instagram, X/Twitter, Truecaller);
- cuts a Google Jobs listing's ``description`` down to the sentences that carry pay, the only
  thing the pay benchmark reads from it;
- masks every email address (``***@domain``: the domain stays, since a fraud notice naming a
  look-alike domain is evidence) and every phone number (``+91 9XXXX XXXXX``: the first digit
  stays, the way the sample offers write them), in any spacing or punctuation. Inside a link
  only a number written as a phone (``tel:``, ``wa.me/``, ``?phone=``) or shaped like an
  Indian mobile number is masked, so a job or article id survives and the evidence link
  still opens;
- removes anything key-shaped: a 64-hex run, or an ``api_key=`` value.

``scrub_account`` keeps the five Account API counts and nothing else, so ``api_key`` and
``account_email`` never leave the provider.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

from offer_checkpost.domains import registrable_domain
from offer_checkpost.extract import _sentences
from offer_checkpost.salary import parse_pay

_PLACE_FIELDS = ("title", "place_id", "type", "address", "rating", "reviews")

WHITELIST: dict[str, tuple[str, ...]] = {
    "google": (
        "knowledge_graph.title",
        "knowledge_graph.website",
        "organic_results[].title",
        "organic_results[].link",
        "organic_results[].snippet",
    ),
    "google_jobs": (
        "jobs_results[].title",
        "jobs_results[].company_name",
        "jobs_results[].location",
        "jobs_results[].detected_extensions.salary",
        "jobs_results[].extensions",
        "jobs_results[].description",
        "jobs_results[].apply_options[].link",
    ),
    # One exact match comes back as a single ``place_results`` object instead of a list.
    "google_maps": (
        *(f"local_results[].{field}" for field in _PLACE_FIELDS),
        *(f"place_results.{field}" for field in _PLACE_FIELDS),
    ),
    "google_news": (
        "news_results[].title",
        "news_results[].link",
        "news_results[].source.name",
        "news_results[].date",
    ),
    "google_maps_reviews": (
        "reviews[].snippet",
        "reviews[].rating",
        "reviews[].iso_date",
        "reviews[].link",
    ),
}

ACCOUNT_FIELDS = (
    "plan_searches_left",
    "searches_per_month",
    "this_month_usage",
    "this_hour_searches",
    "account_rate_limit_per_hour",
)

PEOPLE_PROFILE_DOMAINS = frozenset(
    {"facebook.com", "fb.com", "instagram.com", "x.com", "twitter.com", "truecaller.com"}
)
# LinkedIn also hosts company pages and job listings; only these paths are a person.
LINKEDIN_PROFILE_PATHS = ("/in/", "/pub/")

REMOVED = "[removed]"

Shape = dict[str, tuple[bool, "Shape | None"]]


def _compile(paths: tuple[str, ...]) -> Shape:
    root: Shape = {}
    for path in paths:
        node = root
        segments = path.split(".")
        for i, segment in enumerate(segments):
            name = segment.removesuffix("[]")
            leaf = i == len(segments) - 1
            node.setdefault(name, (segment.endswith("[]"), None if leaf else {}))
            node = node[name][1]
    return root


_SHAPES = {engine: _compile(paths) for engine, paths in WHITELIST.items()}


def scrub(engine: str, response: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``response`` holding only ``engine``'s whitelisted fields, cleaned for a
    public recording. The input is left as it was."""
    if engine not in _SHAPES:
        raise ValueError(f"no field whitelist for engine {engine!r}")
    kept = _keep(response, _SHAPES[engine])
    for job in kept.get("jobs_results", []):
        if "description" in job:
            job["description"] = pay_sentences(job["description"])
            if not job["description"]:
                del job["description"]
    return _clean(kept)


def scrub_account(account: Mapping[str, Any]) -> dict[str, Any]:
    """The Account API response reduced to the five counts ``search_budget`` shows."""
    return {field: account[field] for field in ACCOUNT_FIELDS if field in account}


def _keep(obj: Mapping[str, Any], shape: Shape) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, (many, child) in shape.items():
        if name not in obj:
            continue
        value = obj[name]
        if many:
            if isinstance(value, list):
                items = (_item(v, child) for v in value if not _is_profile_result(v))
                out[name] = [item for item in items if item]
        elif child is not None:
            if isinstance(value, Mapping) and (kept := _keep(value, child)):
                out[name] = kept
        elif (leaf := _leaf(value)) is not None:
            out[name] = leaf
    return out


def _item(value: Any, child: Shape | None) -> Any:
    if child is None:
        return _leaf(value)
    return _keep(value, child) if isinstance(value, Mapping) else None


def _leaf(value: Any) -> Any:
    scalar = (str, int, float, bool)
    if isinstance(value, scalar):
        return value
    if isinstance(value, list) and all(isinstance(v, scalar) for v in value):
        return list(value)
    return None


def _is_profile_result(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("link"), str)
        and is_people_profile(value["link"])
    )


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def is_people_profile(url: str) -> bool:
    """True when ``url`` is a page about a person: a LinkedIn profile, or anything on
    Facebook, Instagram, X/Twitter or Truecaller."""
    domain = registrable_domain(url)
    if domain in PEOPLE_PROFILE_DOMAINS:
        return True
    if domain != "linkedin.com":
        return False
    path = urlsplit(url if "://" in url else "//" + url).path.lower()
    return path.startswith(LINKEDIN_PROFILE_PATHS)


def pay_sentences(description: str) -> str:
    """The sentences of a listing description that carry a pay figure, one per line."""
    kept = (description[s:e] for s, e in _sentences(description))
    return "\n".join(sentence for sentence in kept if parse_pay(sentence))


# ---- masking ---------------------------------------------------------------------------------

# "%40" is "@" written into a link.
_EMAIL = re.compile(
    r"(?<![\w.%+-])[\w.%+-]+(?P<at>@|%40)"
    r"(?P<domain>(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?!\w)",
    re.IGNORECASE,
)

# What may sit between a phone number's digits: up to three spaces (a no-break space too),
# dots, hyphens, en or em dashes, slashes, underscores or parentheses.
_SEPARATORS = "[ \t\u00a0.\\-–—/_()]"
_GAP = _SEPARATORS + "{0,3}"
# A number written with +91 (or 0091) is a phone whatever its length. Without it: 91 and a
# mobile number, or ten digits, optionally after a trunk 0.
_PHONE = re.compile(
    rf"(?<![\w+])(?:"
    rf"(?P<intl>(?:\(\s*)?(?:\+|00)91(?:\s*\))?{_GAP})(?P<intl_digits>\d(?:{_GAP}\d){{5,11}})"
    rf"|(?P<cc>91{_GAP})(?P<cc_digits>[6-9](?:{_GAP}\d){{9}})"
    rf"|(?P<trunk>0{_GAP})?(?P<digits>\d(?:{_GAP}\d){{9}})"
    r")(?!\w)"
)
# Links: a run of digits in a link is an id (a job, an article) unless it is written as a
# phone, after tel:, wa.me/ or a phone-like query parameter, or is an Indian mobile number.
_LINK = re.compile(r"(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|\bwa\.me/|\btel:)[^\s<>\"']*", re.I)
_PHONE_MARK = re.compile(
    r"(?:tel:|wa\.me/|[?&;](?:phone|mobile|mob|tel|whatsapp|contact)=)(?:\+|%2B)?$", re.I
)
_MARKED_NUMBER = re.compile(r"\d(?:(?:[-.]|%20)?\d){6,}")
_MOBILE = re.compile(r"(?:(?<=%[0-9A-Fa-f]{2})|(?<!\d))(?P<prefix>91|0)?[6-9]\d{9}(?!\d)")
# "₹15000-20000" and "salary 65000-75000 per month" have the shape of a phone number written
# 5-5 with a hyphen; the words around them are what make them pay.
_MONEY_BEFORE = re.compile(
    r"(?:₹|\brs\.?|\binr|\bsalary|\bstipend|\bpay|\bctc|\bpackage)[\s:.\-–]*"
    r"(?:(?:of|is|upto|up to|around|approx\.?)\s+)?$",
    re.IGNORECASE,
)
_MONEY_AFTER = re.compile(
    r"^\s*(?:/-|k\b|lpa\b|lakhs?\b|lacs?\b|per\b|pm\b|p\.m\.|a month\b|monthly\b"
    r"|/\s*(?:month|mo|m)\b|rupees?\b|rs\b|inr\b|p\.a\.|pa\b)",
    re.IGNORECASE,
)

_KEY_LIKE = re.compile(r"[0-9a-fA-F]{64,}")
_API_KEY_VALUE = re.compile(r"(api_key=)[^&\s\"'<>]+", re.IGNORECASE)


def _is_pay(text: str, start: int, end: int) -> bool:
    return bool(
        _MONEY_BEFORE.search(text[max(0, start - 24) : start])
        or _MONEY_AFTER.match(text[end : end + 12])
    )


def _segments(text: str):
    # (piece, is_link) in order.
    last = 0
    for m in _LINK.finditer(text):
        yield text[last : m.start()], False
        yield m.group(), True
        last = m.end()
    yield text[last:], False


def _mask_digits(number: str, keep: int) -> str:
    # Separators and a link's percent escapes ("%20") stay as they are.
    seen = 0

    def one(m: re.Match[str]) -> str:
        nonlocal seen
        if m.group().startswith("%"):
            return m.group()
        seen += 1
        return m.group() if seen <= keep else "X"

    return re.sub(r"%[0-9A-Fa-f]{2}|\d", one, number)


def _mask_text(text: str) -> str:
    out, last = [], 0
    for m in _PHONE.finditer(text):
        if not m["intl"] and _is_pay(text, m.start(), m.end()):
            continue
        prefix = m["intl"] or m["cc"] or m["trunk"] or ""
        number = m["intl_digits"] or m["cc_digits"] or m["digits"]
        out += [text[last : m.start()], prefix + _mask_digits(number, 1)]
        last = m.end()
    return "".join(out) + text[last:]


def _mask_link(link: str) -> str:
    def marked(m: re.Match[str]) -> str:
        if not _PHONE_MARK.search(link, 0, m.start()):
            return m.group()
        digits = re.sub(r"%[0-9A-Fa-f]{2}|\D", "", m.group())
        return _mask_digits(m.group(), 3 if digits.startswith("91") and len(digits) >= 12 else 1)

    def mobile(m: re.Match[str]) -> str:
        prefix = m["prefix"] or ""
        return prefix + _mask_digits(m.group()[len(prefix) :], 1)

    return _MOBILE.sub(mobile, _MARKED_NUMBER.sub(marked, link))


def mask_phones(text: str) -> str:
    """Each phone number with every digit after its first written as ``X`` (a ``+91``,
    ``91`` or trunk ``0`` in front stays). In a link, only a number written as a phone
    (after ``tel:``, ``wa.me/`` or a ``phone=``-like parameter) or shaped like an Indian
    mobile number is masked, so the link's other ids survive and it still opens."""
    return "".join(
        _mask_link(piece) if is_link else _mask_text(piece) for piece, is_link in _segments(text)
    )


_DIGIT_RUN = re.compile(rf"\d+(?:{_SEPARATORS}{{1,3}}\d+)*")
_LINK_DIGITS = re.compile(r"\d(?:[-. ()]?\d)*")
_MOBILE_DIGITS = re.compile(r"(?:91|0)?[6-9]\d{9}")


def _phone_digits(digits: str, intl: bool) -> bool:
    if intl:
        return len(digits.removeprefix("00")) >= 8
    return (
        len(digits) == 10
        or (len(digits) == 11 and digits[0] == "0")
        or (len(digits) == 12 and digits.startswith("91"))
    )


def _text_phones(text: str) -> list[str]:
    found = []
    for run in _DIGIT_RUN.finditer(text):
        groups = list(re.finditer(r"\d+", run.group()))
        plus = text[: run.start()].endswith("+")
        i = 0
        while i < len(groups):
            for j in range(i + 1, len(groups) + 1):
                start, end = run.start() + groups[i].start(), run.start() + groups[j - 1].end()
                digits = "".join(g.group() for g in groups[i:j])
                intl = (plus and i == 0 and digits.startswith("91")) or digits.startswith("0091")
                if _phone_digits(digits, intl) and (intl or not _is_pay(text, start, end)):
                    found.append(text[start:end])
                    i = j - 1
                    break
            i += 1
    return found


def _link_phones(link: str) -> list[str]:
    text = unquote(link)
    found = []
    for m in _LINK_DIGITS.finditer(text):
        if _PHONE_MARK.search(text, 0, m.start()) and len(re.sub(r"\D", "", m.group())) >= 7:
            found.append(m.group())
        else:
            found += [d for d in re.findall(r"\d+", m.group()) if _MOBILE_DIGITS.fullmatch(d)]
    return found


def find_phones(text: str) -> list[str]:
    """Every phone number in ``text`` that still shows its digits: the personal-data scan's
    reading, built apart from ``mask_phones`` so a spelling the masker misses is still caught.

    Outside a link it joins digit groups across up to three separators and flags ten digits,
    eleven starting with 0, twelve starting with 91, or eight or more after ``+91`` or
    ``0091``, unless the words around them make them pay. In a link (read decoded) it flags
    a number of seven or more digits after ``tel:``, ``wa.me/`` or a ``phone=``-like
    parameter, and any run of digits that is an Indian mobile number."""
    found = []
    for piece, is_link in _segments(text):
        found += _link_phones(piece) if is_link else _text_phones(piece)
    return found


def find_emails(text: str) -> list[str]:
    """Every email address in ``text`` whose local part is still readable, including one
    written into a link with ``%40`` for ``@``."""
    return [m.group() for m in _EMAIL.finditer(text)]


def mask_emails(text: str) -> str:
    """Each email address with its local part replaced by ``***``; the domain stays."""
    return _EMAIL.sub(lambda m: "***" + m["at"] + m["domain"], text)


def clean_text(text: str) -> str:
    """``text`` with key-shaped values removed and emails and phone numbers masked."""
    text = _API_KEY_VALUE.sub(lambda m: m[1] + REMOVED, _KEY_LIKE.sub(REMOVED, text))
    return mask_phones(mask_emails(text))
