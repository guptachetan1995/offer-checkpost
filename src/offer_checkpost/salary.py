"""Listing pay for the ``pay_outlier`` benchmark: parse, normalise to monthly INR, take a median.

A Google Jobs listing carries pay in ``detected_extensions.salary``, else only in one of its
``extensions[]`` strings, else only in its ``description``. ``listing_pay`` reads the first of
those that yields a figure; from a description it takes only a ₹/K/LPA/lakh range, because a
lone figure in free text is as often an incentive, an allowance or a target as the pay. Each
listing contributes one data point, the midpoint of its range, and ``is_pay_outlier`` is the
``pay_outlier`` condition exactly: the offered monthly pay is more than 2.0x the median of at
least 3 listings that carry pay.

``parse_pay`` reads Indian formats: ``₹``/``Rs``/``INR``, Indian digit grouping
(``2,50,000``), ``K``, ``L``/lakh/lac, ``LPA``, crore, ranges (``-``, ``to``, Hinglish ``se``),
and periods in English or Hinglish (``per month``, ``pm``, ``p.a.``, ``mahina``, ``saal``).
Hourly, daily and weekly pay is left out, because turning it into a month would mean guessing
the hours worked.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

PAY_OUTLIER_FACTOR = 2.0
MIN_PAY_DATA_POINTS = 3

# Below this, a parsed figure is a fee, a count or a clock time, never a month's pay.
MIN_MONTHLY_INR = 1_000
# Indian listings quote CTC in lakhs a year and in-hand pay in thousands a month, so a range
# with no stated period reads as annual from one lakh up.
LAKH = 100_000
# Ends further apart than this are two unrelated numbers (pay next to a count), not a range.
MAX_RANGE_RATIO = 10

PaySource = Literal["detected_extensions.salary", "extensions", "description"]

UNITS = {
    "k": 1_000,
    "thousand": 1_000,
    "hazar": 1_000,
    "hazaar": 1_000,
    "hajar": 1_000,
    "l": LAKH,
    "lac": LAKH,
    "lacs": LAKH,
    "lakh": LAKH,
    "lakhs": LAKH,
    "lpa": LAKH,
    "cr": 10_000_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}


def _either(words: Iterable[str]) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


_NUMBER = r"(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?"
_CURRENCY = r"(?:₹|\brs\.?|\binr)(?![a-z])"
_TAIL = r"(?:/-|(?:rupees|rupee|rupaye|rupay|rupiya|rs\.?|inr)(?![a-z]))"
_UNIT = rf"(?:{_either(UNITS)})(?![a-z])"


def _amount(tag: str) -> str:
    return (
        rf"(?:(?P<cur_{tag}>{_CURRENCY})\s*|(?<![\w.,]))(?P<num_{tag}>{_NUMBER})"
        rf"(?:\s*(?P<unit_{tag}>{_UNIT}))?(?:\s*(?P<tail_{tag}>{_TAIL}))?"
    )


_PREFIX = (
    r"(?:(?<![a-z])(?:"
    r"(?P<pre_month>monthly|per\s+month|har\s+mahine|mahine\s+k[aei]|mahina|mahine)"
    r"|(?P<pre_year>annual|annually|yearly|per\s+annum|per\s+year|salana|saalana"
    r"|saal\s+k[aei]|ctc|package))"
    r"(?![a-z])[\s:()\-–]*"
    r"(?:(?:salary|pay|stipend|ctc|package|income|kamai)(?![a-z])[\s:()\-–]*)?"
    r"(?:(?:of|is|hai|upto|up\s+to|around|approx\.?)\s+)?)?"
)
_RANGE = rf"(?:\s*(?:[-–—~‑]|\bto\b|\bse\b)\s*{_amount('b')})?"
# A qualifier or a period stays on the figure's own line: "₹1.40 LPA – ₹1.80 LPA" followed by a
# line "Monthly Salary: ₹11,700" is a year's pay, and "Monthly" labels the next figure.
_SAME_LINE = r"[^\S\n]*"
_QUALIFIER = (
    rf"(?:{_SAME_LINE}(?:ctc|salary|stipend|fixed|gross|net|in[- ]hand|take[- ]home)(?![a-z]))?"
)
_SUFFIX = (
    rf"(?:{_SAME_LINE}(?:(?:per|a|an|every|har|prati)\s+|/\s*)?(?:"
    rf"(?P<per_month>{_either(['month', 'mo', 'mth', 'monthly', 'mahina', 'mahine', 'maheena'])}"
    r"|p\.?m\.?)"
    rf"|(?P<per_year>{_either(['year', 'yr', 'annum', 'annual', 'annually', 'yearly'])}"
    r"|saal|sal|salana|saalana|p\.?a\.?)"
    rf"|(?P<per_other>{_either(['hour', 'hr', 'hourly', 'day', 'daily', 'week', 'wk'])}"
    r"|weekly|din|ghanta)"
    r")(?![a-z]))?"
)
_PAY = re.compile(_PREFIX + _amount("a") + _RANGE + _QUALIFIER + _SUFFIX, re.IGNORECASE)
_MARKS = ("cur_a", "tail_a", "unit_a", "cur_b", "tail_b", "unit_b")

# The nearest label before a figure says what it is: "Salary ₹15,000" is pay, "Incentives
# ₹2,000–₹5,000" and "a target of 1500 per month" are not.
_LABEL = re.compile(
    r"(?<![a-z])(?:(?P<pay>salary|stipend|ctc|package|pay|wages?|income|remuneration|earn\w*)"
    r"|(?P<other>incentives?|allowances?|bonus(?:es)?|targets?|commissions?|reimburs\w*"
    r"|rewards?|insurance|cover|fees?|deposit))(?![a-z])",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(r"[;!?\n]|\.\s")


@dataclass(frozen=True)
class Pay:
    raw: str
    monthly_inr: int


@dataclass(frozen=True)
class ListingPay:
    source: PaySource
    raw: str
    monthly_inr: int


@dataclass(frozen=True)
class PayBenchmark:
    listings: int
    pays: tuple[ListingPay, ...]

    @property
    def found(self) -> int:
        return len(self.pays)

    @property
    def enough(self) -> bool:
        return self.found >= MIN_PAY_DATA_POINTS

    @property
    def median_monthly_inr(self) -> float | None:
        return statistics.median(p.monthly_inr for p in self.pays) if self.pays else None

    @property
    def coverage(self) -> str:
        return f"pay found in {self.found} of {self.listings} listings"


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _read(m: re.Match[str]) -> Pay | None:
    if m["per_other"]:
        return None
    a = _number(m["num_a"])
    b = _number(m["num_b"]) if m["num_b"] else None
    unit_a = (m["unit_a"] or "").lower()
    unit_b = (m["unit_b"] or "").lower()
    # "20-30K" and "3-5 LPA" write one unit for both ends; "800-1.2K" does not.
    if b is not None and not unit_a and unit_b and a <= b:
        unit_a = unit_b
    low = a * UNITS.get(unit_a, 1)
    high = b * UNITS.get(unit_b, 1) if b is not None else low
    if max(low, high) > MAX_RANGE_RATIO * min(low, high):
        return None

    explicit = m["per_month"] or m["per_year"] or m["pre_month"] or m["pre_year"]
    marked = any(m[g] for g in _MARKS)
    if not (marked or explicit):
        return None

    # LPA carries its own period, lakhs per annum, whatever word follows it.
    if "lpa" in (unit_a, unit_b) or m["per_year"]:
        months = 12
    elif m["per_month"]:
        months = 1
    elif m["pre_month"]:
        months = 1
    elif m["pre_year"]:
        months = 12
    elif b is not None:
        months = 12 if max(low, high) >= LAKH else 1
    else:
        # A lone figure with no period is as often a bonus, a fee or an insurance cover as pay.
        return None

    monthly = round((low + high) / 2 / months)
    return Pay(m.group(0), monthly) if monthly >= MIN_MONTHLY_INR else None


def _labelled_other(text: str, start: int) -> bool:
    clause = _CLAUSE_BREAK.split(text[max(0, start - 40) : start])[-1]
    labels = list(_LABEL.finditer(clause))
    return bool(labels) and labels[-1].lastgroup == "other"


def _explicit_period(m: re.Match[str]) -> bool:
    units = ((m["unit_a"] or "").lower(), (m["unit_b"] or "").lower())
    return bool(m["per_month"] or m["per_year"] or m["pre_month"] or m["pre_year"]) or (
        "lpa" in units
    )


def parse_pay(text: str, *, ranges_only: bool = False) -> Pay | None:
    """The pay figure in ``text``, normalised to monthly INR, or None if it has none.

    The first figure with a stated period wins; a range with none counts only when no
    figure states one. A figure labelled as an incentive, allowance, bonus, target, fee or
    reimbursement is skipped. ``ranges_only`` reads nothing but a range marked ₹/Rs/INR,
    K, LPA or lakh, the only pay a listing's free-text description is trusted for.
    """
    unstated = None
    for m in _PAY.finditer(text):
        if ranges_only and not (m["num_b"] and any(m[g] for g in _MARKS)):
            continue
        if _labelled_other(text, m.start()) or not (pay := _read(m)):
            continue
        if _explicit_period(m):
            return pay
        unstated = unstated or pay
    return unstated


def listing_pay(listing: dict[str, Any]) -> ListingPay | None:
    """One Google Jobs listing's pay: ``detected_extensions.salary``, then ``extensions[]``,
    then a ₹/K/LPA/lakh range in ``description``. A source that is missing, or carries no
    monthly or yearly figure, falls through to the next."""
    sources: list[tuple[PaySource, str | None]] = [
        ("detected_extensions.salary", listing.get("detected_extensions", {}).get("salary"))
    ]
    sources += [("extensions", text) for text in listing.get("extensions", [])]
    sources.append(("description", listing.get("description")))
    for source, text in sources:
        if text and (pay := parse_pay(text, ranges_only=source == "description")):
            return ListingPay(source, pay.raw, pay.monthly_inr)
    return None


def pay_benchmark(listings: list[dict[str, Any]]) -> PayBenchmark:
    """The comparable listings' pay. Every listing passed in counts as comparable: the caller
    passes the listings of a ``google_jobs`` search for the role that are near the city."""
    return PayBenchmark(
        listings=len(listings),
        pays=tuple(pay for pay in map(listing_pay, listings) if pay),
    )


def is_pay_outlier(offered_monthly_inr: float, benchmark: PayBenchmark) -> bool:
    """The ``pay_outlier`` condition: more than 2.0x the median of at least 3 listings' pay."""
    return (
        benchmark.enough
        and offered_monthly_inr > PAY_OUTLIER_FACTOR * benchmark.median_monthly_inr
    )
