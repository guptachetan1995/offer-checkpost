"""Offer text in; checkable claims and text-rule hits out. No search, no network, no state.

Every claim carries ``span``, a ``[start, end]`` pair of Python string offsets (code points)
into the pasted text, so ``text[start:end]`` is exactly the words the claim came from. Claims
start unconfirmed: the person confirms or corrects them before anything is searched.

Reads English and romanised Hinglish ("registration ke liye ₹1,999 bhejo"). Money is read in
the Indian formats ₹ / Rs. / INR, ``38,000`` and ``1,20,000`` grouping, ``k``, thousand,
hazar, ``L``, lakh, ``LPA`` and crore, and pay is normalised to rupees a month.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from offer_checkpost.salary import LAKH, MIN_MONTHLY_INR, UNITS

TEXT_RULES = ("fee_requested", "task_scam_pattern", "sensitive_docs_early", "chat_only_interview")

Span = tuple[int, int]


def extract_claims(text: str) -> dict:
    """The claims object of a case: company, role, city, pay, fee, contacts and links.

    A claim that isn't in the text is ``None`` (contacts and links are empty lists)."""
    sentences = _sentences(text)
    emails = _emails(text)
    phones = _phones(text)
    links = _links(text, [c["span"] for c in emails])
    taken = [tuple(c["span"]) for c in emails + phones + links]
    money = _money(text)
    fee = _fee(text, sentences, money)
    return {
        "company": _company(text, taken),
        "role": _role(text),
        "city": _city(text, taken),
        "pay": _pay(text, money, fee),
        "fee": fee,
        "contacts": sorted(emails + phones, key=lambda c: c["span"][0]),
        "links": links,
    }


def text_rules(text: str) -> list[dict]:
    """The text rules that fire, in ``TEXT_RULES`` order, each quoting the sentence it fired on.

    Each hit is ``{"rule", "span", "quote"}``; direction and severity belong to the rule
    table, not to extraction."""
    sentences = _sentences(text)
    fee = _fee(text, sentences, _money(text))
    positions = {
        "fee_requested": fee["span"][0] if fee and fee["amountInr"] is not None else None,
        "task_scam_pattern": _first_sentence(sentences, lambda s, e: _task_scam(text[s:e])),
        "sensitive_docs_early": _first_sentence(
            sentences, lambda s, e: _asks_sensitive_docs(text, s, e)
        ),
        "chat_only_interview": None
        if _interview_not_in_chat(text, sentences)
        else _first_sentence(sentences, lambda s, e: _chat_only_interview(text[s:e])),
    }
    hits = []
    for rule in TEXT_RULES:
        pos = positions[rule]
        if pos is None:
            continue
        start, end = _sentence_at(sentences, pos)
        hits.append({"rule": rule, "span": [start, end], "quote": text[start:end]})
    return hits


# ---- sentences ----------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"\n|[!?]+|\.(?=\s|$)")
_ABBREVIATIONS = {"rs", "pvt", "ltd", "no", "co", "inc", "corp", "mr", "ms", "mrs", "dr", "st"}
_ABBREVIATIONS |= {"approx", "etc", "vs", "e.g", "i.e"}


def _sentences(text: str) -> list[Span]:
    spans = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        if m.group() == "." and _abbreviation_before(text, m.start()):
            continue
        spans.append(_trimmed(text, start, m.start() if m.group() == "\n" else m.end()))
        start = m.end()
    spans.append(_trimmed(text, start, len(text)))
    return [s for s in spans if s[0] < s[1]]


def _abbreviation_before(text: str, dot: int) -> bool:
    return re.search(r"[\w.]*$", text[max(0, dot - 12) : dot]).group().lower() in _ABBREVIATIONS


def _trimmed(text: str, start: int, end: int) -> Span:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _sentence_at(sentences: list[Span], pos: int) -> Span:
    return next(s for s in sentences if s[0] <= pos < s[1])


def _first_sentence(sentences: list[Span], test) -> int | None:
    return next((s for s, e in sentences if test(s, e)), None)


# Clauses of one sentence: "share your bank details; bring the originals on joining" asks for
# the bank details now, whatever the second clause says.
_CLAUSE_BREAK = re.compile(r";|,[ \t]*(?:and|but)\b|\bbut\b|\bhowever\b", re.I)


def _clauses(text: str, start: int, end: int) -> list[Span]:
    spans = []
    for m in _CLAUSE_BREAK.finditer(text, start, end):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, end))
    return spans


def _clause_at(text: str, sentence: Span, pos: int) -> Span:
    return next(c for c in _clauses(text, *sentence) if c[0] <= pos <= c[1])


# ---- contacts and links -------------------------------------------------------------------

# RFC 2606 / RFC 6761 names: nobody can register them, so nobody can be reached through them.
_RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost")
_RESERVED_DOMAINS = ("example.com", "example.net", "example.org")

_EMAIL = re.compile(
    r"(?<![\w.*+%-])[\w.%+*-]+@(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,}(?![\w-])"
)

# Indian mobiles are 10 digits starting 6-9. A masked number keeps its first digit and
# writes the rest as X (or *), which no dialler can reach.
_PHONE = re.compile(
    r"(?<![\w+])(?:(?:\(\s*\+?91\s*\)|\+?91)[ \t-]*|0)?"
    r"(?P<body>[6-9](?:[ .-]?[0-9Xx*]){9})(?![\w*])"
)

_LINK_TLDS = (
    "com|in|org|net|info|xyz|online|site|jobs|work|careers|example|test|invalid|io|ly|gle|me"
    "|app|co|biz|live|top|store|club|tech|link|ai|dev|cc|to|gl|gd|shop|vip|icu|cloud|pro"
)
_LINK = re.compile(
    r"\bhttps?://[^\s<>\"'`]+|\bwww\.[^\s<>\"'`]+"
    rf"|(?P<bare>\b(?:[^\W_](?:[\w-]*[^\W_])?\.)+(?P<tld>{_LINK_TLDS})\b)(?:/[^\s<>\"'`]*)?",
    re.I,
)
# Degrees and tech names written like hosts. Other bare hosts must read like one: a first label
# of 2+ characters, and a lower-case TLD unless the label is 4+ characters ("B.Tech", "B.Com",
# "ASP.NET" are not links; "QUICKJOBS.IN" is).
_NOT_HOSTS = {"asp.net", "vb.net", "ado.net"}


def _bare_host_is_a_link(host: str, tld: str) -> bool:
    first = host.split(".", 1)[0]
    return host not in _NOT_HOSTS and len(first) >= 2 and (tld.islower() or len(first) >= 4)


def _reserved_host(host: str) -> bool:
    return host.endswith(_RESERVED_TLDS) or any(
        host == d or host.endswith("." + d) for d in _RESERVED_DOMAINS
    )


def _email_end(text: str, m: re.Match) -> int:
    at = text.index("@", m.start())
    labels = text[at + 1 : m.end()].split(".")
    # A full stop typed with no space after it glues the next word on: "hr@brand.com.Thanks".
    for i in range(2, len(labels)):
        if labels[i][:1].isupper() and labels[i][1:].islower() and labels[i - 1].islower():
            return at + 1 + len(".".join(labels[:i]))
    return m.end()


def _emails(text: str) -> list[dict]:
    found, seen = [], set()
    for m in _EMAIL.finditer(text):
        end = _email_end(text, m)
        value = text[m.start() : end].lower()
        if value in seen:
            continue
        seen.add(value)
        local, domain = value.split("@")
        found.append(
            {
                "kind": "email",
                "value": value,
                "span": [m.start(), end],
                "synthetic": _reserved_host(domain) or "*" in local,
            }
        )
    return found


def _phones(text: str) -> list[dict]:
    found, seen = [], set()
    for m in _PHONE.finditer(text):
        digits = re.sub(r"[ .-]", "", m.group("body")).upper().replace("*", "X")
        value = f"+91 {digits[:5]} {digits[5:]}"
        if value in seen:
            continue
        seen.add(value)
        found.append(
            {
                "kind": "phone",
                "value": value,
                "span": [m.start(), m.end()],
                "synthetic": "X" in digits,
            }
        )
    return found


def _links(text: str, email_spans: list[list[int]]) -> list[dict]:
    found, seen = [], set()
    for m in _LINK.finditer(text):
        start, end = m.start(), m.end()
        while end > start and text[end - 1] in ".,;:!?)]}'\"*":
            end -= 1
        if any(s < end and start < e for s, e in email_spans):
            continue
        value = text[start:end]
        if value.lower() in seen:
            continue
        seen.add(value.lower())
        host = re.sub(r"^[a-z]+://", "", value.lower()).split("/")[0].split("?")[0]
        host = host.rsplit("@", 1)[-1].split(":")[0]
        if m.group("bare") and not _bare_host_is_a_link(host, m.group("tld")):
            continue
        found.append(
            {"value": value, "host": host, "span": [start, end], "synthetic": _reserved_host(host)}
        )
    return found


# ---- money --------------------------------------------------------------------------------

_MULT = "|".join(sorted(UNITS, key=len, reverse=True))
_AMOUNT = re.compile(
    r"(?:(?P<cur>₹|\brs\.?|\binr)[ \t]*|(?<![\w.,:]))"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)"
    rf"(?:[ \t]*(?P<mult>{_MULT})(?![a-z]))?"
    r"(?P<tail>[ \t]*/-|[ \t]*(?:rupees|rs|inr)\b)?",
    re.I,
)
_RANGE_JOIN = re.compile(r"[ \t]*(?:[-–—]|to|se)[ \t]*", re.I)
# An unmarked number this large is a phone number or an ID, never an amount.
_MAX_PLAIN_NUMBER = 10_000_000


@dataclass(frozen=True)
class _Money:
    start: int
    end: int
    amount: float
    unit: str
    # ₹/Rs/INR, a unit, "/-", "rupees" or Indian digit grouping. A plain number is money only
    # where a fee phrase or a pay period sits right next to it.
    marked: bool

    @property
    def lakh(self) -> bool:
        # Indian offers only ever quote lakh, LPA or crore for annual pay.
        return UNITS.get(self.unit, 1) >= LAKH


def _money(text: str) -> list[_Money]:
    """Amounts, with ranges ("₹25,000 - 35,000", "12k-15k", "15,000 se 20,000") merged into
    one item at their lower bound."""
    tokens = []
    for m in _AMOUNT.finditer(text):
        unit = (m.group("mult") or "").lower()
        marked = bool(m.group("cur") or unit or m.group("tail") or "," in m.group("num"))
        value = float(m.group("num").replace(",", "")) * UNITS.get(unit, 1)
        if marked or value < _MAX_PLAIN_NUMBER:
            tokens.append((m.start(), m.end(), value, unit, marked))

    items: list[_Money] = []
    i = 0
    while i < len(tokens):
        start, end, value, unit, marked = tokens[i]
        if i + 1 < len(tokens):
            n_start, n_end, high, n_unit, n_marked = tokens[i + 1]
            low = value * UNITS[n_unit] if n_unit and not unit else value
            # An order of magnitude apart is two separate numbers, not a range.
            if (
                _RANGE_JOIN.fullmatch(text, end, n_start)
                and (marked or n_marked)
                and low <= high <= low * 10
            ):
                items.append(_Money(start, n_end, low, unit or n_unit, True))
                i += 2
                continue
        items.append(_Money(start, end, value, unit, marked))
        i += 1
    return items


# ---- pay ----------------------------------------------------------------------------------

_PERIOD_AFTER = re.compile(
    r"(?:[ \t]+tak)?[ \t]*(?:"
    r"(?P<month>(?:/|per|a|every|each)[ \t]*(?:month|mnth|mo)\b|monthly\b|p\.?[ \t]?m\b\.?"
    r"|(?:per|har|prati)[ \t]+(?:mahina|mahine|maah)\b|mahina\b|mahine\b)"
    r"|(?P<year>(?:/|per|a|every)[ \t]*(?:annum|year|yr)\b|p\.?[ \t]?a\b\.?|yearly\b|annually\b"
    r"|(?:per|har)[ \t]+saal\b|salana\b)"
    r"|(?P<day>(?:/|per|a|every|each)[ \t]*(?:day|din)\b|daily\b|roz(?:ana)?\b"
    r"|(?:har|per)[ \t]+din\b)"
    r"|(?P<week>(?:/|per|a|every)[ \t]*(?:week|wk)\b|weekly\b|(?:har|per)[ \t]+hafte?\b))",
    re.I,
)
_PERIOD_BEFORE = re.compile(
    r"(?:(?P<day>\b(?:roz(?:ana)?|daily|per\s+day|har\s+din|every\s+day))"
    r"|(?P<month>\b(?:monthly|per\s+month|har\s+mahine|every\s+month))"
    r"|(?P<year>\b(?:yearly|annual(?:ly)?|per\s+annum)))"
    r"\W{0,3}(?:(?:salary|income|earnings?|pay|kamai|stipend|up\s*to|upto)\W{0,3})?$",
    re.I,
)
# "Pay" is pay only as a noun ("Pay: ₹16,000"); "Pay ₹999 to confirm" asks for money.
_PAY_CONTEXT = re.compile(
    r"\b(?:salary|stipend|package|ctc|income|earn\w*|kamao|kamai|kamaye|remuneration"
    r"|compensation|wages?|in[- ]hand|take[- ]home|payout)\b"
    r"|\b(?:net|gross|basic|total|fixed)[ \t]+pay\b|\bpay[ \t]*(?:[:=–-]|of\b|is\b|hai\b)"
    r"|\b(?:be|get|gets|getting|are|is|was)[ \t]+paid\b",
    re.I,
)
_PAY_LABEL_AFTER = re.compile(
    r"[ \t]*\(?[ \t]*(?:in[- ]hand|take[- ]home|salary|stipend|ctc|fixed)\b", re.I
)
# A figure labelled as a fee, an incentive or a target is not the offer's pay.
_NOT_PAY_LABEL = re.compile(
    r"\b(?:incentives?|allowances?|bonus(?:es)?|targets?|commissions?|reimburs\w*|rewards?"
    r"|insurance|fees?|(?<!\bin-)(?<!\bin )charges?|deposit|refund\w*|fine|penalty)\b",
    re.I,
)
_NOT_PAY_AFTER = re.compile(
    r"[ \t]*(?:incentives?|bonus(?:es)?|allowances?|commissions?|rewards?)\b", re.I
)
_PERIOD_WORD = r"(?:month|mnth|year|annum|day|week|mahin|saal|din)"
_TASK_UNITS = (
    r"(?:like|review|rating|task|video|order|click|post|comment|channel|subscri\w*|follow\w*)s?"
)
_PER_UNIT_AFTER = re.compile(
    r"[ \t]*(?:(?:for|on)[ \t]+)?"
    rf"(?:(?:per|each|every|har)[ \t]+(?:(?!{_PERIOD_WORD})[\w-]+[ \t]+){{0,2}}|(?:/|\ba\b)[ \t]*)"
    rf"{_TASK_UNITS}\b"
    rf"|[ \t]*each\b(?![ \t]+{_PERIOD_WORD})",
    re.I,
)
_PER_UNIT_BEFORE = re.compile(
    rf"(?:\b(?:per|har|each|every)\s+(?:[\w-]+\s+){{0,2}}{_TASK_UNITS}"
    rf"|\b{_TASK_UNITS}\s+(?:karne|dene|karo|do))\s*(?:pe|par|ke)?\s*$",
    re.I,
)
# Daily pay is promised for every day of the month, so it counts 30 times.
_TO_MONTHLY = {"month": 1.0, "year": 1 / 12, "day": 30.0, "week": 52 / 12}
_SENTENCE_BREAK = re.compile(r"[!?;]|\.(?=\s)")


def _line_before(text: str, pos: int, width: int) -> str:
    return text[max(0, pos - width) : pos].rsplit("\n", 1)[-1]


def _clause_before(text: str, pos: int, width: int) -> str:
    return _SENTENCE_BREAK.split(_line_before(text, pos, width))[-1]


def _per_unit(text: str, m: _Money) -> bool:
    return bool(
        _PER_UNIT_AFTER.match(text, m.end)
        or _PER_UNIT_BEFORE.search(_line_before(text, m.start, 30))
    )


def _pay_labelled(text: str, m: _Money) -> bool:
    return bool(
        _PAY_CONTEXT.search(_clause_before(text, m.start, 30))
        or _PAY_LABEL_AFTER.match(text, m.end)
    )


def _labelled_not_pay(text: str, m: _Money) -> bool:
    if _NOT_PAY_AFTER.match(text, m.end):
        return True
    window = _clause_before(text, m.start, 30)
    labels = [(x.end(), False) for x in _NOT_PAY_LABEL.finditer(window)]
    labels += [(x.end(), True) for x in _PAY_CONTEXT.finditer(window)]
    return bool(labels) and not max(labels)[1]


def _pay_claim(text: str, start: int, end: int, amount: float, period: str) -> dict | None:
    monthly = round(amount * _TO_MONTHLY[period])
    if monthly < MIN_MONTHLY_INR:
        return None
    return {
        "raw": text[start:end],
        "monthlyInr": monthly,
        "span": [start, end],
        "confirmed": False,
    }


def _pay_with_period(text: str, m: _Money) -> dict | None:
    after = _PERIOD_AFTER.match(text, m.end)
    if after:
        return _pay_claim(text, m.start, after.end(), m.amount, after.lastgroup)
    if not m.marked:
        return None
    if m.unit == "lpa":
        return _pay_claim(text, m.start, m.end, m.amount, "year")
    window = _line_before(text, m.start, 25)
    before = _PERIOD_BEFORE.search(window)
    if before:
        start = m.start - len(window) + before.start()
        return _pay_claim(text, start, m.end, m.amount, before.lastgroup)
    return None


def _pay(text: str, money: list[_Money], fee: dict | None) -> dict | None:
    """The first figure with a stated period, else the first one labelled as pay. A payment
    asked for, a per-task rate and an incentive are never the pay."""
    fee_start, fee_end = fee["span"] if fee else (0, 0)
    labelled = None
    for m in money:
        if (
            (m.start < fee_end and fee_start < m.end)
            or _per_unit(text, m)
            or _labelled_not_pay(text, m)
        ):
            continue
        if claim := _pay_with_period(text, m):
            return claim
        if labelled is None and m.marked and (m.lakh or _pay_labelled(text, m)):
            # Offers quote annual pay in lakh or as a six-figure CTC, and monthly pay otherwise.
            period = "year" if m.lakh or m.amount >= 100_000 else "month"
            labelled = _pay_claim(text, m.start, m.end, m.amount, period)
    return labelled


# ---- fee ----------------------------------------------------------------------------------

_FEE_KINDS = (
    r"registration|training|security|joining|processing|laptop|kit|onboarding|verification"
    r"|documentation|document"
)
_FEE_NOUNS = r"fees?|charges?|deposit|amount|money|payment"
# A person "In-charge" and training "free of charge" ask for nothing.
_FEE_PHRASE = re.compile(
    rf"(?P<strong>(?:\brefundable\s+)?\b(?:{_FEE_KINDS})\s+(?:{_FEE_NOUNS})\b"
    r"|\brefundable\s+(?:deposit|fees?|amount)\b)"
    rf"|(?P<weak>\b(?:{_FEE_KINDS})\s+ke\s+liye\b|\bfor\s+(?:the\s+)?(?:{_FEE_KINDS})\b"
    r"|\bdeposit\b|\bfees?\b|(?<!\bin-)(?<!\bin )(?<!\bfree of )\bcharges?\b)",
    re.I,
)
# "Pay ₹999 to confirm your seat": money asked for without naming a fee.
_PAY_DEMAND = re.compile(
    r"\b(?:pay|send|transfer)[ \t]+(?=(?:₹|rs\.?|inr)?[ \t]*\d[\d,.]*[ \t]*(?:/-|rupees\b)?"
    r"[ \t]+(?:to|for|as|now|today|immediately|within|before|via|through|using)\b)",
    re.I,
)
_NEGATION_FILLER = (
    rf"(?:any|a|an|the|such|extra|hidden|additional|other|or|and|{_FEE_KINDS}|charge[sd]?"
    r"|charging|ask(?:s|ed)?(?:\s+for)?|take|takes|collect|demand|require|need\s+to\s+pay"
    r"|need\s+to|to|pay|paying|candidates?|you|anyone|applicants?|students?)"
)
# The denial sits right before the fee phrase ("no fee", "never charge a joining fee",
# "koi registration fee"), not three arbitrary words back ("No interview, direct joining").
_NEGATED = re.compile(
    rf"\b(?:no|not|never|zero|without|koi|bina|don'?t|doesn'?t|won'?t)\s+"
    rf"(?:{_NEGATION_FILLER}[\s/]+){{0,4}}$",
    re.I,
)
_NEGATED_AFTER = re.compile(
    r"[ \t]*(?:(?:is|are|will[ \t]+be)[ \t]+)?(?:not[ \t]+(?:required|needed|applicable|charged"
    r"|payable)|nahi|nahin|waived|nil)\b",
    re.I,
)
# The employer paying, or paying the candidate back for a cost of their own, is no request.
_NOT_A_REQUEST = re.compile(
    r"\b(?:paid|reimbursed|borne|covered|sponsored)[ \t]+by[ \t]+(?:the[ \t]+)?"
    r"(?:company|us|employer|organi[sz]ation|management)\b"
    r"|\bwe[ \t]+(?:will[ \t]+)?(?:reimburse|pay|bear|cover|sponsor)\b"
    r"|\bcompany[ \t]+(?:will[ \t]+)?(?:reimburse|pay|bear|cover)s?\b|\bwaived\b",
    re.I,
)
_FEE_GAP = re.compile(r"[ \t]*(?:[:=–-]|of\b|is\b|hai\b)?[ \t]*", re.I)
_NOT_AN_AMOUNT_AFTER = re.compile(
    r"[ \t]*(?:%|days?\b|hours?\b|hrs?\b|weeks?\b|months?\b|years?\b|yrs?\b|times\b)", re.I
)


def _fee(text: str, sentences: list[Span], money: list[_Money]) -> dict | None:
    """The first fee phrase with an amount in its sentence, else the first explicit fee phrase
    ("registration fee") without one. A bare "fee" or "deposit", or "pay ₹X to …", counts only
    with an amount."""
    phrases = [(p.start(), p.end(), bool(p.group("strong"))) for p in _FEE_PHRASE.finditer(text)]
    phrases += [(p.start(), p.end(), False) for p in _PAY_DEMAND.finditer(text)]
    no_amount = None
    for start, end, strong in phrases:
        if _NEGATED.search(_line_before(text, start, 40)) or _NEGATED_AFTER.match(text, end):
            continue
        sentence = _sentence_at(sentences, start)
        if _NOT_A_REQUEST.search(text, *_clause_at(text, sentence, start)):
            continue
        amount = _fee_amount(text, start, end, strong, sentence, money)
        if amount:
            span_start, span_end = min(start, amount.start), max(end, amount.end)
            return {
                "raw": text[span_start:span_end],
                "amountInr": round(amount.amount),
                "span": [span_start, span_end],
                "confirmed": False,
            }
        if strong and no_amount is None:
            no_amount = {
                "raw": text[start:end],
                "amountInr": None,
                "span": [start, end],
                "confirmed": False,
            }
    return no_amount


def _fee_amount(
    text: str, start: int, end: int, strong: bool, sentence: Span, money: list[_Money]
) -> _Money | None:
    def one_off(m: _Money) -> bool:
        return not (
            _PERIOD_AFTER.match(text, m.end) or _per_unit(text, m) or _pay_labelled(text, m)
        )

    def plain_right_after(m: _Money) -> bool:
        # "Registration fee 2000 only": a plain number counts right after a named fee.
        return (
            strong
            and bool(_FEE_GAP.fullmatch(text, end, m.start))
            and m.amount >= 100
            and not _NOT_AN_AMOUNT_AFTER.match(text, m.end)
        )

    after = [
        m
        for m in money
        if end <= m.start <= min(end + 40, sentence[1])
        and (m.marked or plain_right_after(m))
        and one_off(m)
    ]
    if after:
        return after[0]
    before = [
        m for m in money if max(start - 30, sentence[0]) <= m.start and m.end <= start and m.marked
    ]
    return before[-1] if before and one_off(before[-1]) else None


# ---- company ------------------------------------------------------------------------------

_NAME_TOKEN = re.compile(r"[A-Z][\w&'-]*\.?|&")
_NAME_CONNECTOR = re.compile(r"(?:of|and)[ \t]+")
_NAME_GAP = re.compile(r"[ \t]+")
_NAME_ABBREVIATIONS = {"pvt", "co", "bros", "intl"}
_TERMINAL_SUFFIXES = {"ltd", "limited", "llp", "inc", "llc", "pte", "corp", "corporation"}
_TEAM_WORDS = {"hr", "team", "recruitment", "recruiting", "recruiter", "careers", "career"}
_TEAM_WORDS |= {"hiring", "talent", "acquisition", "onboarding", "department", "dept", "desk"}
_GREETINGS = {"dear", "hi", "hello", "hey", "namaste", "greetings", "congratulations", "congrats"}
_NOT_COMPANY = _GREETINGS | {"candidate", "applicant", "sir", "madam", "we", "you", "our"}
_NOT_COMPANY |= {"your", "the", "this", "india", "urgent", "whatsapp", "telegram", "youtube"}
_NOT_COMPANY |= {"instagram", "facebook", "gmail", "upi", "paytm", "phonepe", "google pay"}
_LEGAL_SUFFIXES = _TERMINAL_SUFFIXES | {"pvt", "private", "technologies", "solutions"}
_LEGAL_SUFFIXES |= {"services", "infotech", "consultancy", "consultants", "enterprises"}
_LEGAL_SUFFIXES |= {"industries", "systems"}

_COMPANY_LABEL = re.compile(
    r"^[ \t*_]*(?:company(?:[ \t]+name)?|organi[sz]ation|employer|client|firm)[ \t*_]*[:–-]"
    r"[ \t*_]*(?P<v>[^\n,;|(]+)",
    re.I | re.M,
)
_COMPANY_PREFIXES = (
    re.compile(r"\b(?:greetings|namaste|hello|hi)[ \t]+from[ \t]+", re.I),
    re.compile(r"\bon[ \t]+behalf[ \t]+of[ \t]+", re.I),
)
_COMPANY_SUFFIX = re.compile(
    r"[ \t]+(?:is[ \t]+(?:hiring|looking[ \t]+for|recruiting)\b|has[ \t]+(?:shortlisted|selected)"
    r"\b|invites\b|(?:company[ \t]+)?mein\b|ki[ \t]+(?:taraf|or)[ \t]+se\b|ko[ \t]+chahiye\b)",
    re.I,
)
_JOB_WORD = re.compile(
    r"\b(?:job|role|position|post|opening|vacancy|opportunity|career|internship|work|offer"
    r"|applying|applied|apply|selected|shortlisted|hiring|drive|walk-in)\b",
    re.I,
)
_EMPLOYER_PREPOSITION = re.compile(r"\b(?:at|with|to|join|joining)[ \t]+", re.I)


@dataclass(frozen=True)
class _Name:
    start: int
    end: int
    trimmed_team_word: bool  # "Brand HR Onboarding Team" came back as "Brand"


def _name_at(text: str, pos: int) -> _Name | None:
    """The capitalised name starting at ``pos`` on this line, with greetings and trailing team
    words ("HR", "Recruitment Team") trimmed off."""
    tokens: list[Span] = []
    i = pos
    while len(tokens) < 7:
        m = _NAME_TOKEN.match(text, i)
        if not m:
            c = _NAME_CONNECTOR.match(text, i) if tokens else None
            if c and _NAME_TOKEN.match(text, c.end()):
                i = c.end()
                continue
            break
        word = m.group().rstrip(".").lower()
        if m.group().endswith(".") and word not in _NAME_ABBREVIATIONS | _TERMINAL_SUFFIXES:
            tokens.append((m.start(), m.end() - 1))
            break
        tokens.append((m.start(), m.end()))
        gap = _NAME_GAP.match(text, m.end())
        if word in _TERMINAL_SUFFIXES or not gap:
            break
        i = gap.end()

    def word(t: Span) -> str:
        return text[t[0] : t[1]].rstrip(".").lower()

    while tokens and word(tokens[0]) in _GREETINGS:
        tokens.pop(0)
    trimmed = False
    while tokens and (word(tokens[-1]) in _TEAM_WORDS or word(tokens[-1]) == "&"):
        tokens.pop()
        trimmed = True
    if not tokens:
        return None
    start, end = tokens[0][0], tokens[-1][1]
    if text[end - 1] == ".":
        end -= 1
    value = text[start:end].lower()
    if value in _NOT_COMPANY or _CITY.fullmatch(value):
        return None
    return _Name(start, end, trimmed)


def _name_runs(text: str) -> list[_Name]:
    runs, covered = [], -1
    for m in re.finditer(r"(?<![\w&'.-])[A-Z]", text):
        if m.start() < covered:
            continue
        name = _name_at(text, m.start())
        if name:
            runs.append(name)
            covered = name.end
    return runs


def _company(text: str, taken: list[Span]) -> dict | None:
    def claim(start: int, end: int) -> dict:
        return {"value": text[start:end], "span": [start, end], "confirmed": False}

    def free(name: _Name) -> bool:
        return not any(s < name.end and name.start < e for s, e in taken)

    for m in _COMPANY_LABEL.finditer(text):
        start, end = _trimmed(text, m.start("v"), m.end("v"))
        while end > start and text[end - 1] in "*_.":
            end -= 1
        if start < end:
            return claim(start, end)

    for prefix in _COMPANY_PREFIXES:
        for m in prefix.finditer(text):
            name = _name_at(text, m.end())
            if name and free(name):
                return claim(name.start, name.end)

    runs = [r for r in _name_runs(text) if free(r)]
    for name in runs:
        if _COMPANY_SUFFIX.match(text, name.end):
            return claim(name.start, name.end)

    for m in _EMPLOYER_PREPOSITION.finditer(text):
        before = _line_before(text, m.start(), 45)
        if "." in before.rstrip() or not _JOB_WORD.search(before):
            continue
        name = _name_at(text, m.end())
        if name and free(name):
            return claim(name.start, name.end)

    for name in runs:
        words = text[name.start : name.end].split()
        if len(words) > 1 and words[-1].rstrip(".").lower() in _LEGAL_SUFFIXES:
            return claim(name.start, name.end)

    for name in runs:
        if name.trimmed_team_word:
            return claim(name.start, name.end)
    return None


# ---- role ---------------------------------------------------------------------------------

_ROLE_LABEL = re.compile(
    r"^[ \t*_]*(?:job[ \t]+)?(?:role|position|post|designation|profile|title|vacancy|opening"
    r"|requirement)[ \t*_]*[:–-][ \t*_]*(?P<v>[^\n;|]+)",
    re.I | re.M,
)
_ROLE_OF = re.compile(r"\b(?:post|position|role|profile)[ \t]+of[ \t]+(?:an?[ \t]+)?", re.I)
_ROLE_FOR = re.compile(r"\bfor[ \t]+(?:the[ \t]+|an?[ \t]+)?", re.I)
_ROLE_HIRING = re.compile(
    r"\b(?:hiring|recruiting|chahiye|requirement[ \t]+for|vacancy[ \t]+for|openings?[ \t]+for)"
    r"(?:[ \t]+for)?(?:[ \t]+(?:the|an?))?[ \t]+",
    re.I,
)
_ROLE_AS = re.compile(r"\b(?:job|work|kaam|joining|join)[ \t]+as[ \t]+(?:an?[ \t]+)?", re.I)
_ROLE_TOKEN = re.compile(r"[A-Za-z][\w/&+'-]*|&")
_WORK_MODE = re.compile(
    r"[ \t]*\((?:wfh|work[ \t]+from[ \t]+home|remote|part[- ]time|full[- ]time|hybrid"
    r"|night[ \t]+shift|voice|non[- ]voice)\)",
    re.I,
)
# "Hiring for Work From Home Customer Service Associates": the work mode leads the role.
_LEADING_MODE = re.compile(
    r"(?:wfh|work[ \t]+from[ \t]+home|remote|part[- ]time|full[- ]time|hybrid"
    r"|night[ \t]+shift)[ \t]+",
    re.I,
)
_ROLE_NOUNS = {"role", "position", "post", "profile", "job", "jobs", "vacancy", "vacancies"}
_ROLE_NOUNS |= {"opening", "openings"}
_ROLE_STOP = _ROLE_NOUNS | {"at", "in", "with", "from", "for", "on", "to", "near", "based"}
_ROLE_STOP |= {"who", "which", "that", "is", "are", "will", "ke", "ki", "ka", "se", "mein"}
_ROLE_STOP |= {"par", "pe", "per", "now", "immediately", "urgently", "and", "or", "our"}
_ROLE_STOP |= {"your", "its", "their", "the", "a", "an"}
_NOT_ROLE = {"you", "candidates", "candidate", "freshers", "fresher", "people", "staff"}
_NOT_ROLE |= {"students", "boys", "girls", "interns", "applicants"}


def _role_at(text: str, pos: int, needs_role_noun: bool) -> Span | None:
    lead = _LEADING_MODE.match(text, pos)
    start = pos
    words = end = i = lead.end() if lead else pos
    count = 0
    stopped_on = None
    while count < 6:
        m = _ROLE_TOKEN.match(text, i)
        if not m:
            break
        if m.group().lower() in _ROLE_STOP:
            stopped_on = m.group().lower()
            break
        end = m.end()
        count += 1
        gap = _NAME_GAP.match(text, end)
        if not gap:
            break
        i = gap.end()
    if not count:
        return None
    mode = _WORK_MODE.match(text, end)
    if needs_role_noun and stopped_on not in _ROLE_NOUNS:
        gap = _NAME_GAP.match(text, mode.end() if mode else end)
        after = _ROLE_TOKEN.match(text, gap.end()) if gap else None
        if not (after and after.group().lower() in _ROLE_NOUNS):
            return None
    if mode:
        end = mode.end()
    if text[words:end].lower() in _NOT_ROLE:
        return None
    return start, end


def _role(text: str) -> dict | None:
    def claim(span: Span) -> dict:
        return {"value": text[span[0] : span[1]], "span": list(span), "confirmed": False}

    for m in _ROLE_LABEL.finditer(text):
        start, end = _trimmed(text, m.start("v"), m.end("v"))
        while end > start and text[end - 1] in "*_.,":
            end -= 1
        if start < end:
            return claim((start, end))
    for pattern, needs_role_noun in (
        (_ROLE_OF, False),
        (_ROLE_FOR, True),
        (_ROLE_HIRING, False),
        (_ROLE_AS, False),
    ):
        for m in pattern.finditer(text):
            span = _role_at(text, m.end(), needs_role_noun)
            if span:
                return claim(span)
    return None


# ---- city ---------------------------------------------------------------------------------

_CITY_NAMES = """
Bengaluru, Mumbai, Navi Mumbai, Thane, Delhi, Gurugram, Noida, Greater Noida, Ghaziabad,
Faridabad, Pune, Hyderabad, Secunderabad, Chennai, Kolkata, Ahmedabad, Gandhinagar, Surat,
Vadodara, Rajkot, Jaipur, Jodhpur, Udaipur, Kota, Indore, Bhopal, Gwalior, Jabalpur, Lucknow,
Kanpur, Agra, Varanasi, Prayagraj, Meerut, Bareilly, Aligarh, Gorakhpur, Patna, Ranchi,
Jamshedpur, Dhanbad, Bhubaneswar, Cuttack, Raipur, Nagpur, Nashik, Aurangabad, Kolhapur,
Chandigarh, Mohali, Ludhiana, Amritsar, Jalandhar, Dehradun, Shimla, Jammu, Srinagar, Guwahati,
Siliguri, Durgapur, Kochi, Thiruvananthapuram, Kozhikode, Thrissur, Coimbatore, Madurai,
Tiruchirappalli, Salem, Mysuru, Mangaluru, Hubballi, Belagavi, Visakhapatnam, Vijayawada, Guntur,
Warangal, Panaji, Puducherry
"""
_OLD_CITY_NAMES = {
    "bangalore": "Bengaluru",
    "bombay": "Mumbai",
    "new delhi": "Delhi",
    "gurgaon": "Gurugram",
    "madras": "Chennai",
    "calcutta": "Kolkata",
    "baroda": "Vadodara",
    "allahabad": "Prayagraj",
    "cochin": "Kochi",
    "trivandrum": "Thiruvananthapuram",
    "calicut": "Kozhikode",
    "trichy": "Tiruchirappalli",
    "mysore": "Mysuru",
    "mangalore": "Mangaluru",
    "hubli": "Hubballi",
    "belgaum": "Belagavi",
    "vizag": "Visakhapatnam",
    "pondicherry": "Puducherry",
}
_CITY_BY_ALIAS = {c.strip().lower(): c.strip() for c in _CITY_NAMES.split(",")} | _OLD_CITY_NAMES
_CITY = re.compile(
    r"\b(?:"
    + "|".join(re.escape(a).replace(r"\ ", r"\s+") for a in sorted(_CITY_BY_ALIAS, key=len)[::-1])
    + r")\b",
    re.I | re.A,
)
_LOCATION_LABEL = re.compile(
    r"^[ \t*_]*(?:(?:job|work|office)[ \t]+)?(?:location|city|venue|place[ \t]+of[ \t]+posting"
    r"|posting|office|address)[ \t*_]*[:–-]",
    re.I | re.M,
)


def _city(text: str, taken: list[Span]) -> dict | None:
    labelled = [(m.start(), text.find("\n", m.start())) for m in _LOCATION_LABEL.finditer(text)]
    hits = [
        m for m in _CITY.finditer(text) if not any(s < m.end() and m.start() < e for s, e in taken)
    ]
    if not hits:
        return None
    on_label = [
        m for m in hits if any(s <= m.start() and (e < 0 or m.start() < e) for s, e in labelled)
    ]
    m = (on_label or hits)[0]
    canonical = _CITY_BY_ALIAS[re.sub(r"\s+", " ", m.group().lower())]
    return {"value": canonical, "span": [m.start(), m.end()], "confirmed": False}


# ---- text rules ---------------------------------------------------------------------------

_MONEY = (
    r"(?:(?:₹|\brs\.?|\binr)[ \t]*\d[\d,]*(?:\.\d+)?(?:[ \t]*k\b)?"
    r"|\b\d[\d,]*[ \t]*(?:/-|rupees\b))"
)
_SCAM_UNIT = r"(?:like|review|rating|subscri(?:be|ber|ption)|follow(?:er)?)s?"
# Liking videos or subscribing to channels makes the video or the channel the unit too.
_SCAM_UNIT_LIKING = r"(?:like|review|rating|subscri(?:be|ber|ption)|follow(?:er)?|video|channel)s?"
_LIKING = re.compile(r"\blik(?:e|es|ed|ing)\b|\bsubscrib\w*", re.I)
_QUALIFIER = r"(?:[\w-]+[ \t]+){0,2}"
_PER = rf"[ \t]*(?:(?:for|on)[ \t]+)?(?:(?:per|each|every|har)[ \t]+{_QUALIFIER}|/[ \t]*)"


def _per_unit_pay(unit: str) -> tuple[re.Pattern, ...]:
    # Money sits next to the unit: "₹50 per like", "₹40/review", "₹150 for every 5-star
    # review", "paid for each review", "har review pe ₹150". "After every performance
    # review" names no money, so it isn't one.
    return (
        re.compile(rf"(?:{_MONEY}|\bpaid\b){_PER}{unit}\b", re.I),
        re.compile(
            rf"\b(?:per|each|every|har)[ \t]+{_QUALIFIER}{unit}[ \t]*(?:pe|par|ke|:|=|-)?[ \t]*"
            rf"{_MONEY}",
            re.I,
        ),
    )


_PER_SCAM_UNIT = _per_unit_pay(_SCAM_UNIT)
_PER_SCAM_UNIT_LIKING = _per_unit_pay(_SCAM_UNIT_LIKING)
_TASK_SCAM = (
    re.compile(
        rf"{_MONEY}[ \t]+(?:for[ \t]+)?(?:each|every|har)[ \t]+(?:[\w-]+[ \t]+){{0,3}}you[ \t]+"
        r"(?:like|review|rate|subscribe|follow)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:lik(?:e|es|ing)|reviews?|rat(?:e|ing)|subscrib\w*|follow\w*)\b[^\n]{0,60}?"
        rf"{_MONEY}[ \t]*each\b",
        re.I,
    ),
    re.compile(r"\bprepaid[ \t]+(?:[\w-]+[ \t]+)?tasks?\b", re.I),
    # A deposit asked of the reader: "deposit ₹500 to unlock tasks", not "investment research".
    re.compile(
        rf"\b(?:deposit|recharge|invest)[ \t]+(?:of[ \t]+)?{_MONEY}[^\n]{{0,40}}\btasks?\b", re.I
    ),
    re.compile(
        rf"\btasks?\b[^\n]{{0,40}}\b(?:deposit|recharge|invest)[ \t]+(?:of[ \t]+)?{_MONEY}", re.I
    ),
)
_HINGLISH_TASK = re.compile(
    r"\b(?:like|review|rating|subscribe)s?[ \t]+(?:karne|dene|karo|karke|do)\b", re.I
)
_MONEY_WORD = re.compile(rf"{_MONEY}|\b(?:milenge|milega|paise|kamao|kamai)\b", re.I)
_DAILY_EARNING = re.compile(
    r"\b(?:earn\w*|kamao|kamaye|kamai|income)\b[^\n]{0,40}?"
    r"\b(?:daily|per[ \t]+day|a[ \t]+day|every[ \t]+day|roz(?:ana)?|har[ \t]+din)\b"
    r"|\b(?:daily|roz(?:ana)?|har[ \t]+din)\b[^\n]{0,40}?\b(?:earn\w*|kamao|kamaye)\b",
    re.I,
)
# "Online" alone is not a task: a delivery job can say "earn ₹1,000 a day, apply online".
_SIMPLE_TASK = re.compile(
    r"\b(?:tasks?|kaam|lik(?:e|es|ing)|reviews?|rat(?:e|es|ing)|subscrib\w*)\b", re.I
)

_SENSITIVE_DOC = re.compile(
    r"\bPAN\b|(?i:\ba{1,2}dha{1,2}r\b|\bpan[ \t]*(?:card|number|no)\b"
    r"|\bbank[ \t]+(?:details|account|a/c|statement|passbook)|\bpassbook\b"
    r"|\bcancell?ed[ \t]+cheque\b|\bifsc\b|\botp\b|\bupi[ \t]+pin\b"
    r"|\b(?:debit|atm|credit)[ \t]+card\b|\bcvv\b)"
)
_DOC_REQUEST = re.compile(
    r"\b(?:send|share|submit|upload|provide|forward|email|mail|give|bhej\w*|dijiye|de[ \t]+do"
    r"|photo|scan|copy|required|chahiye)\b",
    re.I,
)
_DOC_LIST = ("required", "chahiye")
# Carrying documents to an in-person interview, or handing them over on joining, is normal.
_DOCS_IN_PERSON = re.compile(
    r"\b(?:carry|bring|laana|lana|lekar|original)\b"
    r"|\bafter[ \t]+(?:the[ \t]+)?(?:interview|selection|joining)\b"
    r"|\bon[ \t]+(?:the[ \t]+)?(?:joining|first)[ \t]+day\b"
    r"|\bat[ \t]+the[ \t]+(?:time[ \t]+of[ \t]+joining|venue|interview|office)\b",
    re.I,
)
_BEFORE_INTERVIEW = re.compile(r"\bbefore[ \t]+(?:the[ \t]+|any[ \t]+|your[ \t]+)?interview", re.I)
# A "Documents required" list after a walk-in or venue line is for showing in person.
_IN_PERSON_EVENT = re.compile(
    r"\bwalk[- ]?ins?\b|\bvenue\b|\bin[- ]person\b|\bface[- ]to[- ]face\b"
    r"|\binterview\w*[ \t]+(?:is[ \t]+|will[ \t]+be[ \t]+)?(?:at|in)[ \t]+(?:our|the)\b",
    re.I,
)

_INTERVIEW = re.compile(r"\binterview\w*", re.I)
_CHAT_CHANNEL = re.compile(
    r"\b(?:whatsapp|telegram)\b|\bchat[ \t]+interview"
    r"|\binterview\w*[ \t]+(?:on|over|via)[ \t]+chat\b",
    re.I,
)
_NOT_CHAT = re.compile(
    r"\bvideo\b|\bin[- ]person\b|\bwalk[- ]in\b|\boffice\b|\bvenue\b|\bface[- ]to[- ]face\b"
    r"|\bzoom\b|\bgoogle[ \t]+meet\b|\bphone[ \t]+call\b",
    re.I,
)
_NOT_CHAT_ANYWHERE = re.compile(
    r"\bwalk[- ]?ins?\b|\bvenue\b|\bin[- ]person\b|\bface[- ]to[- ]face\b|\bvideo[ \t]+call\b"
    r"|\bzoom\b|\bgoogle[ \t]+meet\b",
    re.I,
)
# WhatsApp carrying the interview's logistics is not an interview held in chat.
_CHAT_LOGISTICS = re.compile(
    r"\b(?:details|schedule[sd]?|slots?|call[ \t]+letter|timings?|venue|address|location)\b"
    r"|\b(?:shared|sent)[ \t]+(?:on|via|over|through)\b",
    re.I,
)


def _task_scam(sentence: str) -> bool:
    per_unit = _PER_SCAM_UNIT_LIKING if _LIKING.search(sentence) else _PER_SCAM_UNIT
    return bool(
        any(p.search(sentence) for p in per_unit + _TASK_SCAM)
        or (_HINGLISH_TASK.search(sentence) and _MONEY_WORD.search(sentence))
        or (_DAILY_EARNING.search(sentence) and _SIMPLE_TASK.search(sentence))
    )


def _asks_sensitive_docs(text: str, start: int, end: int) -> bool:
    listed_for_a_visit = bool(_IN_PERSON_EVENT.search(text, 0, start))
    for c_start, c_end in _clauses(text, start, end):
        clause = text[c_start:c_end]
        if not _SENSITIVE_DOC.search(clause):
            continue
        requests = [m.group().lower() for m in _DOC_REQUEST.finditer(clause)]
        if listed_for_a_visit:
            requests = [r for r in requests if r not in _DOC_LIST]
        if requests and (_BEFORE_INTERVIEW.search(clause) or not _DOCS_IN_PERSON.search(clause)):
            return True
    return False


def _interview_not_in_chat(text: str, sentences: list[Span]) -> bool:
    return bool(_NOT_CHAT_ANYWHERE.search(text)) or any(
        _INTERVIEW.search(text, s, e) and _NOT_CHAT.search(text, s, e) for s, e in sentences
    )


def _chat_only_interview(sentence: str) -> bool:
    return bool(
        _INTERVIEW.search(sentence)
        and _CHAT_CHANNEL.search(sentence)
        and not _NOT_CHAT.search(sentence)
        and not _CHAT_LOGISTICS.search(sentence)
    )
