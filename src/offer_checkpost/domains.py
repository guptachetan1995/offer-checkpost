"""Domain arithmetic for recruiter contacts: registrable domains, free mail, look-alikes.

Each domain function takes whatever the offer or a search result carried (a bare host, an
email address or a URL) and reduces it to a registrable domain before comparing, so
``careers.brand.example``, ``hr@careers.brand.example`` and
``https://careers.brand.example/jobs`` are all ``brand.example``.

The look-alike rule is ``sender_lookalike`` exactly: a domain that is not the official one
(or a subdomain of it) is a candidate when its label carries the brand token, or when its
label is within Damerau–Levenshtein distance 1 of the official label (official label 5–8
characters) or 2 (9 or more). Official labels of 4 characters or fewer get no fuzzy match,
so ``tvs.com`` is never a look-alike of ``tcs.com``. A candidate is only ever moderate
evidence; ``confirm_outcome`` reads the confirming ``site:`` search that either clears it
(an employer's second official domain, such as ``amazon.jobs``) or upgrades it.

A host written in Unicode is kept in its punycode (``xn--``) form, so ``brаnd.com`` with a
Cyrillic ``а`` is still a domain, and a look-alike of ``brand.com``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal
from urllib.parse import urlsplit

DomainClass = Literal["official", "subdomain", "free_mail", "lookalike", "unrelated"]
ConfirmRule = Literal["domain_named_in_fraud_notice", "sender_official", "sender_lookalike"]

# A subset of the Public Suffix List. Only multi-label suffixes are listed, because every
# single-label TLD is a public suffix by default. The free-hosting and site-builder entries
# are here so that a fake careers page on one is judged by its own label, not by the host's,
# and a small firm's own site on one never makes the whole host official.
SUFFIXES = frozenset(
    {
        "5g.in", "6g.in", "ac.in", "ai.in", "am.in", "bihar.in", "biz.in", "business.in",
        "ca.in", "cn.in", "co.in", "com.in", "coop.in", "cs.in", "delhi.in", "dr.in",
        "edu.in", "er.in", "firm.in", "gen.in", "gov.in", "gujarat.in", "ind.in", "info.in",
        "int.in", "internet.in", "io.in", "me.in", "mil.in", "net.in", "nic.in", "org.in",
        "pg.in", "post.in", "pro.in", "res.in", "travel.in", "tv.in", "uk.in", "up.in",
        "us.in", "blogspot.in",
        "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz",
        "com.sg", "edu.sg", "gov.sg", "com.my", "co.jp", "com.cn", "com.hk",
        "co.ae", "net.ae", "org.ae", "ac.ae", "gov.ae",
        "com.sa", "com.qa", "com.om", "com.kw", "com.bh",
        "com.pk", "com.bd", "com.np", "com.lk",
        "co.za", "com.br", "com.mx", "co.id", "com.ph",
        "github.io", "blogspot.com", "netlify.app", "vercel.app", "pages.dev",
        "web.app", "firebaseapp.com", "herokuapp.com", "appspot.com",
        "azurewebsites.net", "000webhostapp.com",
        "wixsite.com", "wordpress.com", "weebly.com", "godaddysites.com", "webflow.io",
        "mystrikingly.com", "jimdosite.com", "square.site", "carrd.co", "site123.me",
    }
)  # fmt: skip

FREE_MAIL = frozenset(
    {
        "gmail.com", "googlemail.com",
        "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com", "rocketmail.com",
        "rediffmail.com",
        "outlook.com", "hotmail.com", "live.com", "msn.com",
        "icloud.com", "me.com", "mac.com",
        "aol.com", "proton.me", "protonmail.com", "pm.me",
        "zohomail.com", "zohomail.in",
        "gmx.com", "mail.com", "yandex.com", "tutanota.com",
    }
)  # fmt: skip

FRAUD_TERMS = ("fraud", "fake", "beware", "scam", "not affiliated")

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_FRAUD = re.compile(r"\b(?:fraud|fake|beware|scam|not\s+affiliated)", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?;])\s+|\s*(?:\.\.\.|…)\s*|\n+")
# An employer's fraud notice often lists its genuine domains in the same sentence as its
# warning ("we recruit only through brand.com and brand.jobs, so beware of any other domain").
_VOUCH = re.compile(r"\b(?:only|genuine|official|legitimate|authori[sz]ed)\b", re.IGNORECASE)

# Cyrillic and Greek letters that render like a Latin one. Accented Latin letters need no
# entry: the skeleton drops their accents.
_CONFUSABLES = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
        "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "к": "k",
        "α": "a", "ε": "e", "ο": "o", "ρ": "p", "ι": "i", "κ": "k", "ν": "v", "υ": "u",
        "χ": "x", "ϲ": "c", "ɡ": "g",
    }
)  # fmt: skip


def host_of(value: str) -> str | None:
    """The lowercased host of a bare host, an email address or a URL, without ``www.``.

    None when there is no domain name in it: an IP address, a single label, or text that
    is not a host at all. A Unicode host comes back in its punycode form, the name DNS
    resolves.
    """
    text = value.strip().lower()
    if "://" not in text:
        text = "//" + text
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    if not host:
        return None
    try:
        host = host.rstrip(".").removeprefix("www.").encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = host.split(".")
    if len(labels) < 2 or labels[-1].isdigit():
        return None
    if not all(_LABEL.fullmatch(label) for label in labels):
        return None
    return host


def registrable_domain(value: str) -> str | None:
    """The public suffix plus one label, e.g. ``careers.brand.co.in`` → ``brand.co.in``.

    None when there is no host, or when the host is itself a public suffix (``co.in``).
    """
    host = host_of(value)
    if host is None:
        return None
    labels = host.split(".")
    suffix_len = next(
        (n for n in range(len(labels), 1, -1) if ".".join(labels[-n:]) in SUFFIXES), 1
    )
    if len(labels) <= suffix_len:
        return None
    return ".".join(labels[-suffix_len - 1 :])


def _label(domain: str) -> str:
    return domain.split(".", 1)[0]


def _unicode_label(domain: str) -> str:
    label = _label(domain)
    try:
        return label.encode("ascii").decode("idna")
    except UnicodeError:
        return label


def _skeleton(label: str) -> str:
    decomposed = unicodedata.normalize("NFKD", label)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).translate(_CONFUSABLES)


def is_official(value: str, official: str) -> bool:
    """True when ``value`` is on the official registrable domain or a subdomain of it."""
    domain = registrable_domain(value)
    return domain is not None and domain == registrable_domain(official)


def is_free_mail(value: str) -> bool:
    """True when ``value`` is on a free-mail provider's domain (a personal mailbox)."""
    return registrable_domain(value) in FREE_MAIL


def damerau_levenshtein(a: str, b: str) -> int:
    """Unrestricted Damerau–Levenshtein distance: insertions, deletions, substitutions and
    transpositions of adjacent characters, each costing 1.

    Unlike the restricted (optimal string alignment) variant, a transposed pair may be
    edited again, so ``ca`` → ``abc`` is 2, not 3.
    """
    worst = len(a) + len(b)
    d = [[worst] * (len(b) + 2) for _ in range(len(a) + 2)]
    for i in range(len(a) + 1):
        d[i + 1][1] = i
    for j in range(len(b) + 1):
        d[1][j + 1] = j
    last_row_of: dict[str, int] = {}
    for i in range(1, len(a) + 1):
        last_match_col = 0
        for j in range(1, len(b) + 1):
            k = last_row_of.get(b[j - 1], 0)
            m = last_match_col
            cost = 1
            if a[i - 1] == b[j - 1]:
                cost = 0
                last_match_col = j
            d[i + 1][j + 1] = min(
                d[i][j] + cost,
                d[i + 1][j] + 1,
                d[i][j + 1] + 1,
                d[k][m] + (i - k - 1) + 1 + (j - m - 1),
            )
        last_row_of[a[i - 1]] = i
    return d[len(a) + 1][len(b) + 1]


def lookalike_reason(value: str, official: str) -> str | None:
    """Why ``value`` is a look-alike candidate of the official domain, or None if it isn't.

    The brand token is the official label. A token of 4+ characters matches as a substring
    of the candidate's label; a shorter one only as a whole hyphen- or edge-delimited
    token, so ``tcs-careers`` matches ``tcs`` and ``tcsion`` does not. The fuzzy match
    compares whole labels and needs an official label of 5+ characters. Both labels are
    compared as confusables skeletons: a punycode label is decoded first, and ``brаnd``
    (with a Cyrillic ``а``) or ``bränd`` reads as ``brand``.
    """
    domain = registrable_domain(value)
    official_domain = registrable_domain(official)
    if domain is None or official_domain is None or domain == official_domain:
        return None
    shown, brand = _unicode_label(domain), _unicode_label(official_domain)
    label, token = _skeleton(shown), _skeleton(brand)
    if label == token and shown != brand:
        return f"'{shown}' is '{brand}' written with look-alike letters"
    if len(token) >= 4:
        if token in label:
            return f"'{shown}' contains the brand token '{brand}'"
    elif re.search(rf"(?:^|-){re.escape(token)}(?:-|$)", label):
        return f"'{shown}' contains the brand token '{brand}' as a separate word"
    if len(token) >= 5:
        distance = damerau_levenshtein(label, token)
        if distance <= (1 if len(token) <= 8 else 2):
            return f"'{shown}' is {distance} edit{'s' if distance > 1 else ''} from '{brand}'"
    return None


def classify_domain(value: str, official: str) -> DomainClass | None:
    """How a recruiter's email or link domain relates to the employer's official domain.

    ``official``: the registrable domain itself. ``subdomain``: a host under it. Both are
    ``sender_official``. ``free_mail``: a personal mailbox. ``lookalike``: a candidate for
    ``sender_lookalike``, never more than moderate until a confirming search. ``unrelated``:
    none of these. None when ``value`` holds no domain name.
    """
    host = host_of(value)
    domain = host and registrable_domain(host)
    if not domain:
        return None
    official_domain = registrable_domain(official)
    if domain == official_domain:
        return "official" if host == domain else "subdomain"
    if domain in FREE_MAIL:
        return "free_mail"
    if lookalike_reason(domain, official):
        return "lookalike"
    return "unrelated"


def _mentions(text: str, domain: str) -> bool:
    pattern = rf"(?<![a-z0-9-]){re.escape(domain)}(?![a-z0-9-]|\.[a-z0-9-])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _condemns(sentence: str, official: str) -> bool:
    fraud = _FRAUD.search(sentence)
    return (
        fraud is not None
        and not _mentions(sentence, official)
        and not _VOUCH.search(sentence, 0, fraud.start())
    )


def confirm_outcome(
    candidate: str, official: str, results: list[dict[str, Any]]
) -> tuple[ConfirmRule, dict[str, Any] | None]:
    """Reads a ``site:<official> "<candidate>"`` search's ``organic_results``.

    Only a result whose link is on the official domain counts: a third-party page is never
    the employer's statement. "Next to a fraud term" means in the same sentence of the
    result's title or snippet, a sentence that doesn't vouch for the domains it lists: one
    that also names the official domain, or says "only", "genuine", "official", "legitimate"
    or "authorised" before its first fraud term, is the employer listing its genuine domains.
    Returns the rule that now holds, with the result that decided it:

    - ``domain_named_in_fraud_notice`` when any such sentence names the candidate next to
      a fraud term (it wins over every other mention);
    - ``sender_lookalike`` (with None) when a result names the candidate and carries a
      fraud term only elsewhere in its title or snippet, or in a sentence that vouches. An
      employer's fraud page may list its genuine domains as well as the fake ones, so such a
      mention neither clears nor condemns the candidate: it stays moderate;
    - ``sender_official`` when the official site mentions the candidate only on results
      with no fraud term at all, as an employer's second official domain;
    - ``sender_lookalike`` (with None) when the official site never mentions it.
    """
    domain = registrable_domain(candidate)
    official_domain = registrable_domain(official)
    plain_mention = None
    on_a_fraud_page = False
    for result in results:
        if not is_official(result.get("link", ""), official):
            continue
        fields = [result.get("title") or "", result.get("snippet") or ""]
        sentences = [s for field in fields for s in _SENTENCE_BREAK.split(field)]
        mentions = [s for s in sentences if _mentions(s, domain)]
        if not mentions:
            continue
        if any(_condemns(s, official_domain) for s in mentions):
            return "domain_named_in_fraud_notice", result
        if any(_FRAUD.search(field) for field in fields):
            on_a_fraud_page = True
        elif plain_mention is None:
            plain_mention = result
    if plain_mention is not None and not on_a_fraud_page:
        return "sender_official", plain_mention
    return "sender_lookalike", None
