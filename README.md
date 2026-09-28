# Offer Checkpost

A scammer can forge a job offer, but not the search results about it. The company's official
domain is in Google's knowledge graph, and that domain may carry the company's own warning that
it never charges candidates. A real opening is listed on Google Jobs, and a real office shows
up on Google Maps.

Offer Checkpost is for the person students forward these messages to: a college placement
officer, a training-institute coordinator, or an NGO or cyber-cell volunteer. They paste the
offer. Offer Checkpost treats it as a set of claims and checks each one against live
[SerpApi](https://serpapi.com) results **before** a student pays a "refundable registration
fee", replies to the recruiter, or sends a photo of their Aadhaar card. The student gets the
answer as a WhatsApp message or a shared page, and installs nothing.

Built for the [SerpApi India Hackathon 2026](https://serpapi.github.io/serpapi-india-hackathon-2026/),
in the **Knowledge & Public Interest** track.

**Status: the investigator is built; the web app is next.** Offer-text extraction (the claims
and the four text rules, in English and romanised Hinglish, with Indian money and `+91` phone
formats), domain classification (registrable domains, free mail, look-alikes), listing-pay
parsing, the SerpApi checks, the decision table, the planner, the drafts, and the one `invoke`
path with the agent's tools and the human-only verbs are in place, with their tests and 21
fictional sample offers in `samples/offers/`. `python -m offer_checkpost investigate <file>`
runs an offer through all of it from the command line. The server and the UI land by
8 October 2026, and this README then gives the full run commands, which will work from a fresh
clone.

## The problem

Job seekers in India, often freshers outside the big cities, get "shortlisted" on WhatsApp or
Telegram for jobs they never applied to. The message names a well-known company, a work-from-home
role, generous pay and a small fee "to confirm your slot within 2 hours". A college placement
officer gets the same messages forwarded by students every week, with the question "is this
real?", and students act on the answer. Scam messages are sent in bulk, so one answer serves a
whole batch.

Most of what such a message claims can be checked. The checks just take twenty minutes across
five places, and you have to know what to look for. And the two ways of getting it wrong cost
different things:
- **Calling a scam genuine** costs the candidate money and identity documents.
- **Calling a real offer a scam** in front of a whole batch smears a real employer, and the
  students miss a real job.

So Offer Checkpost never certifies an offer as genuine. Its best finding is "no
contradictions found", and a verdict posted for others to read is always a person's decision.

## How it works (planned)

1. **Paste the message.** Offer Checkpost extracts the claims (company, role, city, pay, any
   fee, the recruiter's email, phone and links) and shows each one as a chip, highlighted where
   it came from in the text. You confirm or correct the chips before anything is searched.
   Four rules read the text directly: money asked for, identity documents asked for early, an
   interview held only in chat, and the paid-likes or "prepaid task" pattern. On their own they
   leave the verdict at *unverified*, because the message alone proves nothing.
2. **Investigate.** A planner checks the claims against SerpApi and picks each next check from
   what the earlier searches found:
   - the company's domain is known and a fee was asked: it searches that domain for the
     company's own fraud notice;
   - the firm has no web footprint at all: it checks for the office on Maps before looking for
     job listings;
   - the recruiter writes from the official domain and asks no fee: it skips the fraud-notice
     search and looks for the listing first;
   - there's no Maps place: there's no reviews query.

   It stops as soon as the evidence is decisive. The trace shows every search, why it ran or
   was skipped, whether it came from the cache, and how many searches it saved. After a
   decisive stop, a person can click **Run remaining checks**; the agent never spends those
   searches on its own. Investigating a case again checks it afresh: the earlier findings are
   set aside, so no finding is ever counted twice.
3. **Read the evidence.** Each finding is a named rule fired by a quoted search result with a
   link. The draft verdict is one of three bands: *high risk*, *unverified*, or *consistent
   with a genuine offer*.
4. **Act, as a person.** Copy the drafted verification reply to the recruiter (one question per
   red flag), use the pre-filled summary for the national cybercrime helpline **1930** /
   cybercrime.gov.in if money has already gone, or, as a placement officer, publish a verdict
   to the **Offer Board**. The board is the officer's own published log on their machine. It
   reaches students as "Copy for WhatsApp" text or as a downloaded HTML page.

**Who "the agent" is:** the automated investigator (the planner), or any MCP client. No LLM
runs inside the app.

**Why not a text classifier?** Many fake-job detectors train a classifier on the wording of
job posts. That learns what scam text looks like, and a scammer can reword the message. Offer
Checkpost checks what the message *claims* against the web instead, which the scammer can't
change, and every conclusion comes from a named rule with the search result behind it.

### Where SerpApi comes in

| Engine | What it checks | Fields read | Rules it can fire | In the demo video |
|---|---|---|---|---|
| `google` (knowledge graph, organic) | The company's official domain, and whether the recruiter's email or link domain is that domain, a look-alike, or a free-mail address | `knowledge_graph.website`, `organic_results[].link` | `sender_official`, `sender_lookalike`, `sender_free_mail`, `no_web_footprint` | yes |
| `google` with `site:<official domain>` | Whether the employer's own site publishes a recruitment-fraud notice that says it never charges fees; whether it mentions a look-alike domain, and in what context | `organic_results[].title`, `.snippet`, `.link` | `fee_contradicts_employer`, `employer_fraud_notice_exists`, `domain_named_in_fraud_notice` | yes |
| `google_jobs` | Whether this company lists this role near this city, with an apply option on the official domain, and what comparable roles there pay | `jobs_results[].apply_options`, `.detected_extensions.salary` (then `.extensions`, then `.description`) | `listing_match`, `no_listing_match`, `pay_outlier` | yes |
| `google_maps` | Whether the claimed office exists as a place | `local_results[].place_id`, `.title`, `.address` | `office_found`, `office_not_found` | yes |
| `google_news` | Reports of fake offers made in this company's name (a weak signal, since big brands are impersonated constantly) | `news_results[].title`, `.link`, `.source.name` | `impersonation_reports` | yes |
| `google_maps_reviews` (`query` filter), conditional | Whether reviews at a found office mention fees; runs only when a fee was asked or no listing matched | `reviews[].snippet`, `.iso_date`, `.link`; never the reviewer | `reviews_mention_fees` | no |
| `google`, exact phrase, conditional | Whether a real recruiter phone or email already appears next to scam reports | `organic_results[].snippet` | `contact_reported` | no |
| Account API (free) | Searches left this month, shown in the header | the five usage counts only | none, but it feeds the quota guard | yes |

**India parameters, per engine:** `google` and `google_jobs` use `gl=in`,
`google_domain=google.co.in` and an Indian-city `location`; `google_maps` uses `location` plus
`z`, with `gl=in` and `google_domain=google.co.in`; `google_news` uses `gl=in` and `hl=en`;
`google_maps_reviews` uses `place_id` and `hl=en`.

Every call goes through SerpApi's official Python client with a `json_restrictor`, so SerpApi
returns only the fields listed above. Results are cached locally, and the app stays well inside
the free plan's 50 searches an hour. A check spends at most 6 searches by default, and a
decisive one stops at 2. So the free plan's 250 searches a month, less a 20-search reserve,
covers at least 38 checks, and the same bulk-sent message forwarded again (the same company,
role, city, pay, fee and recruiter contacts) is answered from the earlier check at 0 searches.

## What the agent does, what only the human does

| | Agent | Human |
|---|---|---|
| Extract claims from the pasted message | Yes | Confirms or corrects them |
| Run searches and fire rules | Yes, within a per-case search budget, stopping when decisive | Starts an investigation |
| Spend searches after the evidence is already decisive | **Never** | `run_remaining_checks` |
| Draft the verdict, the recruiter reply and the 1930 summary | Yes, drafts only | Reads them |
| Send a message, file a report, contact a recruiter | **Never**: the app has no way to do any of these | Does it themselves, outside the app |
| Publish a verdict to the Offer Board | **Never** | `publish_verdict` |
| Retract a published verdict | **Never** | `retract_verdict` |
| Record what they decided (walked away / proceeding / reported to 1930) | **Never** | `record_outcome` |

## Tools

Every call, from the agent or from a button in the UI, goes through one
`invoke(tool, args, actor)` function. Each tool's description is written for a reader who
cannot see the screen, and each one says what the tool does not do.

| Tool | What it does | What it does not do |
|---|---|---|
| `open_case` | Stores the pasted message, extracts claims with their source spans, runs the text rules | Search anything or contact anyone |
| `update_claims` | Corrects or confirms extracted claims, and flags the findings that depended on a corrected claim as stale (a new fee amount flags none: the rules read only whether a fee is asked) | Delete, re-fire or re-score findings; touch drafts or the board |
| `lookup_official_site` | Finds the official domain and classifies the recruiter's domains | Treat the official site as proof the offer is genuine |
| `find_fraud_notice` | Quotes a recruitment-fraud notice from the employer's own domain, and says whether it mentions fees | Run before an official domain is known, or quote any other site as the employer |
| `confirm_sender_domain` | Checks whether the official site mentions a look-alike domain, as its own second domain or as a known fake | Open either site, or check a domain that isn't in the offer |
| `check_job_listings` | Looks for a matching listing and benchmarks pay against comparable listings | Apply to jobs, or treat a missing listing as proof |
| `check_office` | Checks whether the claimed office exists on Google Maps | Judge a business by its rating |
| `scan_office_reviews` | Quotes review text at the matched office that mentions a fee or fraud term | Run without a matched place, show who wrote a review, or turn a review into an accusation |
| `check_scam_reports` | Finds news reports of fake offers in the company's name | Conclude this offer is fake from reports about others |
| `check_contact_footprint` | Searches one recruiter-supplied phone or email next to scam terms | Search the candidate's own details, or any sample contact |
| `investigate` | Runs the planner within the search budget and returns the trace | Run on unconfirmed claims, exceed the budget, search past a decisive result, fall back to made-up data, or publish anything |
| `draft_verdict` | Writes the draft band and an evidence-cited summary | Publish, or ever say "genuine" or "safe" |
| `draft_recruiter_reply` | Drafts verification questions, one per red flag | Send anything |
| `draft_cybercrime_report` | Drafts a summary for 1930 / cybercrime.gov.in | File or submit anything |
| `get_case`, `list_cases` | Read cases | Change anything |
| `search_budget` | Shows searches left this month (free Account API) | Expose the account email or key, or spend a search |

A check tool called on its own keeps the planner's stop rules: it refuses, spending nothing, on
unconfirmed claims, past the per-case search budget or the monthly quota reserve, and, when the
agent calls it, once the evidence is already decisive.

Four verbs are **human only**: `publish_verdict` (posts a verdict, with its evidence, to the
Offer Board), `retract_verdict` (removes a post and logs why), `record_outcome` (records the
person's own decision) and `run_remaining_checks` (spends searches after a decisive result).
They are **never registered as a tool**. The agent's tool list doesn't include them, and they
refuse any caller but the human, even when called directly. A click in the UI counts as the
human; the planner's own searches count as the agent, and the activity log shows both. The
caller is a field in the request, which is safe only because the server listens on
`127.0.0.1` alone.

## Stack (planned)

- Python 3.13 for development, runs on 3.11+
- [`serpapi`](https://pypi.org/project/serpapi/) 1.1.2, SerpApi's official Python client, as
  the only runtime dependency
- Standard-library HTTP server; plain HTML, CSS and JavaScript UI with no build step
- pytest and ruff; Playwright (driving an installed Google Chrome) for the end-to-end test and
  the demo recording
- Three search providers behind one interface: **live** (your SerpApi key, with a local
  cache), **replay** (real SerpApi responses recorded during development and labelled with
  their date; no key needed), and **fake** (synthetic data for the tests)

## Setup, run, test

`make setup`, `make test`, `make lint`, `./verify.sh` and the command-line investigator work
today; `make run` and the web app land by 8 October 2026. The commands, on macOS or Linux:

```bash
cp .env.example .env          # add your own SERPAPI_KEY, or leave it empty for replay mode
make setup                    # virtualenv + hash-locked install
make run                      # http://127.0.0.1:8741
make test                     # the whole suite, offline
make lint
./verify.sh                   # everything above, as one check
```

On Windows, or anywhere without `make`:

```bat
python -m venv .venv
.venv\Scripts\pip install --require-hashes -r requirements-dev.lock
.venv\Scripts\pip install --no-deps -e .
copy .env.example .env
.venv\Scripts\python -m offer_checkpost serve
.venv\Scripts\python -m pytest
```

With no key in `.env`, the app starts in replay mode and says so on every screen.

To watch the planner work on a sample offer with no key, against the synthetic test data:

```bash
.venv/bin/python -m offer_checkpost investigate samples/offers/a.txt --provider fake
```

It prints every search with why it ran or was skipped, the band, the searches spent and saved,
the draft verdict and the activity log. `--as agent` makes every call the agent's. Nothing is
published from the command line: publishing is a person's click in the app.

`./verify.sh` checks this README, the licence, the ignore rules for secrets and the sample
environment file, then runs lint and the tests.

## What it can't check

- **Offers that name nothing checkable.** Most "task" scams (paid likes, reviews, prepaid
  tasks) name no company, role or office. Only the text rules apply, so the verdict stays
  *unverified*, with the text red flags listed first.
- **Scripts other than Latin.** Extraction reads English and romanised Hinglish, not Hindi in
  Devanagari or other Indian scripts.
- **Screenshots.** Paste the text; images are not read.
- **Anything behind a login:** WhatsApp groups, Telegram channels, a job portal's private
  listings.
- **Small or new genuine employers** with no knowledge graph, listing or Maps place can look
  like an unknown firm. That's why the band describes the offer, not the company, and why only
  a person publishes a verdict.
- **Whether a real employer's real recruiter is behaving badly.** It checks the offer's claims,
  not people.
- **Search results change.** Replay mode shows responses recorded on a stated date, not today's.

## Keys and privacy

- Your SerpApi key belongs in `.env`, which is git-ignored. It is never logged, never shown in
  the UI, never written into recorded responses, and never taken from an error message; a test
  scans the repository for anything key-shaped.
- **What leaves your machine, exactly:** search queries carrying the claimed company, role and
  city and the official domain; a recruiter's domain when a look-alike is being confirmed; and
  a recruiter's phone or email only when the contact-footprint check runs. The candidate's own
  details are never sent.
- Pasted messages and the Offer Board stay in memory on your machine and are gone when the
  server stops. The board is a single-machine log; "Copy for WhatsApp" and the downloaded page
  are how a verdict reaches anyone else.
- All sample offers are fictional. Their contacts use reserved `.example` domains and masked
  phone numbers.
- The recorded SerpApi responses used by replay mode are trimmed to the fields listed above,
  with reviewer identities and personal profiles removed. They are third-party search content,
  not covered by this repository's licence, and are included only for replay and tests.

## License

[MIT](./LICENSE). Copyright (c) 2026 Chetan Gupta. The MIT licence covers this project's own
code and documentation, not the recorded third-party search content.
