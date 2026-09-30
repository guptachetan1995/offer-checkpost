"""One params builder and one reader per check: SerpApi engine JSON in, signals out.

Each ``*_params`` builder returns the full params of one search: the engine, the query, the
India parameters that engine accepts, and a ``json_restrictor`` so SerpApi sends back only the
fields the reader uses. No builder ever adds an ``api_key``; the provider adds it to a copy.

Each ``read_*`` takes that search's trimmed response and returns a ``Reading``: the signals it
fires, the facts the next check needs (the official domain, a ``place_id``, the pay benchmark)
and a one-line note for the trace. Every signal is built by ``rules.signal_for``, so its
direction, severity and source are the rule table's; its ``id`` is None until the case numbers
it. On top of the table's fields a signal carries ``detail`` (one line on what was found),
on the sender rules ``domain``, the registrable domain a replacement matches on, and on a
confirming ``sender_official`` that must not count as green, ``setAside`` (why). Its
``evidence`` is::

    {"engine", "query", "title", "link", "snippet", "date", "retrievedAt"}

quoting the result the rule fired on, as the engine returned it. A rule that fires on an
absence (``no_web_footprint``, ``no_listing_match``, ``office_not_found``) has no result to
quote, so its evidence names the search alone.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from offer_checkpost.domains import (
    FRAUD_TERMS,
    classify_domain,
    confirm_outcome,
    is_official,
    lookalike_reason,
    registrable_domain,
)
from offer_checkpost.extract import (
    _CITY,
    _CITY_BY_ALIAS,
    _LEGAL_SUFFIXES,
    _TERMINAL_SUFFIXES,
    _sentences,
)
from offer_checkpost.rules import Signal, signal_for
from offer_checkpost.salary import is_pay_outlier, pay_benchmark

GOOGLE_INDIA = {"gl": "in", "google_domain": "google.co.in", "hl": "en"}
# City-wide: at 1280 px a zoom-12 viewport spans about 50 km.
MAPS_ZOOM = "12"

RESTRICTORS = {
    "google": "knowledge_graph.{title,website},organic_results[].{title,link,snippet}",
    "google_jobs": (
        "jobs_results[].{title,company_name,location,detected_extensions.salary,extensions,"
        "description,apply_options[].link}"
    ),
    "google_maps": (
        "local_results[].{title,place_id,type,address,rating,reviews},"
        "place_results.{title,place_id,type,address,rating,reviews}"
    ),
    "google_news": "news_results[].{title,link,source.name,date}",
    "google_maps_reviews": "reviews[].{snippet,rating,iso_date,link}",
}

# Each OR group stays parenthesised so ``site:`` still scopes the whole query. ``never`` plus a
# fee word put an employer's own "we never ask for any payment" notice first in the 30 Sep 2026
# probes: the best of five phrasings tried on four employers. It is also narrow enough that
# Google sometimes drops ``site:`` for it and answers with generic fraud pages (the film take
# that afternoon), so the planner retries once with the notice's usual titles.
FRAUD_NOTICE_WORDINGS = {
    "specific": "(recruitment OR hiring) (fraud OR scam OR fake) never (fee OR money OR payment)",
    "broad": '("recruitment fraud" OR "fake job offers" OR "recruitment scams" OR "fraudulent")',
}
FRAUD_NOTICE_TERMS = FRAUD_NOTICE_WORDINGS["specific"]
SCAM_REPORT_TERMS = "job offer (fake OR scam OR fraud)"
CONTACT_REPORT_TERMS = "(scam OR fraud OR fake)"


@dataclass(frozen=True)
class Reading:
    signals: tuple[Signal, ...]
    facts: dict[str, Any]
    note: str


# ---- params builders ----------------------------------------------------------------------


def _location(city: str | None) -> str:
    return f"{city}, India" if city else "India"


def _google(q: str, city: str | None) -> dict[str, str]:
    return {
        "engine": "google",
        "q": q,
        **GOOGLE_INDIA,
        "location": _location(city),
        "json_restrictor": RESTRICTORS["google"],
    }


def lookup_official_site_params(company: str, city: str | None = None) -> dict[str, str]:
    return _google(search_name(company), city)


def find_fraud_notice_params(
    official_domain: str, city: str | None = None, wording: str = "specific"
) -> dict[str, str]:
    return _google(f"site:{official_domain} {FRAUD_NOTICE_WORDINGS[wording]}", city)


def confirm_sender_domain_params(
    official_domain: str, candidate: str, city: str | None = None
) -> dict[str, str]:
    return _google(f'site:{official_domain} "{registrable_domain(candidate)}"', city)


def check_contact_footprint_params(contact: dict, city: str | None = None) -> dict[str, str]:
    return _google(f'"{_contact_phrase(contact)}" {CONTACT_REPORT_TERMS}', city)


def check_job_listings_params(
    role: str, city: str | None, company: str | None = None
) -> dict[str, str]:
    """``company`` narrows the search to the employer's own listing. Leave it out for a firm
    with no official domain: no listing of it could carry an official apply link, and the
    role alone brings back the city's comparable listings for the pay benchmark."""
    q = _plain_role(role)
    if company:
        q = f"{q} {search_name(company)}"
    return {
        "engine": "google_jobs",
        "q": q,
        **GOOGLE_INDIA,
        "location": _location(city),
        "json_restrictor": RESTRICTORS["google_jobs"],
    }


def check_office_params(company: str, city: str) -> dict[str, str]:
    return {
        "engine": "google_maps",
        "type": "search",
        "q": search_name(company),
        **GOOGLE_INDIA,
        "location": _location(city),
        "z": MAPS_ZOOM,
        "json_restrictor": RESTRICTORS["google_maps"],
    }


def scan_office_reviews_params(place_id: str, term: str) -> dict[str, str]:
    return {
        "engine": "google_maps_reviews",
        "place_id": place_id,
        "hl": "en",
        "query": term,
        "json_restrictor": RESTRICTORS["google_maps_reviews"],
    }


def check_scam_reports_params(company: str) -> dict[str, str]:
    return {
        "engine": "google_news",
        "q": f'"{search_name(company)}" {SCAM_REPORT_TERMS}',
        "gl": "in",
        "hl": "en",
        "json_restrictor": RESTRICTORS["google_news"],
    }


# ---- names, roles, cities -----------------------------------------------------------------

_LEGAL_TAIL = _TERMINAL_SUFFIXES | {"pvt", "private"}


def search_name(company: str) -> str:
    """The company as a search engine or a Maps listing writes it: trailing legal words
    (``Pvt. Ltd``, ``Limited``, ``LLP``) dropped, so ``Zorvanta Support Services Pvt. Ltd``
    is searched as ``Zorvanta Support Services``."""
    words = company.split()
    while len(words) > 1 and words[-1].strip(".,").lower() in _LEGAL_TAIL:
        words.pop()
    return " ".join(words).rstrip(".,")


def _tokens(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", plain.replace("&", " and "))


def _name_tokens(name: str) -> list[str]:
    tokens = _tokens(name)
    while tokens and tokens[-1] in _LEGAL_TAIL:
        tokens.pop()
    return tokens


_OFFICE_WORDS = {"india", "office", "offices", "branch", "head", "headquarters", "hq"}
_OFFICE_WORDS |= {"corporate", "regional", "zonal", "campus", "centre", "center", "plant", "unit"}
# Words a place, a listing or a knowledge graph may add after a company's name without naming
# another business: legal words, the kind of office, and city names.
_NAME_EXTRAS = frozenset(
    _OFFICE_WORDS
    | _LEGAL_SUFFIXES
    | {token for alias in _CITY_BY_ALIAS for token in _tokens(alias)}
)


def names_match(a: str, b: str, where: str = "") -> bool:
    """True when one company name starts the other, word for word, and the longer one adds
    only legal words, office words, a city, or words of ``where`` (the place's address or
    the listing's location, so a locality counts): ``Contoso`` matches ``Contoso India Pvt
    Ltd``, ``Contoso Pune Office`` and ``Contoso Kharadi`` at an address in Kharadi, but not
    ``Contoso Pizza``, and ``Support Services`` does not match ``Zorvanta Support Services``."""
    short, long = sorted((_name_tokens(a), _name_tokens(b)), key=len)
    extras = _NAME_EXTRAS | set(_tokens(where))
    return (
        bool(short)
        and long[: len(short)] == short
        and all(t in extras for t in long[len(short) :])
    )


def mentions(text: str, company: str) -> bool:
    """True when ``text`` carries the company's name as consecutive words."""
    words, name = _tokens(text), _name_tokens(company)
    n = len(name)
    return bool(name) and any(words[i : i + n] == name for i in range(len(words) - n + 1))


def _organic_official(organic: list[dict], company: str) -> tuple[str | None, dict | None]:
    # The first result whose domain label is the whole name run together
    # ("zorvantasupportservices"); failing that, the first whose label is only the name's
    # first words, 4+ characters ("zorvanta"), and whose title names the company, since
    # another business can share a first word ("quillon.example" for Quillon Hospitals).
    tokens = _name_tokens(company)
    whole = "".join(tokens)
    prefixes = {"".join(tokens[:k]) for k in range(1, len(tokens))}
    by_prefix: tuple[str | None, dict | None] = (None, None)
    for result in organic:
        domain = registrable_domain(result.get("link", ""))
        if not domain or not whole:
            continue
        label = domain.split(".", 1)[0].replace("-", "")
        if label == whole:
            return domain, result
        if (
            by_prefix[0] is None
            and len(label) >= 4
            and label in prefixes
            and mentions(result.get("title", ""), company)
        ):
            by_prefix = (domain, result)
    return by_prefix


_ROLE_FILLER = {"a", "an", "the", "and", "of", "for", "in", "at", "wfh", "remote", "work"}
_ROLE_FILLER |= {"from", "home", "fresher", "freshers", "urgent", "hiring"}


def _plain_role(role: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", role)).strip()


def role_matches(title: str, role: str) -> bool:
    """True when a listing's title carries more than half of the claimed role's words:
    ``Customer Support Associate`` is the role ``Customer Support Executive``,
    ``Sales Executive`` is not ``Data Entry Executive (WFH)``."""
    wanted = {t for t in _tokens(_plain_role(role)) if t not in _ROLE_FILLER}
    return bool(wanted) and 2 * len(wanted & set(_tokens(title))) > len(wanted)


def _in_city(address: str, city: str) -> bool:
    canonical = _CITY_BY_ALIAS.get(city.lower(), city)
    names = {alias for alias, name in _CITY_BY_ALIAS.items() if name == canonical}
    names |= {city.lower(), canonical.lower()}
    text = f" {' '.join(_tokens(address))} "
    return any(f" {' '.join(_tokens(name))} " in text for name in names)


_REMOTE = re.compile(r"\b(?:anywhere|remote|work\s+from\s+home)\b", re.I)


def _near(location: str, city: str) -> bool:
    # Google Jobs' location only sets where a search starts, so a listing can be in another
    # city or remote. A place the city list doesn't know (a suburb such as Chakan) counts.
    if _in_city(location, city):
        return True
    return not _REMOTE.search(location) and not _CITY.search(location)


# ---- evidence -----------------------------------------------------------------------------


def _evidence(
    params: dict,
    retrieved_at: str,
    *,
    title: str | None = None,
    link: str | None = None,
    snippet: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    return {
        "engine": params["engine"],
        "query": params.get("q", params.get("query")),
        "title": title,
        "link": link,
        "snippet": snippet,
        "date": date,
        "retrievedAt": retrieved_at,
    }


def _signal(rule: str, evidence: dict, detail: str, **extra: Any) -> Signal:
    return signal_for(rule, None, evidence, detail=detail, **extra)


def _quote(result: dict, params: dict, retrieved_at: str) -> dict[str, Any]:
    return _evidence(
        params,
        retrieved_at,
        title=result.get("title"),
        link=result.get("link"),
        snippet=result.get("snippet"),
    )


def _text(result: dict) -> str:
    return f"{result.get('title') or ''}\n{result.get('snippet') or ''}"


def _words(terms: tuple[str, ...]) -> re.Pattern[str]:
    # A term matches as a word start, so "fraud" also reads "fraudulent" and "fraudsters".
    return re.compile(r"\b(?:" + "|".join(t.replace(" ", r"\s+") for t in terms) + ")", re.I)


_FRAUD = _words(FRAUD_TERMS)
_REPORTED_TERMS = FRAUD_TERMS + ("impersonat", "dupe", "cheat", "bogus")
# A bare "offer" is not a recruitment word: a bank warns of fake loan and deposit offers.
_JOB_WORDS = (
    r"(?:recruit\w*|hiring|jobs?|offer\s+letters?|appointment\s+letters?|candidates?"
    r"|employment|careers?|interviews?|placements?|vacanc(?:y|ies))"
)
_RECRUITMENT = re.compile(rf"\b{_JOB_WORDS}\b", re.I)
# The fee phrases of ``fee_contradicts_employer``. ``_FEE_PHRASE`` reads each with its
# inflections, contractions and a few words between ("never charges", "doesn't charge",
# "does not ask candidates for any money", "no registration fee").
FEE_PHRASES = (
    "never charge",
    "no fee",
    "does not charge",
    "not ask for money",
    "not ask for payment",
    "deposit",
)
_MONEY_NOUN = r"(?:money|payments?|fees?|deposits?|charges?)"
_ASK = r"(?:ask|seek|demand|request|collect)s?"
_FEE_PHRASE = re.compile(
    rf"\b(?:never|not)\s+(?:charg(?:e|es|ed|ing)\b"
    rf"|{_ASK}\s+(?:[\w-]+\s+){{0,4}}?{_MONEY_NOUN}\b)"
    rf"|n[’']t\s+(?:charge\b|{_ASK}\s+(?:[\w-]+\s+){{0,4}}?{_MONEY_NOUN}\b)"
    r"|\bno\s+(?:[\w-]+\s+)?fees?\b"
    r"|\bdeposits?\b",
    re.I,
)


# ---- google: the official site ------------------------------------------------------------

# Never an employer's own domain, even when a knowledge graph lists one as its website.
_PLATFORMS = frozenset(
    {
        "facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com",
        "youtube.com", "justdial.com", "indiamart.com", "business.site", "google.com",
    }
)  # fmt: skip


def read_official_site(
    response: dict,
    *,
    params: dict,
    retrieved_at: str,
    company: str,
    contacts: list[dict],
    links: list[dict],
) -> Reading:
    """``lookup_official_site``: the official domain from the knowledge graph's ``website``,
    else from the first organic result whose domain is the company's whole name run together,
    else from the first whose domain is its first words and whose title names the company
    (lower confidence), then each recruiter email and link domain classified against it.

    A knowledge graph counts only when its title names the company. ``sender_official``
    needs a knowledge-graph domain, at least one recruiter email or link on it, and no
    recruiter email anywhere else: an offer that links the real careers page but writes from
    another domain is not from the employer, and an organic guess is not enough to vouch for
    a sender. ``sender_free_mail`` counts emails only; ``sender_lookalike`` emails and links.
    """
    kg = response.get("knowledge_graph")
    if kg and not names_match(kg.get("title", ""), company):
        kg = None
    organic = response.get("organic_results", [])

    official = via = None
    anchor = _evidence(params, retrieved_at)
    kg_domain = registrable_domain(kg.get("website") or "") if kg else None
    if kg_domain and kg_domain not in _PLATFORMS:
        official, via = kg_domain, "knowledge_graph"
        anchor = _evidence(params, retrieved_at, title=kg.get("title"), link=kg["website"])
    else:
        official, result = _organic_official(organic, company)
        if result is not None:
            via = "organic"
            anchor = _quote(result, params, retrieved_at)

    name = search_name(company)
    footprint = bool(kg or official) or any(mentions(r.get("title", ""), company) for r in organic)
    signals = []
    if not footprint:
        signals.append(
            _signal(
                "no_web_footprint",
                anchor,
                f"no knowledge graph and no result naming {name} or on its domain, among "
                f"{len(organic)} results",
            )
        )

    domains: dict[str, dict[str, Any]] = {}
    if official:
        found = [(c["value"], True) for c in contacts if c["kind"] == "email"]
        found += [(link["value"], False) for link in links]
        for value, email in found:
            domain = registrable_domain(value)
            if domain is None:
                continue
            entry = domains.setdefault(
                domain, {"class": classify_domain(value, official), "email": False}
            )
            entry["email"] |= email

    for domain, entry in domains.items():
        kind = "email" if entry["email"] else "link"
        if entry["class"] == "lookalike":
            reason = lookalike_reason(domain, official)
            detail = f"the recruiter's {kind} is on {domain}, not {official}: {reason}"
            signals.append(_signal("sender_lookalike", anchor, detail, domain=domain))
        elif entry["class"] == "free_mail" and entry["email"]:
            detail = (
                f"the recruiter writes from {domain}, a free-mail provider, while {name} "
                f"has its own domain {official}"
            )
            signals.append(_signal("sender_free_mail", anchor, detail, domain=domain))

    on_official = [d for d, e in domains.items() if e["class"] in ("official", "subdomain")]
    elsewhere = [d for d, e in domains.items() if e["email"] and d not in on_official]
    if via == "knowledge_graph" and on_official and not elsewhere:
        kind = "email" if any(domains[d]["email"] for d in on_official) else "link"
        detail = f"the recruiter's {kind} is on {name}'s official domain {official}"
        signals.append(_signal("sender_official", anchor, detail, domain=official))

    facts = {
        "officialDomain": official,
        "via": via,
        "knowledgeGraph": kg is not None,
        "footprint": footprint,
        "domains": {d: e["class"] for d, e in domains.items()},
    }
    if official is None:
        note = "no official domain found" + ("" if footprint else ": no web footprint")
    else:
        source = "knowledge graph" if via == "knowledge_graph" else "top organic result"
        note = f"official domain {official} ({source})"
        if domains:
            note += "; recruiter domains: " + ", ".join(
                f"{d} {e['class'].replace('_', '-')}" for d, e in domains.items()
            )
    return Reading(tuple(signals), facts, note)


# ---- google: the employer's fraud notice ---------------------------------------------------


def _is_recruitment_notice(result: dict) -> bool:
    fields = (result.get("title") or "", result.get("snippet") or "")
    sentences = [field[s:e] for field in fields for s, e in _sentences(field)]
    return _FRAUD.search(_text(result)) is not None and any(
        _RECRUITMENT.search(s) and (_FRAUD.search(s) or _FEE_PHRASE.search(s)) for s in sentences
    )


def read_fraud_notice(
    response: dict,
    *,
    params: dict,
    retrieved_at: str,
    official_domain: str,
    fee_requested: bool,
) -> Reading:
    """``find_fraud_notice``: a recruitment-fraud notice is a result on the official domain
    whose title or snippet carries a fraud term, with a recruitment word in the same sentence
    as a fraud term or a fee phrase; a bank's page on fixed-deposit fraud or fake loan offers
    is not one. A notice with a fee phrase ("never charge", "no fee", "does not charge", "not
    ask for money", "not ask for payment", "deposit") wins over one without. Both rules need
    a fee to have been asked; without one the notice is quoted in the facts only.

    A search that returns no result on the official domain is inconclusive, not a finding: Google
    can drop ``site:`` and answer with pages of other sites, so it says nothing about whether
    the employer publishes a notice. It fires no signal, and its facts carry
    ``inconclusive: true`` for the planner's one retry and for the drafts."""
    results = response.get("organic_results", [])
    on_domain = [r for r in results if is_official(r.get("link", ""), official_domain)]
    notices = [r for r in on_domain if _is_recruitment_notice(r)]
    phrased = [(r, m) for r in notices if (m := _FEE_PHRASE.search(_text(r)))]
    notice, phrase = phrased[0] if phrased else (notices[0] if notices else None, None)

    signals = []
    if notice and fee_requested:
        evidence = _quote(notice, params, retrieved_at)
        if phrase:
            detail = (
                f"a fee was asked, and {official_domain}'s own recruitment-fraud notice says "
                f'"{phrase.group(0)}"'
            )
            signals.append(_signal("fee_contradicts_employer", evidence, detail))
        else:
            detail = (
                f"a fee was asked, and {official_domain} publishes a recruitment-fraud notice "
                "(it names no fee)"
            )
            signals.append(_signal("employer_fraud_notice_exists", evidence, detail))

    inconclusive = not on_domain
    facts = {
        "notice": {k: notice.get(k) for k in ("title", "link", "snippet")} if notice else None,
        "feePhrase": phrase.group(0) if phrase else None,
        "results": len(results),
        "onDomain": len(on_domain),
        "inconclusive": inconclusive,
    }
    if inconclusive:
        where = f"{len(results)} results, all elsewhere" if results else "no results at all"
        note = (
            f"inconclusive: the search returned no page from {official_domain} ({where}), so it "
            "says nothing about a notice"
        )
    elif notice is None:
        pages = f"{len(on_domain)} page{'s' if len(on_domain) != 1 else ''}"
        note = (
            f"{pages} from {official_domain} came back, "
            f"{'none is' if len(on_domain) != 1 else 'it is not'} a recruitment-fraud notice"
        )
    elif phrase:
        note = f'{official_domain} has a recruitment-fraud notice: "{phrase.group(0)}"'
    else:
        note = f"{official_domain} has a recruitment-fraud notice with no fee phrase"
    return Reading(tuple(signals), facts, note)


# ---- google: confirming a look-alike sender -----------------------------------------------


def read_confirm_sender_domain(
    response: dict,
    *,
    params: dict,
    retrieved_at: str,
    official_domain: str,
    candidate: str,
    contacts: list[dict],
) -> Reading:
    """``confirm_sender_domain``: whether the official site names a look-alike candidate as
    fake (``domain_named_in_fraud_notice``), mentions it as its own (``sender_official``),
    or neither, which leaves ``sender_lookalike`` at moderate. Either signal carries the
    candidate's ``domain``, which is how the rule table finds the look-alike it replaces.

    A cleared candidate vouches for the sender only as ``lookup_official_site`` would: when
    no recruiter email is on a domain other than the official one or the candidate. Otherwise
    the ``sender_official`` still withdraws the look-alike but is marked ``setAside``, so it
    counts as no green signal: the employer owns the link, not the address writing to you."""
    domain = registrable_domain(candidate)
    official = registrable_domain(official_domain)
    rule, result = confirm_outcome(candidate, official_domain, response.get("organic_results", []))
    emails = {registrable_domain(c["value"]) for c in contacts if c["kind"] == "email"}
    elsewhere = sorted(d for d in emails if d and d not in (official, domain))
    facts = {"domain": domain, "outcome": rule, "emailsElsewhere": elsewhere}
    if result is None:
        note = (
            f"{official_domain} neither clears {domain} nor names it as fake: "
            "sender_lookalike stays moderate"
        )
        return Reading((), facts, note)

    evidence = _quote(result, params, retrieved_at)
    extra: dict[str, Any] = {"domain": domain}
    if rule == "domain_named_in_fraud_notice":
        detail = f"{official_domain}'s own site names {domain} next to a fraud term"
        note = f"{official_domain} names {domain} as fake: sender_lookalike replaced"
    else:
        detail = f"{official_domain}'s own site mentions {domain}, with no fraud term"
        note = f"{official_domain} mentions {domain} as its own: sender_lookalike withdrawn"
        if elsewhere:
            extra["setAside"] = (
                f"the recruiter writes from {', '.join(elsewhere)}, "
                f"not from {official_domain} or {domain}"
            )
            note += f", but {extra['setAside']}, so the sender is not vouched for"
    signal = _signal(rule, evidence, detail, **extra)
    return Reading((signal,), facts, note)


# ---- google: a recruiter contact's footprint ----------------------------------------------


def _contact_phrase(contact: dict) -> str:
    if contact["kind"] == "phone":
        return re.sub(r"\D", "", contact["value"])[-10:]
    return contact["value"]


def _carries_contact(text: str, contact: dict) -> bool:
    if contact["kind"] == "phone":
        joined = re.sub(r"(?<=\d)[\s.()-]+(?=\d)", "", text)
        return re.search(rf"(?<!\d)(?:91|0)?{_contact_phrase(contact)}(?!\d)", joined) is not None
    # Whole address only: a scam notice naming "fakehr@brand.com" or "hr@brand.com.in" is
    # not about the recruiter's "hr@brand.com".
    email = re.escape(contact["value"])
    return re.search(rf"(?<![\w.%+-]){email}(?![\w-]|\.[a-z0-9])", text, re.I) is not None


def read_contact_footprint(
    response: dict, *, params: dict, retrieved_at: str, contact: dict
) -> Reading:
    """``check_contact_footprint``: a page whose title or snippet carries the recruiter's
    phone (in any spacing, with or without +91) or email next to a scam or fraud term."""
    pages = [
        r
        for r in response.get("organic_results", [])
        if _carries_contact(_text(r), contact) and _FRAUD.search(_text(r))
    ]
    signals = ()
    if pages:
        kind = contact["kind"]
        detail = f"{len(pages)} page(s) carry the recruiter's {kind} next to a scam report"
        signals = (_signal("contact_reported", _quote(pages[0], params, retrieved_at), detail),)
    note = (
        f"the recruiter's {contact['kind']} appears in {len(pages)} scam or fraud report(s)"
        if pages
        else f"no scam or fraud report carries the recruiter's {contact['kind']}"
    )
    return Reading(signals, {"reports": len(pages)}, note)


# ---- google_jobs --------------------------------------------------------------------------


def read_job_listings(
    response: dict,
    *,
    params: dict,
    retrieved_at: str,
    company: str,
    role: str,
    city: str | None,
    official_domain: str | None,
    offered_monthly_inr: float | None,
) -> Reading:
    """``check_job_listings``: the company's own listing for this role near the claimed city,
    and the pay benchmark from the listings near it.

    The search's ``location`` only sets where Google starts looking, so a listing counts as
    near only when its location names the claimed city, or names no other known city and
    isn't remote (a suburb, or no location at all). Without a claimed city every listing
    counts. ``listing_match`` needs the company's listing to carry an apply option on the
    official domain; ``no_listing_match`` fires only when the company has no listing for the
    role near the city. A listing on a job portal alone fires neither.
    """
    listings = response.get("jobs_results", [])
    near = [job for job in listings if city is None or _near(job.get("location") or "", city)]
    theirs = [
        job
        for job in near
        if names_match(job.get("company_name", ""), company, job.get("location") or "")
        and role_matches(job.get("title", ""), role)
    ]
    applied = next(
        (
            (job, option["link"])
            for job in theirs
            for option in job.get("apply_options", [])
            if official_domain and is_official(option.get("link", ""), official_domain)
        ),
        None,
    )
    benchmark = pay_benchmark(near)
    name, where = search_name(company), f"near {city}" if city else "in India"

    signals = []
    if applied:
        job, link = applied
        evidence = _evidence(
            params,
            retrieved_at,
            title=job.get("title"),
            link=link,
            snippet=f"{job.get('company_name')} · {job.get('location')}",
        )
        detail = f"{name} lists {job.get('title')} {where}, applying on {official_domain}"
        signals.append(_signal("listing_match", evidence, detail))
    elif not theirs:
        detail = f"no listing by {name} for {_plain_role(role)} among {len(near)} listings {where}"
        signals.append(_signal("no_listing_match", _evidence(params, retrieved_at), detail))

    median = benchmark.median_monthly_inr
    if offered_monthly_inr is not None and is_pay_outlier(offered_monthly_inr, benchmark):
        evidence = _evidence(
            params,
            retrieved_at,
            snippet=" · ".join(f"{p.raw} ({p.source})" for p in benchmark.pays),
        )
        detail = (
            f"the offered ₹{offered_monthly_inr:,.0f} a month is "
            f"{offered_monthly_inr / median:.1f}x the median ₹{median:,.0f} "
            f"({benchmark.coverage})"
        )
        signals.append(_signal("pay_outlier", evidence, detail))

    facts = {
        "listings": len(listings),
        "nearCity": len(near),
        "companyListings": [
            {k: job.get(k) for k in ("title", "company_name", "location")} for job in theirs
        ],
        "payCoverage": benchmark.coverage,
        "payFound": benchmark.found,
        "enoughPayData": benchmark.enough,
        "medianMonthlyInr": median,
    }
    if applied:
        listing = f"{name}'s listing applies on {official_domain}"
    elif theirs:
        listing = f"{name} lists the role, with no apply option on its official domain"
    else:
        listing = f"no listing by {name} for this role"
    pay = benchmark.coverage + ("" if benchmark.enough else ": not enough pay data")
    if benchmark.enough:
        pay += f", median ₹{median:,.0f} a month"
    found = f"{len(listings)} listings"
    if len(near) != len(listings):
        found += f", {len(near)} of them {where}"
    return Reading(tuple(signals), facts, f"{found}; {listing}; {pay}")


# ---- google_maps --------------------------------------------------------------------------


_PLACE_FIELDS = ("title", "place_id", "type", "address", "rating", "reviews")


def maps_link(place_id: str) -> str:
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def read_office(
    response: dict, *, params: dict, retrieved_at: str, company: str, city: str
) -> Reading:
    """``check_office``: a place named after the company in the claimed city. Maps answers
    one exact match with ``place_results`` and anything else with ``local_results``. A place
    with no address counts as in the city the search was scoped to; one with no ``place_id``
    is still the office, with no Maps link to cite and no reviews to scan."""
    if "place_results" in response:
        places = [response["place_results"]]
    else:
        places = response.get("local_results", [])
    place = next(
        (
            p
            for p in places
            if names_match(p.get("title", ""), company, p.get("address", ""))
            and ("address" not in p or _in_city(p["address"], city))
        ),
        None,
    )
    name = search_name(company)
    if place is None:
        detail = f"no place named {name} in {city} among {len(places)} Maps results"
        signal = _signal("office_not_found", _evidence(params, retrieved_at), detail)
        return Reading((signal,), dict.fromkeys(_PLACE_FIELDS), detail)

    place_id = place.get("place_id")
    evidence = _evidence(
        params,
        retrieved_at,
        title=place.get("title"),
        link=maps_link(place_id) if place_id else None,
        snippet=place.get("address"),
    )
    detail = f"{place.get('title')} is on Maps in {city}"
    facts = {k: place.get(k) for k in _PLACE_FIELDS}
    return Reading((_signal("office_found", evidence, detail),), facts, detail)


def read_office_reviews(response: dict, *, params: dict, retrieved_at: str, term: str) -> Reading:
    """``scan_office_reviews``: reviews whose text carries the term. ``fee`` is read as a
    whole word ("fee" and "fees", never "feedback"); any other term as a word start, so
    ``fraud`` also reads "fraudulent" and "fraudsters". Only the text, rating, date and link
    are read; a reviewer's name or profile never is."""
    tail = r"s?\b" if term == "fee" else r"\w*"
    pattern = re.compile(rf"\b{re.escape(term)}{tail}", re.I)
    reviews = response.get("reviews", [])
    hits = [r for r in reviews if pattern.search(r.get("snippet") or "")]
    signals = ()
    if hits:
        first = hits[0]
        evidence = _evidence(
            params,
            retrieved_at,
            title=f"Google Maps review, rated {first.get('rating')}/5",
            link=first.get("link"),
            snippet=first.get("snippet"),
            date=first.get("iso_date"),
        )
        detail = f"{len(hits)} of {len(reviews)} reviews at this office mention '{term}'"
        signals = (_signal("reviews_mention_fees", evidence, detail),)
    note = f"{len(hits)} of {len(reviews)} reviews mention '{term}'"
    return Reading(signals, {"matching": len(hits), "reviews": len(reviews)}, note)


# ---- google_news --------------------------------------------------------------------------


_CLAUSE_JOINERS = r"(?:and|or|but|after|amid|while|over)\b"


def _fake_offers(company: str) -> re.Pattern[str]:
    # A fraud word that qualifies a job word, a few words apart ("fake Brand job offers",
    # "fraudsters posing as Brand recruiters"), or follows one ("recruitment fraud", "job
    # offer scam"). A fraud word elsewhere in the headline ("accounting fraud probe; job cuts
    # feared", "fake discount offers", "fake news about layoffs") is not about recruitment.
    fraud = "(?:" + "|".join(t.replace(" ", r"\s+") for t in _REPORTED_TERMS) + r")\w*"
    word = rf"(?!{_CLAUSE_JOINERS})[\w'’&.-]+"
    between = 3 + max(0, len(_name_tokens(company)) - 1)
    return re.compile(
        rf"\b{fraud}\s+(?:{word}\s+){{0,{between}}}{_JOB_WORDS}\b"
        rf"|\b{_JOB_WORDS}\s+(?:{word}\s+)?{fraud}",
        re.I,
    )


_REPORTED = _words(_REPORTED_TERMS)


def may_report_fake_offers(headline: str) -> bool:
    """True when a headline carries a fraud term and a job word anywhere in it: every headline
    ``read_scam_reports`` counts passes, whatever the company. The recording scrub keeps only
    these, so the headlines no reader looks at, which often name people, are not published."""
    return bool(_REPORTED.search(headline) and _RECRUITMENT.search(headline))


def read_scam_reports(response: dict, *, params: dict, retrieved_at: str, company: str) -> Reading:
    """``check_scam_reports``: news whose headline names the company and reports fake offers:
    a fraud term that qualifies a job word, as in "fake Brand job offers" or "recruitment
    fraud". Google News carries no snippet, so the evidence's ``snippet`` is the publisher's
    name and ``date`` the article's date."""
    fake_offers = _fake_offers(company)
    reports = [
        n
        for n in response.get("news_results", [])
        if mentions(n.get("title", ""), company) and fake_offers.search(n.get("title", ""))
    ]
    name = search_name(company)
    facts = {
        "reports": [
            {
                "title": n.get("title"),
                "link": n.get("link"),
                "source": (n.get("source") or {}).get("name"),
                "date": n.get("date"),
            }
            for n in reports
        ]
    }
    if not reports:
        return Reading((), facts, f"no news reports of fake offers in {name}'s name")

    first = reports[0]
    evidence = _evidence(
        params,
        retrieved_at,
        title=first.get("title"),
        link=first.get("link"),
        snippet=(first.get("source") or {}).get("name"),
        date=first.get("date"),
    )
    detail = (
        f"{len(reports)} news report(s) of fake offers in {name}'s name; weak, since large "
        "employers are impersonated often"
    )
    note = f"{len(reports)} news report(s) of fake offers in {name}'s name"
    return Reading((_signal("impersonation_reports", evidence, detail),), facts, note)
