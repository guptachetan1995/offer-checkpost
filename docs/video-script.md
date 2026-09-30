# Demo video: shot list

The demo video is a screen recording of the app running on this machine, at
`127.0.0.1:<port>`, under three minutes. `python -m demo.record_demo` starts the app, opens the
address it prints in Google Chrome (headless, 1280x720) and records the page with Playwright
while it clicks through the beats below as the person. The filmed take runs on **live
SerpApi** with the local cache off (`OFFER_CHECKPOST_NO_CACHE=1`), so every search on screen is
a real, counted SerpApi search, and the call-log strip under the header shows each one as it
runs.

- **It opens on the live app, and says what it is for in the first sentence.** The first frame
  is the app's empty state (the header, "No SerpApi calls yet"), and the first sentence is
  "Offer Checkpost checks a job offer before anyone pays." The three differences follow as
  captions over real screens, inside 16 s: a forwarded copy of a checked offer costs 0
  searches (the empty call log ringed), the evidence is the employer's own fraud notice (the
  page's own explanation ringed), only a person publishes and an agent provably cannot (the Offer
  Board, "Nothing published yet. Only a person publishes a verdict.", ringed).
- **Each sentence starts when the screen reaches it, and each caption goes up with the change
  it describes.** A beat's narration is a list of sentences, spoken by macOS `say` (the default
  system voice), one clip each. The recorder logs the capture second at which the page reached
  what each sentence says (the click, the scroll to the finding) and waits for the sentence
  before it to finish, so a sentence never starts before its action and never overlaps the
  last. A beat's captions are separate lines: each goes up once the page has scrolled to (or
  the ring has landed on) what it describes, is taken down before the page scrolls on, and is
  replaced by the next; none is left over a beat, and none covers the finding line it
  describes (the page keeps its targets clear of the caption's row). `render.json` carries every
  clip's start and `narration_late` (the most a clip ran behind its cue; 0.01 s at most here) and
  every caption's second and words.
- **Nothing is sped up except a wait on searches.** Every interval the page waits on searches
  in flight that is longer than 4 s plays in 2.5 s, under a label that names the provider, the
  number of searches, the real duration and the factor, for example "Sped up · Live SerpApi: 2
  searches took 14 s · shown 5.5x faster". The label is on screen for exactly the sped-up 2.5
  s. Everything else plays in real time.
- **Still, silent stretches are cut.** Where the screen did not change (ffmpeg's `freezedetect`)
  and nobody is speaking, all but 0.6 s after a sentence and 0.5 s before the next is left out:
  one stretch in this film, 0.9 s, listed in `render.json`. A wait is never cut,
  only sped up. A beat's last sentences can be left out of the video with the footage after
  them (`dropped` in `demo/beats.py`): the film keeps to three minutes, and the page's checks
  name only what the kept sentences say.
- **The words are checked against the page.** Each beat names text the page must show for its
  narration to be true (`expects` in `demo/beats.py`: the look-alike finding, the fraud-notice
  search's result, the budget line, the bands, the reuse trace, the call-log count and so on).
  The recorder reads them off the filmed page, and the render refuses a take where one is
  missing. Narration rewritten after a capture, to what its page did show, names text the
  capture never checked: the render refuses that too unless `--allow-unchecked`, and
  `render.json` lists those claims as `unchecked`, for a person to confirm against the frames.
  This film's render needed no such flag: `unchecked` and `failed` are empty.
- **The two live searches the film can't know beforehand decide some beats' words, and the
  film says what happened.** The recorder reads each case's state after its investigation.
  Sample A: `decisive` if the employer's fraud notice fired `fee_contradicts_employer` and the
  case stopped decisive at 2 searches; `retried` if the first notice search returned no page
  from hcltech.com and the planner's one retry (the notice's usual titles) found it, decisive
  at 3; `unverified` if the checks ran to their end without it; `unverified_retried` if a retry
  ran too and found none. Beat 3's narration and captions say which (in `retried`: "the first
  search returned no page from hcltech.com, so it retried with the notice's usual titles"),
  beats 4 and 5 follow. In `unverified` and `unverified_retried` the film carries one 13.3 s
  **replay cutaway** after beat 4: the decisive path from the recordings, its caption dated
  ("Replay of responses recorded earlier on 30 Sep 2026; live results move"), with the header
  badge and the page's banner saying replay, not live, and the narration says what the live
  search found. In `decisive` and `retried` the notice is shown live and there is no cutaway.
  Sample B: `clean` if Google Jobs returned Siemens's own listing and nothing contradicts the
  offer, `unverified` if it returned no Siemens listing (one red signal against the green
  office, band unchanged); beat 8 says which and never says "genuine". The recorder stops the
  take on any other stop (a failed search): a forwarded copy cannot reuse a check that never
  finished. **This film is `decisive` and `clean`**, with no cutaway.
- A **replay** take (`--provider replay`) spends no search. It differs in beat 1, whose caption
  and narration name the recorded responses instead of live SerpApi; the end card's last line
  says so; and it is always `decisive` and `clean`, since the recordings hold the notice at the first
  search and Siemens's listing. Its call-log strip says `replay` on every call. It has no
  cutaway.

## Beats

Times are from the final render: the live take of 30 Sep 2026, 15:59-16:02 IST (take 2 of 2),
`decisive` and `clean` variants, no cutaway, 150.03 s, 10 live searches. "Clicks" are the
person's, made in the page opened from the address the app printed. The header's "searches left"
count is SerpApi's Account API and trails the call log for a while (it reads 144 at the start,
143 from about 31 s beside 2 calls in beats 3 to 5, 142 from about 63 s, so it drops during the
0-search beat 5 and stays beside 2 calls and then 4 and 5 in beat 6, 138 beside 6 calls, 137
beside 10 from about 114 s, and 134 from 133.6 s: 10 searches spent); the first caption says
it can lag. Captions are listed in order; each is on screen from
the moment shown until the next sentence starts or the page scrolls on.

| # | Time | On screen | Action | Caption |
|---|---|---|---|---|
| 1 | 0:00.0-0:16.3 | The live app, empty: `127.0.0.1:<port>`, **Live SerpApi**, "144 searches left this month" (the Account API), "No SerpApi calls yet", the "Check an offer before anyone pays" card, the Offer Board ("Nothing published yet. Only a person publishes a verdict."). Rings on the provider badge, the searches-left badge and the tagline; then on the call log, the card's lead paragraph, and the board (scrolled to). | Page loaded | "Running locally, on live SerpApi. Its searches-left count can lag." · "1 · A forwarded copy of a checked offer costs 0 searches" · "2 · The evidence is the employer's own fraud notice" · "3 · Only a person publishes; an agent provably cannot" |
| 2 | 0:16.3-0:26.7 | Sample A's claim chips: HCLTech, Data Entry Executive (WFH), Noida, ₹38,000/month, "refundable registration fee of ₹2,499", the look-alike `hcltech-careers.example` sender. The draft verdict **Unverified**, "The message alone proves nothing". | Try a sample -> A; **Open case**; **Confirm claims** | The message alone proves nothing: verdict unverified. |
| 3 | 0:26.7-0:43.9 | **Investigate**: the call log gains 2 calls, `google` 1,186 ms and `google` 824 ms, so there is no wait to speed up. The trace: step 1 finds hcltech.com and marks the sender **Look-alike sender domain**; step 2 "A fee was asked and step 1 named the official domain hcltech.com", `site:hcltech.com (recruitment OR hiring) (fraud OR scam OR fake) never (fee OR money OR payment)`, "Found: hcltech.com has a recruitment-fraud notice: "never ask for any payment"" and **Employer says it charges no fee**; the stop row "Decisive: the band is high_risk and no search left can change it, so 4 searches were not spent"; "2 searches spent of 6 · 4 not spent · stopped: the evidence was decisive", **High risk**. The evidence card quotes the notice ("HCLTech will never ask for any payment of money at any stage of the interview or recruitment process ...") with its link, www.hcltech.com/de-de/careers/genuine-job-offers, retrieved 30 Sep 2026. | **Investigate**; the page scrolls through the trace and the evidence | "Search 1 flagged the sender's domain as a look-alike." · "Search 2 ran because search 1 named the employer's domain." · "The employer's own notice says it never asks for any payment." · "Decisive: 4 searches never spent." |
| 4 | 0:43.9-0:52.8 | Publish card: label **Likely impersonation**, note "Do not pay the fee." typed in. The Offer Board post and its **Copy for WhatsApp** button. The hint under the label reads "The draft verdict is high risk. "No contradictions found" is not offered on it; a red-flag label is your call." | Choose the label, type the note, **Publish to Offer Board** | "Only a person publishes a verdict." · "The officer posted it, with a copy for WhatsApp." |
| 5 | 0:52.8-1:11.7 | **The forwarded copy.** **New case**; `samples/offers/a-forwarded.txt` pasted into the offer box (the same claims, "Fwd:" and different spacing); **Open case**, with "same message as case_001" beside the case's id, ringed; **Confirm claims**. **Investigate**: the strip stays at "2 calls" and no wait appears. "0 searches spent of 6 · 2 not spent · stopped: same message as case_001: its checks were reused", **High risk**; the trace's **reuse** row "Same message as case_001: reused, 0 searches" carrying the first case's findings (look-alike sender domain, employer says it charges no fee), and each of case_001's step rows marked "1 search spent by case_001" and "reused from case_001". | **New case**, paste, **Open case**, **Confirm claims**, **Investigate** | "The app notes it is the same message as case 1." · "0 searches: the call log does not grow." · "The trace says reused, 0 searches." |
| 6 | 1:11.7-1:32.4 | Sample C's chips: Kavrellon Support Services, Customer Support Executive, Indore, Rs. 42,000 per month; the documents and chat-only flags. The trace: no web footprint, the **reorder** that moves `check_office` ahead of the listings, **Office not on Maps**, **High risk**, "Decisive ... 4 searches were not spent". Its 2 searches took 1.5 s at real speed. | **New case**; Try a sample -> C; **Open case**; **Confirm claims**; **Investigate** | "Sample C: no fee asked, an invented firm in Indore." · "No fee asked, yet the searches still find what doesn't add up." |
| 7 | 1:32.4-1:42.2 | "Live SerpApi: 2 searches took 14 s · shown 5.5x faster" (`google_jobs` 5,946 ms, `google_news` 7,259 ms). "Checks the decisive stop skipped"; the trace lines marked *Run on request after a decisive result*: `google_jobs` with **No matching listing** and **Pay far above comparable listings** (pay found in 6 of 10 listings, median ₹17,292 a month), `google_news`, the reviews scan skipped for lack of a place. The drafted reply to the recruiter is filmed but its sentence and footage are left out of the video. | **Run remaining checks**; **Draft recruiter reply** | "Only a person spends searches after a decisive result." · "Google Jobs: no listing by this firm." · "Pay far above comparable listings." |
| 8 | 1:42.2-2:05.9 | "Live SerpApi: 4 searches took 6 s · shown 2.4x faster". Sample B, the Siemens Application Support Engineer opening in Bengaluru. The trace: step 1 names siemens.com; `find_fraud_notice` **skipped** ("No fee or sensitive documents were asked for, and no recruiter email or link is off siemens.com"); "Found: 10 listings, 7 of them near Bengaluru; Siemens's listing applies on siemens.com" and **Listing applies on the official domain**; "Siemens Ltd is on Maps in Bengaluru", **Office found on Maps**; "no news reports in Siemens's name". The draft verdict **Nothing found contradicts the offer**. Four more calls in the strip (10 in all). | **New case**; Try a sample -> B; **Open case**; **Confirm claims**; **Investigate** | "The fraud-notice search is skipped: nothing to contradict." · "Siemens's own listing, its office on Maps, no fake-offer news." · "It never says 'genuine'. Its best is 'nothing contradicts this'." |
| 9 | 2:05.9-2:13.6 | Case A again: the drafted summary for helpline 1930 / cybercrime.gov.in, "Not sent · Draft only". The board download follows in the capture; its sentence and footage are left out of the video. | Case A tab; **Draft 1930 / cybercrime.gov.in summary** | "Drafts only. Nothing is ever sent or filed." |
| 10 | 2:13.6-2:24.8 | A panel with a scripted MCP client's real exchange with `python -m offer_checkpost mcp`: `tools/list` has 17 tools and no `publish_verdict`; `tools/call publish_verdict` comes back `isError: true`, refused: "publish_verdict is human-only ...". Beside it, the activity log's newest row: **agent** · `publish_verdict` · **REFUSED**, ringed, and the page's note "The agent called publish_verdict (refused)." | The recorder runs the MCP client (no language model) over stdio | "A scripted MCP client, no language model, is the agent." · "The app refuses publish_verdict and logs it as the agent's." |
| 11 | 2:24.8-2:30.0 | End card: Offer Checkpost; https://github.com/guptachetan1995/offer-checkpost; SerpApi India Hackathon 2026 · Knowledge & Public Interest track; "This take: 10 live SerpApi searches, from 127.0.0.1:<port> · Account API searches left: 144 before, 134 after (it can trail)". | - | - |

Where a take's live searches went the other way, the beats change as `demo/beats.py` says (its
`decisive`, `retried`, `unverified` and `unverified_retried` variants for beat 3, `unverified`
for beat 8, and the replay cutaway after beat 4 for an unverified A); none of that is in this
film.

**Known limits of this film.** Captions are baked into the capture, so none of these was fixed
by a re-render. Beat 8's second caption ("Siemens's own listing, its office on Maps,
no fake-offer news.") goes up when only the news finding is fully on screen, with the listing and
office rows just scrolled above it. Beat 8's first sentence, "The fraud-notice search is
skipped: nothing to contradict.", is spoken about 2.5 s before the skip row scrolls into view.
Beat 3's first caption sits over the top of step 2's query line, not over the finding it
describes. Near 125 to 127 s the case-tab strip is scrolled so that the ring lands on a
clipped fragment of case A's tab. The searches-left count in the header drops from 143 to 142
during beat 5 while the call log stays at 2; the caption there is about the call log, and
nothing said is false. The scripted MCP client in beat 10 is the agent, and its caption says
"provably cannot" for what the app's own gate refuses; a program running as the person is not
covered (README, "Run it").

## Narration, word for word

The take's sentences, as shipped (`decisive`, `clean`); where another variant says something
else it follows in italics. Each is one clip, cued to its action.

1. Offer Checkpost checks a job offer before anyone pays. / Three differences. A forwarded copy
   of a checked offer costs zero searches. / The evidence is the employer's own fraud notice,
   if it has one. / Only a person publishes; an agent provably cannot.
   *(Replay take: "Offer Checkpost checks a job offer before anyone pays; this take replays
   recorded SerpApi responses, and says so." first.)*
2. A forwarded HCLTech offer, with a refundable registration fee. / The officer confirms each
   claim. / The message alone proves nothing: unverified.
3. Investigate. Search one finds HCLTech's official domain, and flags the sender's as a
   look-alike. / A fee was asked, so search two reads HCLTech's own site: it never asks for any
   payment. / Decisive: four searches never spent. High risk.
   *(`retried`: "A fee was asked, so search two looks on HCLTech's site for a fraud notice, and
   gets no page from hcltech.com." / "So search three retries with the notice's usual titles,
   and finds it: decisive after three searches, three never spent. High risk."
   `unverified`: "A fee was asked, so search two looks on HCLTech's own site for a fraud notice.
   Today it finds no notice to quote." / "The planner keeps checking until its six searches run
   out: still unverified." `unverified_retried`: the `retried` second sentence, then "Search
   three retries with the notice's usual titles: still no notice to quote. The planner runs its
   six searches out: unverified.")*
4. Only a person publishes. / The officer labels it likely impersonation and posts it to the
   Offer Board, with a copy for WhatsApp.
   *(Unverified A: "Only a person publishes. The draft stays unverified, but the officer
   chooses to warn students." / "Likely impersonation, on the Offer Board, with a copy for
   WhatsApp.")*
4b. (Only in an unverified A's film.) Replay, not live: the same offer, on responses recorded
   earlier on 30 September; live results move. / Search two finds HCLTech's own notice: high
   risk, four searches never spent.
5. The same offer, forwarded by another student, pasted as a new case. / Investigate: zero
   searches. It reuses the first check, and the call log does not grow. / The trace says
   reused: same verdict, high risk.
   *(Unverified A: "... the call log stays at six, and the header's count is still catching
   up." / "... same verdict, unverified.")*
6. Sample C: an invented firm in Indore. 42,000 rupees a month for freshers, and no fee. / The
   flags: documents asked for before any interview, and a chat-only interview. No web
   footprint, so the planner checks Maps for an office first. None. High risk.
7. Only a person spends searches past a decisive result. / Google Jobs: no listing by this
   firm, and pay far above comparable listings. *(A third sentence, "Then a drafted reply asks
   the recruiter one question per red flag.", is left out with its footage.)*
8. Sample B, a real Siemens opening in Bengaluru: links only to Siemens, and no fee. /
   Investigate. The fraud-notice search is skipped: nothing to contradict. / Jobs finds
   Siemens's own listing, Maps the office, and News no fake-offer reports. / Its best verdict:
   nothing found contradicts the offer. Never genuine.
   *(Unverified B: "Jobs finds no listing by Siemens for this role today, and Maps finds the
   office." / "The result: unverified. One red signal and one green are not enough to move the
   band.")*
9. Back on case A: a drafted summary for the 1930 cybercrime helpline. Nothing is sent or
   filed. *(A second sentence, "And the Offer Board downloads as one page.", is left out with
   its footage.)*
10. Now an agent: a scripted MCP client, no language model, calls publish verdict. / It isn't
   in the agent's tools. The app refuses it, and logs it as the agent's.
11. Offer Checkpost. Knowledge and Public Interest. Code on GitHub.

## Runtime

A sentence starts at the second the page reaches what it says, no sooner than 0.4 s into its
beat and no sooner than 0.25 s after the sentence before it ends; the beat ends at least 0.6 s
after its last sentence. Where the words run longer than the footage (words rewritten after the
capture), the beat's last frame is held. The final render:

| Beat | Sentences | Narration (s) | Beat (s) | Last frame held (s) | Clip behind its cue (s) |
|---|---|---|---|---|---|
| intro | 4 | 14.49 | 16.30 | 0.03 | 0.01 |
| a-claims | 3 | 8.08 | 10.43 | 0.03 | 0.00 |
| a-investigate | 3 | 15.64 | 17.20 | 0.03 | 0.00 |
| a-publish | 2 | 6.83 | 8.84 | 0.00 | 0.00 |
| a-forwarded | 3 | 12.95 | 18.96 | 0.03 | 0.01 |
| c-investigate | 2 | 15.40 | 20.67 | 0.00 | 0.00 |
| c-remaining | 2 | 7.62 | 9.77 | 0.27 | 0.00 |
| b-investigate | 4 | 19.19 | 23.73 | 0.00 | 0.00 |
| a-report | 1 | 6.19 | 7.67 | 0.33 | 0.00 |
| mcp-refused | 2 | 9.93 | 11.20 | 0.00 | 0.00 |
| end | 1 | 4.26 | 5.26 | 0.00 | 0.00 |
| **Total** | **27** | **120.58** | **150.03** | **0.72** | |

150.03 s is what ffprobe measured (4,501 frames at 30 fps), against the hard ceiling of 179
s. The render refuses a video of 179 s or more, one with no audio stream, and one whose frame
count is more than a frame off the plan. The plan starts each clip after the one before it ends
plus 0.25 s, so no clip overlaps another, and the audio's mean is -23.3 dB with a peak of -5.2
dB. Silences in the audio longer than 1.5 s number seven, from 1.5 s to 3.5 s, each where the
page scrolls, rings a target, pastes text or waits for a click to land between two sentences
(the longest two, 3.5 s each, are the forwarded copy being pasted and confirmed, and sample C's
searches).

Two waits on searches were longer than 4 s and each plays in 2.5 s under its label: C's
remaining checks (2 searches, 13.8 s of capture) and B's investigation (4, 6.0 s). A's
investigation waited 2 s and the forwarded copy's on no search, so neither is sped up. The
capture itself ran 177.4 s.

**Searches:** at most 18 in a live take (the per-case budget of 6 for each of A, C and B; the
app is started with that budget pinned, so a larger one in the environment can't make the guard
wrong). The final take spent 10: A 2 (the notice found at the second), the forwarded copy 0, C 2
+ 2, B 4. Before its first search the recorder reads "N searches left" and "used" from the
header's Account API badge, and refuses to go on if the worst case could take this month's
usage past `--max-usage`. `render.json` keeps the counts before and after: 106 used (144 left)
and 116 used (134 left). This was the second live take. The first (15:50 IST, 11 searches, 95
to 106 used) found A's notice at search two as well, but Google Jobs returned no Siemens
listing for B, so B's words (the listing, "nothing contradicts") were not true of it and its
captions could not be changed after the capture; it was discarded, and the recorder now says
what B found as it does for A.

## Claims table

Every feature the README and the submission copy name, with the beat that shows it or the
test that proves it.

| Claim | Shown in beat | Proved by |
|---|---|---|
| Runs locally, on 127.0.0.1 only | 1 (bind badge, strip) | `test_server_http.py::test_binding_anything_but_127_0_0_1_raises_before_anything_is_bound` |
| Live SerpApi, and the searches left this month from the Account API (which can trail the call log for a while) | 1, 3, 11 | `test_providers.py::test_account_returns_only_the_five_counts` |
| The call-log strip: engine, live or replay, cache, milliseconds | 1, 3, 5, 6, 7, 8 | `test_ui_e2e.py::test_the_strip_shows_whole_columns_of_the_newest_calls`, `test_server_http.py::test_the_call_log_grows_while_an_investigation_is_running` |
| Claims extracted as chips, highlighted where they came from | 2, 5, 6, 8 | `test_extract.py::test_sample_spans_are_exact` |
| ₹, K, LPA, lakh and +91 numbers read | 2 (₹38,000/month), 6 (Rs. 42,000) | `test_extract.py::test_pay_formats_are_normalised_to_monthly_inr`, `test_extract.py::test_plus_91_phone_formats_and_masks`, `test_salary.py::test_lpa_is_a_years_pay_whatever_period_follows_it` |
| Romanised Hinglish read | — | `test_extract.py::test_sample_claims_and_text_rules` (the `hinglish-*` samples), `test_salary.py::test_hinglish_pay_forms` |
| Claims confirmed or corrected before any search | 2, 5, 6, 8 (confirm) | `test_planner.py::test_investigate_refuses_unconfirmed_claims_and_searches_nothing`, `test_ui_e2e.py::test_a_correction_marks_out_of_date_everything_that_relied_on_it` |
| The page marks claims the agent confirmed as the agent's | — | `test_ui_e2e.py::test_claims_the_agent_confirmed_say_so_until_a_person_confirms_them` |
| Four text rules: fee, documents early, chat-only interview, task-scam pattern | 2 (fee), 6 (documents, chat-only) | `test_extract.py::test_text_rules_are_the_four_in_table_order`, `test_extract.py::test_task_scam_pattern` |
| The message alone leaves the verdict unverified | 2 | `test_rules.py::test_the_bands_never_certify_an_offer` |
| Official domain from the knowledge graph or the top result; look-alike sender | 3 | `test_checks.py::test_sample_a_knowledge_graph_names_the_domain_and_the_sender_is_a_lookalike`, `test_checks.py::test_an_organic_domain_anchors_a_lookalike` |
| A fee asked and the domain known: `site:` search for the employer's own fraud notice | 3 (live, search two: the notice is found and quoted) | `test_recordings.py::test_sample_as_fraud_notice_search_quotes_the_fee_phrase_from_hcltechs_own_site` |
| A fee-notice search that returns no page from the employer's domain is inconclusive, not "no notice", and is retried once in a broader wording | — (a take whose live search goes that way narrates it, `retried` above; not this film's) | `test_checks.py::test_a_search_with_no_page_from_the_official_domain_is_inconclusive_not_no_notice`, `test_planner.py::test_an_inconclusive_notice_search_is_tried_once_more_in_the_broad_wording`, `test_planner.py::test_a_second_inconclusive_search_stays_inconclusive_and_is_the_last_one` |
| Stops when decisive and shows the searches saved | 3 ("4 not spent"), 6 ("4 searches were not spent") | `test_planner.py::test_sample_a_stops_decisive_after_two_searches_with_four_saved` |
| An unknown firm gets Maps before listings (R1) | 6 | `test_planner.py::test_sample_c_r1_checks_the_office_first_and_stops_decisive` |
| No fee and every link on the official domain: the fraud-notice search is skipped | 8 | `test_planner.py::test_a_link_on_the_official_domain_skips_the_fraud_notice_without_a_knowledge_graph` |
| No Maps place, no reviews query | 7 (the skipped `scan_office_reviews`) | `test_planner.py::test_a_missing_city_skips_the_office_and_its_reviews` |
| Google Maps Reviews scanned for fees at a found office | — (neither A nor B ran it in this film: B's reviews scan is skipped, beat 8; it runs when no listing matches) | `test_checks.py::test_reviews_that_mention_the_fee`, `test_planner.py::test_the_office_reviews_are_scanned_for_fraud_when_no_listing_matched` |
| A real recruiter phone or email searched next to scam reports | 7 (skipped: the sample's contacts are placeholders) | `test_checks.py::test_contact_footprint_searches_the_exact_contact`, `test_checks.py::test_a_contact_on_a_page_next_to_a_scam_report` |
| A look-alike checked against the official site (`confirm_sender_domain`) | — (A stops decisive before it in this film; it runs when the budget goes on) | `test_checks.py::test_the_official_site_naming_the_candidate_as_fake_replaces_the_lookalike` |
| Google Jobs: listing match and a pay benchmark ("pay found in n of m listings") | 7, 8 | `test_checks.py::test_sample_c_no_listing_and_pay_far_above_the_city_median`, `test_salary.py::test_pay_found_in_n_of_m_listings` |
| Pay above 2x the median of 3 or more listings is flagged | 7 | `test_salary.py::test_pay_outlier_at_exactly_2x_does_not_fire_and_at_2_01x_does`, `test_salary.py::test_pay_outlier_needs_three_data_points` |
| Google News reports of fake offers, weighted weak | 7, 8 | `test_drafts.py::test_b_verdict_cites_three_greens_notes_the_weak_reports_and_never_says_genuine` |
| India parameters per engine, and a `json_restrictor` on every search | — (the trace shows only each search's query) | `test_checks.py::test_google_builders_set_the_india_parameters_and_the_restrictor`, `test_checks.py::test_each_restrictor_keeps_the_fields_its_reader_uses` |
| Every finding is a named rule fired by a quoted search result, with a link where the result has a page | 3 (the evidence card) | `test_drafts.py::test_a_search_signal_cites_the_step_that_found_it_never_the_line_that_reused_it` |
| Three bands; never "genuine", "verified" or "safe" | 3, 6, 8 | `test_drafts.py::test_no_draft_calls_an_offer_genuine_or_safe`, `test_rules.py::test_the_bands_never_certify_an_offer` |
| **Run remaining checks** is the person's; the agent never spends past a decisive stop | 7 | `test_planner.py::test_run_remaining_checks_refuses_the_agent_and_changes_nothing`, `test_planner.py::test_the_agent_calling_a_check_directly_never_searches_past_a_decisive_stop` |
| Investigating again checks afresh | — | `test_planner.py::test_investigating_again_checks_afresh_and_a_person_can_still_run_the_remaining_checks` |
| Drafted recruiter reply, one question per red flag | — (filmed, left out of the video to keep it under three minutes) | `test_drafts.py::test_c_reply_asks_about_documents_footprint_office_pay_and_chat_but_never_a_fee` |
| Drafted 1930 / cybercrime.gov.in summary; nothing is sent or filed | 9 | `test_drafts.py::test_a_cybercrime_report_says_what_was_asked_when_by_which_contact_and_the_evidence` |
| Publishing to the Offer Board is a person's click | 4, 10 | `test_server_http.py::test_each_human_only_verb_is_refused_for_the_agent_and_changes_nothing`, `test_ui_e2e.py::test_paste_confirm_investigate_publish_in_chrome` |
| "Copy for WhatsApp" | 4 (the button) | `test_ui_e2e.py::test_paste_confirm_investigate_publish_in_chrome` (copies it), `test_drafts.py::test_a_whatsapp_text_carries_the_label_the_note_and_the_evidence_links` |
| The board downloads as an HTML page | — (filmed, left out of the video to keep it under three minutes) | `test_server_http.py::test_the_board_download_has_only_published_verdicts` |
| "No contradictions found" is offered only on the best band, and a red-flag label never on it | 4 (the hint reads "No contradictions found" is not offered on a high-risk draft) | `test_ui_e2e.py::test_the_label_follows_the_draft_verdict_and_a_text_only_case_says_why_it_cannot_post` |
| A post is a snapshot, and publishing is refused if the case changed since it was read | — | `test_ui_e2e.py::test_a_label_chosen_before_the_agent_changed_the_case_never_publishes_the_changed_case`, `test_server_http.py::test_an_agent_cannot_rewrite_a_published_post_or_the_board_download` |
| A case on the board can't change until a person retracts it | — | `test_gate.py::test_a_case_on_the_board_is_not_changed_until_a_person_retracts_it` |
| Retract a verdict; record an outcome (the person's) | 4 (Retract…, Outcome buttons on screen) | `test_gate.py::test_once_retracted_the_case_can_be_corrected_and_published_again`, `test_server_http.py::test_the_same_verbs_run_for_a_person` |
| A forwarded copy is answered from the earlier check at 0 searches | 5 (0 searches spent, "reused from case_001", the strip still at 2 calls) | `test_gate.py::test_a_forwarded_copy_is_answered_from_the_first_check_at_zero_searches`, `test_planner.py::test_the_fingerprint_ignores_case_spacing_and_legal_suffixes` |
| Per-case budget | 3 ("2 searches spent of 6 · 4 not spent") | `test_planner.py::test_the_budget_cap_stops_before_the_search_that_would_pass_it` |
| The monthly quota guard (a reserve of 20 searches) | — (it never triggers in the film) | `test_planner.py::test_the_quota_guard_stops_below_the_reserve_without_falling_back` |
| The agent's tools and the person's verbs are separate lists; verbs never registered as tools | 10 | `test_mcp.py::test_the_tool_list_is_the_agents_17_tools_and_no_human_only_verb`, `test_server_http.py::test_the_tool_list_is_the_agents_and_names_no_human_only_verb` |
| The MCP adapter over stdio is always the agent, and a refused verb is logged as the agent's | 10 | `test_mcp.py::test_a_human_only_verb_is_a_tool_error_carrying_the_apps_refusal_logged_as_the_agents` |
| The person is the browser that opened the single-use address; a request can't claim to be them | — | `test_server_http.py::test_the_token_address_works_once_and_the_next_one_signs_the_first_browser_out`, `test_server_http.py::test_a_request_without_the_session_cannot_claim_to_be_the_person` |
| The agent's calls show in the open page's activity log | 10 | `test_ui_e2e.py::test_the_agents_calls_appear_without_costing_the_person_what_they_were_doing` |
| Loopback guards: foreign host, origin, non-JSON calls refused | — | `test_server_http.py::test_a_foreign_or_missing_host_is_forbidden_on_every_request`, `test_server_http.py::test_a_fetch_from_another_site_is_forbidden` |
| Replay mode, dated and marked not live, never making a result up | — (this film has no replay stretch; an unverified A's film carries a labelled cutaway, beat 4b above) | `test_ui_e2e.py::test_replay_says_when_its_responses_were_recorded`, `test_providers.py::test_replay_never_makes_up_an_unrecorded_query` |
| Live results cached for 24 hours; `OFFER_CHECKPOST_NO_CACHE` bypasses it | — | `test_providers.py::test_the_cache_lasts_24_hours`, `test_providers.py::test_no_cache_skips_the_cache_sends_no_cache_and_refreshes_it` |
| The key is never logged, shown or kept in an error | 1 (no key on screen) | `test_key_hygiene.py::test_a_401_never_carries_the_key`, `test_no_secrets.py::test_the_tree_holds_nothing_key_shaped` |
| Recorded responses are trimmed and identify nobody | — | `test_scrub.py::test_the_whitelist_covers_every_engine_a_check_reads`, `test_no_secrets.py::test_recordings_and_fixtures_identify_nobody` |
| The candidate's own details are never sent | — | `test_checks.py::test_contact_footprint_searches_the_exact_contact`, `test_extract.py::test_sample_contacts_and_links_are_all_synthetic_but_a_real_offers_careers_link` |
| `investigate <file>` from the command line, and `--as agent` | — | `test_cli.py::test_sample_a_prints_its_trace_two_searches_and_four_saved`, `test_cli.py::test_sample_c_as_the_agent_makes_every_call_the_agents` |
| macOS, Linux and Windows commands | — | Neither shown nor tested: the Windows commands in the README are not run by any test. |
