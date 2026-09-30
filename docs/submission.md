# Submission copy — Offer Checkpost

**Hackathon:** [SerpApi India Hackathon 2026](https://serpapi.github.io/serpapi-india-hackathon-2026/) ·
**Deadline:** 10 October 2026, 23:59 IST. A draft is never submitted automatically, so
"Submit project" has to be clicked before then.

This file holds the copy for every field on the hackathon's submission form, in the form's
order. The field copy is plain text, so it pastes into the form as written. Fields marked
form-only are entered in the form and not kept here.

## Lead participant name

Chetan Gupta

## Lead participant email, mobile number

Form-only.

## Occupation, years of experience

Form-only ("for internal analytics only", according to the form).

## Project name

Offer Checkpost

## Track

Knowledge & Public Interest

## Public GitHub repository

https://github.com/guptachetan1995/offer-checkpost (live after the first publish; it opens
without signing in).

## Demo video

Added once it's uploaded: a screen recording under three minutes of the project running
locally, checked in a private browser window.

## Project description (max 4,000 characters)

Knowledge & Public Interest: a fraud check for the person students forward job offers to,
evidenced by the employer's own words.

Offer Checkpost checks a job offer before anyone pays, replies or hands over documents.

WHO IT'S FOR: the person students forward these messages to: a college placement officer, a
training-institute coordinator, or an NGO or cyber-cell volunteer. They run it. The students it
protects, often freshers "shortlisted" on WhatsApp or Telegram for jobs they never applied to,
get the answer as a WhatsApp message or a shared page. Scam messages are sent in bulk, so one
check answers a whole batch.

WHAT'S DIFFERENT from a 0-100 scam-score checker:
- One check answers the batch. While the app runs, a forward that makes the same claims as a
  message already checked (company, role, city, pay, fee and recruiter contacts) is answered
  from that check at 0 searches, and its trace names the case it reused.
- The proof is the employer's own words. When the message asks for a fee, it searches the
  employer's official domain for the employer's own notice that it charges candidates
  nothing, and quotes it with the link. A search that returns no page from that domain is
  reported inconclusive, not "no notice", and retried once in a broader wording (tested offline, not yet fired live). The demo
  video's live run for Sample A found HCLTech's own notice at search two.
- An agent cannot publish through the app's tools, API or MCP adapter: the agent's tools and
  the person's verbs are separate lists. A published verdict is a person's click, a snapshot
  of what they read.
- Each search is chosen from what the earlier ones found. It stops once the evidence is
  decisive and shows the searches it saved, and every search sends a json_restrictor.
- The best band reads "No contradictions found", never "verified", "safe" or a score.

WHAT IT DOES: paste the message. Offer Checkpost treats it as a set of claims about the public
web, since a scammer can forge the message but not the search results about it. It extracts the
claims, shows where each one came from, and lets you confirm or correct them. A planner then
checks the claims against live SerpApi results: an unknown firm gets its office checked on Maps before its listings, and a
recruiter on the official domain who asks no fee skips the fraud-notice search. Every finding
is a named rule fired by a quoted, linked search result. The draft verdict is one of three
bands: high risk, unverified, or "No contradictions found".

WHAT YOU GET: a drafted verification reply to the recruiter, with one question per red flag;
a pre-filled summary for the national cybercrime helpline 1930 / cybercrime.gov.in; and, for a
placement officer, a verdict to publish on the Offer Board, the officer's own log, which
reaches students as "Copy for WhatsApp" text or a downloaded page.

HUMAN-ONLY BY DESIGN: no LLM runs inside the app. "The agent" is the automated investigator,
or an AI agent driving the app's 17 tools through its stdio MCP adapter; the four human-only
verbs are not among those tools, and a call to one is refused and logged. The agent drafts.
Only a person publishes or retracts a verdict, records an outcome, or spends searches after
the evidence is decisive, and the person is the browser that opened the single-use address the
app prints. The app never sends a message, files a report or contacts a recruiter. A false
"scam" label smears a real employer; a false "genuine" costs a candidate money.

PRIVACY: the searches carry the claimed company, role and city and the official domain, plus a
recruiter's domain or contact only when one is being checked. The candidate's own details are
never sent.

Runs locally in Python. Judges can run it with their own key or in keyless replay mode, which
serves the three demo samples' real SerpApi
responses, recorded on 29 and 30 September 2026 and labelled with those dates.

## How the project uses SerpApi (max 4,000 characters)

SerpApi is the product. Without search, Offer Checkpost can only read the message text, and
its verdict can never rise above "unverified". Every conclusion it draws comes from a SerpApi
result, and each engine proves a different claim.

All searches go through SerpApi's official Python client (serpapi 1.1.2), with India
parameters per engine as its documentation allows and a json_restrictor, so SerpApi returns
only the fields the app reads:

- Google Search, knowledge graph and organic results (gl=in, google_domain=google.co.in, an
  Indian-city location): reads knowledge_graph.website and organic_results[].link to find the
  employer's official domain, then classifies the recruiter's email or link domain as that
  domain, a look-alike, or a free-mail address.
- Google Search with site:<official domain>: reads organic_results[].title and snippet for the
  employer's own recruitment-fraud notice, and whether it says the employer never charges
  fees. The query pairs recruitment, fraud and fee words with "never", in parenthesised OR
  groups, so site: still scopes it; on 30 September 2026 it found the employer's notice for 3
  of 4 employers (HCLTech, Infosys, TCS, Wipro; the best of five phrasings scored on those
  four), the earlier phrasing for none. In an earlier film take that afternoon Google apparently dropped
  site: and returned no page from hcltech.com: the app now calls that inconclusive and retries
  once with a broader wording. The same kind of search checks whether the official site names
  a look-alike domain.
- Google Jobs (gl=in, google_domain=google.co.in, location = the claimed city): reads
  jobs_results[].apply_options for a listing by this company with an apply link on the
  official domain, and the pay of comparable listings from detected_extensions.salary (then
  extensions, then the description). An offer above 2x the median of 3 or more listings is
  flagged.
- Google Maps (location plus z, gl=in): reads local_results[].place_id, title and address to
  see whether the claimed office exists as a place.
- Google News (gl=in, hl=en): reads news_results[].title, link and source for reports of fake
  offers made in the company's name.
- Account API (free): searches left this month, shown in the header and used by a quota guard
  with a monthly reserve.

Two checks run only when needed: Google Maps Reviews with its query
filter reads reviews[].snippet at a found office when a fee was asked or no listing matched
(never the reviewer), and an exact-phrase Google search looks for a real recruiter phone or
email next to scam reports.

Why it's used this way: the next search depends on what the earlier ones found. With no
official domain there is no site: search and the office is checked first; with the sender on
the official domain and no fee asked, the fraud-notice search is skipped. The planner stops when the evidence is decisive, and its trace shows every call, why it ran and the searches saved.
In replay, HCLTech stops at 2 of its 6 searches (4 more if a person asks), the unknown firm
at 2 (2 more if asked), and Siemens takes 4. In the live video (30 Sep 2026, 16:00 IST), the
site: search found HCLTech's own notice: 2 searches, high risk, 4 not spent, and the same offer
forwarded again is answered from that check at 0 searches. An earlier take that day found
none. With a 6-search
cap and a 20-search reserve, the free plan's 250 searches cover at least 38 checks a month.
Recorded responses keep only these fields, with no keys or profiles, and phones and emails
masked.

WHERE TO LOOK, by judging criterion (README sections and files): idea strength, "The problem";
originality, "What's different here"; technical complexity, planner.py (its docstring is the
policy), extract.py, invoke.py, server.py, mcp_server.py, tests/; usefulness, "How it works"
step 4 and "What it can't check"; SerpApi usage, "Where SerpApi comes in" and checks.py.

## AI tools used (max 2,000 characters)

Claude Code (Anthropic) did most of the hands-on work under my direction: researching the
rules against the concept, planning, and writing the code, tests and documentation, including
the Playwright tests that drive the app in Chrome. I set the constraints and reviewed the
result. The demo video's narration is macOS text-to-speech (the built-in say command). No AI
model runs inside the app: every verdict comes from deterministic rules over SerpApi results.

## Additional team members

None; this is a solo entry.

## How did you hear about the hackathon?

No community — I found it elsewhere

## Checkboxes

| Checkbox | Answer |
|---|---|
| This project existed before the hackathon. | Unticked. It is a new project: planned on 28 September 2026 and coded from 29 September 2026, all inside the hackathon period (1 September to 10 October 2026). |
| I tested the repository and demo links in an incognito window and confirm that judges can open them without requesting access. | Ticked once both links have been opened in a private window. |
| I have read and accept the Hackathon Rules. | Ticked at submission. |
| I have read and accept the Terms & Conditions. | Ticked at submission. |
| Marketing opt-in | Unticked. |
