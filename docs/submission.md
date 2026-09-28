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

Offer Checkpost checks a job offer before anyone pays, replies or hands over documents.

WHO IT'S FOR: the person students forward these messages to: a college placement officer, a
training-institute coordinator, or an NGO or cyber-cell volunteer. They run it. The students
and job seekers it protects, often freshers outside the big cities who get "shortlisted" on
WhatsApp or Telegram for jobs they never applied to, get the answer as a WhatsApp message or a
shared page. Scam messages are sent in bulk, so one check answers a whole batch.

THE INSIGHT: a scam offer is a set of claims about the public web: a company, a role, an
office, a recruiter and a pay figure. A scammer can forge the message but not the search
results about it. The employer's official domain is in Google's knowledge graph, and many large
employers publish a notice on that domain saying they never charge candidates. A real opening
is listed on Google Jobs, and a real office shows up on Google Maps. Many fake-job detectors
train a text classifier on the wording of job posts, and a scammer can simply reword the
message. Offer Checkpost checks what the message claims instead.

WHAT IT DOES: paste the message. Offer Checkpost extracts the checkable claims, shows where
each one came from, and lets you confirm them. It reads Indian formats: ₹, K, LPA, lakh and
+91 numbers. A planner then checks the claims against live SerpApi results and picks each next
check from what the earlier searches found. When the knowledge graph names the employer's
domain and the message asks for a fee, it searches that domain for the employer's own fraud
notice. When the firm has no web footprint, it checks Maps for the office before looking for
listings. When the recruiter writes from the official domain and asks no fee, it skips the
fraud-notice search and looks for the listing first. It stops as soon as the evidence is
decisive, and the trace shows why each search ran and how many it saved. Every finding is a
named rule fired by a quoted, linked search result. The draft verdict is one of three bands:
high risk, unverified, or consistent with a genuine offer. It never says "genuine" or "safe".

WHAT YOU GET: a drafted verification reply to the recruiter, with one question per red flag;
a pre-filled summary for the national cybercrime helpline 1930 / cybercrime.gov.in; and, for a
placement officer, a verdict to publish on the Offer Board. The board is the officer's own
log, and it reaches students as "Copy for WhatsApp" text or a downloaded page.

HUMAN-ONLY BY DESIGN: "the agent" here is the automated investigator, or any MCP client; no
LLM runs inside the app. The agent drafts. Only a person publishes or retracts a verdict, or
spends searches after the evidence is already decisive. Those verbs are never registered as
agent tools, and they refuse any caller but the human. The app never sends a message, files a
report or contacts a recruiter. A false "scam" label can smear a real employer, and a false
"genuine" costs a candidate money. Both are too costly to automate.

PRIVACY: the searches carry the claimed company, role and city and the official domain, plus a
recruiter's domain or contact only when one is being checked. The candidate's own details are
never sent. Everything else stays on the machine running it.

Runs locally: Python, on SerpApi's official client. Judges can run it with their own SerpApi
key, or keyless in replay mode on real SerpApi responses recorded during development and
labelled with their date.

## How the project uses SerpApi (max 4,000 characters)

SerpApi is the product. Without search, Offer Checkpost can only read the message text, and
its verdict can never rise above "unverified". Every conclusion it draws comes from a SerpApi
result, and each engine proves a different claim.

All calls go through SerpApi's official Python client (serpapi 1.1.2), with India parameters
set per engine as each engine's documentation allows, and a json_restrictor so SerpApi returns
only the fields the app reads:

- Google Search, knowledge graph and organic results (gl=in, google_domain=google.co.in, an
  Indian-city location): reads knowledge_graph.website and organic_results[].link to find the
  employer's official domain, then classifies the recruiter's email or link domain as that
  domain, a look-alike, or a free-mail address.
- Google Search with site:<official domain>: reads organic_results[].title and snippet for the
  employer's own recruitment-fraud notice, and whether it says the employer never charges
  fees. The same kind of search checks whether the official site mentions a look-alike
  domain, either as its own second domain or as a known fake.
- Google Jobs (gl=in, google_domain=google.co.in, location = the claimed city): reads
  jobs_results[].apply_options for a listing by this company with an apply link on the
  official domain, and the pay of comparable listings from detected_extensions.salary (then
  extensions, then the description). An offer above 2x the median of 3 or more listings is
  flagged.
- Google Maps (location plus z, gl=in): reads local_results[].place_id, title and address to
  see whether the claimed office exists as a place.
- Google News (gl=in, hl=en): reads news_results[].title, link and source for reports of fake
  offers made in the company's name. This is weighted weak, because big brands are
  impersonated constantly.
- Account API (free): searches left this month, shown in the header and used by a quota guard
  that stops an investigation before the monthly reserve is touched.

Two further checks run only when the evidence calls for them: Google Maps Reviews with its
query filter reads reviews[].snippet at a found office when a fee was asked (never the
reviewer's identity), and an exact-phrase Google search looks for a real recruiter phone or
email next to scam reports.

Why it's used this way: the next search depends on what the earlier ones found. With no
official domain there is no site: search and the office is checked first; with the sender on
the official domain and no fee asked, the fraud-notice search is skipped; with no Maps place
there is no reviews query. The planner stops when the evidence is decisive. In the demo, the
impersonation sample is settled in 2 searches with 4 of its 6-search budget saved, the unknown
firm also in 2, and the genuine offer takes 4. So the free plan covers at least 38 checks a
month, and the same bulk-sent message forwarded again is answered at 0 searches. Results are
cached locally. The trace shows every engine call, why it ran, and whether it came from the
cache. Recorded responses, trimmed to the fields above and scrubbed of keys, account fields
and personal data, power a keyless replay mode and the test fixtures.

## AI tools used (max 2,000 characters)

Claude Code (Anthropic) did most of the hands-on work under my direction: researching the
rules against the concept, planning, writing the code, tests and documentation, and scripting
the Playwright screen recording of the demo. I set the constraints and reviewed the result.
No AI model runs inside the app: every verdict comes from deterministic rules over SerpApi
results.

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
