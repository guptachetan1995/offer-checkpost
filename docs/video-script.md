# Demo video: shot list

The demo video is a screen recording of the app running on this machine, at
`127.0.0.1:<port>`, under three minutes. `python -m demo.record_demo` starts the app, opens the
address it prints in Google Chrome (headless, 1280x720) and records the page with Playwright
while it clicks through the beats below as the person. The filmed take runs on **live
SerpApi** with the local cache off (`OFFER_CHECKPOST_NO_CACHE=1`), so every search on screen is
a real, counted SerpApi search, and the call-log strip under the header shows each one as it
runs.

- **Captions** are drawn into the page by the recorder. **Narration** is macOS text-to-speech
  (`say`, the default system voice), one clip per beat, starting 0.4 s into the beat, plus a
  per-beat delay where the screen takes a few seconds to reach what the first words say, and
  ending at least 0.6 s before the next one. When rewritten narration is longer than its
  footage, the beat's last frame is held for the difference.
- **Nothing is sped up except a wait on searches.** Every interval the page waits on searches
  in flight that is longer than 4 s plays in 3 s, under a label that names the provider, the
  number of searches, the real duration and the factor, for example "Sped up · Live SerpApi: 2
  searches took 63 s · shown 21x faster". The label is on screen for exactly the sped-up 3 s.
  Everything else plays in real time.
- **The words are checked against the page.** Each beat names text the page must show for its
  narration to be true (`expects` in `demo/beats.py`: the look-alike finding, the fraud-notice
  search's result, the budget line, the bands, the pay-outlier finding and so on). The recorder
  reads them off the filmed page, and the render refuses a take where one is missing, so a live
  search that found something else is never narrated as if it found what the recordings did.
  Narration rewritten after a capture, to what its page did show, names text the capture never
  checked: the render refuses that too unless `--allow-unchecked`, and `render.json` lists those
  claims as `unchecked`, for a person to confirm against the frames.
- **One replay cutaway.** The filmed live search for HCLTech's fraud notice found none that
  day, so sample A stays unverified on screen. A 14-second cutaway, spliced in after beat 4,
  runs sample A again on the recorded responses, which hold the notice: two searches, High
  risk, four never spent. It is captioned "Replay of SerpApi responses recorded 29 Sep 2026",
  the header badge and the page's banner say replay and not live, and the narration opens
  "Replay, not live". It spends no search.
- A **replay** take (`--provider replay`) spends no search. It differs in beat 1, whose caption
  and narration name the recorded responses instead of live SerpApi; in beats 3 and 4, whose
  narration says what the recorded responses found (HCLTech's own notice, decisive at 2
  searches, High risk, and a label with no unverified draft to speak of); and in the end card's
  last line. Its call-log strip says `replay` on every call. It has no cutaway.

## Beats

Times are from the final render: the live take of 29 Sep 2026, 16:20–16:25 IST, with the
replay cutaway, 174.03 s, 14 live searches. "Clicks" are the person's, made in the page opened
from the address the app printed. The header's "searches left" count is SerpApi's Account API
and can trail the call-log strip by a few searches for a while (it reads 223 beside 6 calls
in beat 3, and 212 at the end, which is exactly 226 − 14).

| # | Time | On screen | Action | Caption |
|---|---|---|---|---|
| 1 | 0:00.0–0:09.8 | The header: `127.0.0.1:<port>`, **Live SerpApi**, "226 searches left this month" (the Account API). The empty call-log strip. The paste card. | Page loaded; each header badge and the strip ringed in turn | Running locally, on live SerpApi. |
| 2 | 0:09.8–0:24.8 | Sample A's claim chips: HCLTech, Data Entry Executive (WFH), Noida, ₹38,000/month, "refundable registration fee of ₹2,499", the look-alike `hcltech-careers.example` sender. The draft verdict **Unverified**, "The message alone proves nothing". | Try a sample → A; **Open case**; **Confirm claims** | The message alone proves nothing: verdict unverified. |
| 3 | 0:24.8–0:48.4 | The wait, sped up: "Live SerpApi: 6 searches took 76 s · shown 25x faster". The trace: "6 searches spent of 6 · stopped: the case's search budget ran out", **Unverified**; step 1 `google` finds hcltech.com and marks the sender **Look-alike sender domain**; step 2 "A fee was asked and step 1 named the official domain hcltech.com", the `site:` search (53,177 ms) with "Found: no recruitment-fraud notice on hcltech.com"; step 3 `confirm_sender_domain`. The evidence cards (fee asked, look-alike sender with its hcltech.com source link, no matching listing), under **Unverified**. Six calls in the strip, and "223 searches left" in the header (see above). | **Investigate**; the page scrolls through the trace and evidence | Search 2 ran because search 1 named the employer's domain. |
| 4 | 0:48.4–0:59.1 | Publish card: label **Likely impersonation**, note "Do not pay the fee." typed in. The Offer Board post and its **Copy for WhatsApp** button. The draft verdict is still **Unverified**: the hint under the label reads "A label that says more than the checks found is not offered", and the person still picks a red-flag label, which the app allows on that band (it refuses only a red-flag label on the best band, and "No contradictions found" on any other). | Choose the label, type the note, **Publish to Offer Board** | Only a person publishes a verdict. |
| 4b | 0:59.1–1:13.4 | The replay cutaway. The header badge reads "Replay: recorded 29 Sep 2026, not live" and the page's banner says the responses were recorded and nothing is made up. Sample A's chips; **Investigate** on the replay provider (two searches, nothing to speed up); "2 searches spent of 6 · 4 not spent · stopped: the evidence was decisive"; the evidence card quoting HCLTech's own notice, "never ask for recruitment fees", from hcltech.com; **High risk**. Two `replay` calls in the strip. | Try a sample → A; **Open case**; **Confirm claims**; **Investigate** | Replay of SerpApi responses recorded 29 Sep 2026: the same message. |
| 5 | 1:13.4–1:35.8 | Sample C's chips: Kavrellon Support Services, Customer Support Executive, Indore, Rs. 42,000 per month; the documents and chat-only flags. The trace: no web footprint, the **reorder** that moves `check_office` ahead of the listings, **Office not on Maps**, **High risk**, "Decisive … 4 searches were not spent". Its 2 searches took 2.5 s, so they play in real time. | **New case**; Try a sample → C; **Open case**; **Confirm claims**; **Investigate** | No fee asked — the searches still find what doesn't add up. |
| 6 | 1:35.8–1:53.7 | "Live SerpApi: 2 searches took 13 s · shown 4.3x faster". "Checks the decisive stop skipped"; the trace lines marked *Run on request after a decisive result*: `google_jobs` with **No matching listing** and **Pay far above comparable listings** (pay found in n of m listings, the median), `google_news`, the reviews scan skipped for lack of a place. The drafted reply to the recruiter, one question per red flag, "Draft only: nothing is sent or filed." | **Run remaining checks**; **Draft recruiter reply** | Only a person spends searches after a decisive result. |
| 7 | 1:53.7–2:20.6 | "Live SerpApi: 4 searches took 39 s · shown 13x faster". Sample B, the Siemens Application Support Engineer opening in Bengaluru. The trace: step 1 names siemens.com; `find_fraud_notice` **skipped** ("no recruiter email or link is off siemens.com"); **Listing applies on the official domain**; **Office found on Maps**; no news reports. The draft verdict **Nothing found contradicts the offer**. Four more calls in the strip. | **New case**; Try a sample → B; **Open case**; **Confirm claims**; **Investigate** | It never says 'genuine'. Its best is 'nothing contradicts this'. |
| 8 | 2:20.6–2:36.3 | Case A again: the drafted summary for helpline 1930 / cybercrime.gov.in, "Not sent · Draft only". Then the downloaded `offer-board.html` open in the browser, with the published post and its evidence link. | Case A tab; **Draft 1930 / cybercrime.gov.in summary**; **Download board (HTML)**, then the saved file opened; back to the app | Drafts only. Nothing is ever sent or filed. |
| 9 | 2:36.3–2:48.4 | A panel with a scripted MCP client's real exchange with `python -m offer_checkpost mcp`: `tools/list` has 17 tools and no `publish_verdict`; `tools/call publish_verdict` comes back `isError: true`, refused: "publish_verdict is human-only …". Beside it, the activity log's newest row: **agent** · `publish_verdict` · **REFUSED**, and the page's note "The agent called publish_verdict (refused)." | The recorder runs the MCP client (no language model) over stdio | An MCP client is the agent: publishing is refused. |
| 10 | 2:48.4–2:54.0 | End card: Offer Checkpost; https://github.com/guptachetan1995/offer-checkpost; SerpApi India Hackathon 2026 · Knowledge & Public Interest track; "This take: 14 live SerpApi searches, from 127.0.0.1:<port> · Account API: 226 → 212 searches left". | — | — |

## Narration, word for word

1. Offer Checkpost, running locally, at local host, on live SerpApi. The header shows the
   searches left this month; SerpApi's count can trail the call log.
   *(Replay take: "Offer Checkpost, running on this machine, at local host. This take replays
   SerpApi responses recorded on the 29th of September, and says so.")*
2. A placement officer opens a forwarded offer in HCLTech's name: data entry from home, and a
   refundable registration fee. Each claim becomes a chip, and the officer confirms them. The
   message alone proves nothing: unverified.
3. Investigate. Search one finds HCLTech's official domain, and flags the sender's as a
   look-alike. Because a fee was asked, search two looks on HCLTech's own site for a fraud
   notice. Live today, that search finds none, so the planner keeps checking until its six
   searches run out. The message stays unverified.
   *(Replay take, from the recorded responses: "… search two looks on HCLTech's own site, and
   finds its warning: it never asks for recruitment fees. Decisive, so four searches are never
   spent. High risk.")*
4. Only a person publishes. The draft stays unverified, but the officer chooses to warn
   students: likely impersonation, on the Offer Board, with a copy for WhatsApp.
   *(Replay take: "Only a person publishes. The officer labels it likely impersonation, and
   posts it to the Offer Board, with a copy for WhatsApp.")*
4b. Replay, not live: the same message against responses recorded on the 29th of September,
   when HCLTech's notice was in the results. Search two finds it: high risk, and four searches
   never spent.
5. Sample C: an invented firm in Indore. 42,000 rupees a month for freshers, a Telegram-only
   interview, Aadhaar and bank photos up front, and no fee. No web footprint, so the planner
   checks Maps for an office before job listings. None. High risk.
6. Only a person spends searches past a decisive result. Google Jobs: no listing by this firm,
   and pay far above comparable listings nearby. Then a drafted reply asks the recruiter one
   question per red flag.
7. Sample B, a real Siemens opening in Bengaluru, links only to Siemens's own careers site and
   asks no fee, so the fraud-notice search is skipped. Jobs finds Siemens's listing on siemens
   dot com, Maps finds the office, and News, no fake-offer reports. Its best verdict: nothing
   found contradicts the offer. Never genuine.
8. Back on case A: a drafted summary for the 1930 cybercrime helpline. Drafts only: nothing is
   sent or filed. And the Offer Board downloads as one page.
9. Now an agent: a scripted MCP client, no language model, calls publish verdict. It isn't in
   the agent's tool list; the app refuses it, and logs the refusal as the agent's.
10. Offer Checkpost. Knowledge and Public Interest track. The code is on GitHub.

## Runtime

A beat lasts as long as the longer of its footage and its narration plus its delay plus 1.0 s
(0.4 s before, 0.6 s after). The delay holds the narration back where the screen needs a few
seconds to reach the first words: 1.5 s in beat 3, 1.4 s in 4b, 3.0 s in 5, 1.5 s in 6, 4.0 s
in 7 and 2.0 s in 8 (each chosen against the frames). Where the narration, with its delay,
is longer than the footage, the beat's last frame is held. The final render:

| # | Narration (s) | Delay (s) | Beat (s) | Last frame held (s) | Footage past the narration (s) |
|---|---|---|---|---|---|
| 1 | 8.76 | 0.0 | 9.77 | 0.73 | 0.01 |
| 2 | 14.02 | 0.0 | 15.03 | 0.63 | 0.01 |
| 3 | 19.43 | 1.5 | 23.60 | 0.00 | 1.67 |
| 4 | 9.66 | 0.0 | 10.67 | 1.63 | 0.01 |
| 4b | 11.54 | 1.4 | 14.30 | 0.00 | 0.36 |
| 5 | 15.82 | 3.0 | 22.40 | 0.00 | 2.58 |
| 6 | 12.32 | 1.5 | 17.93 | 0.00 | 3.11 |
| 7 | 20.16 | 4.0 | 26.90 | 0.00 | 1.74 |
| 8 | 9.66 | 2.0 | 15.73 | 0.00 | 3.07 |
| 9 | 11.00 | 0.0 | 12.04 | 0.00 | 0.04 |
| 10 | 4.66 | 0.0 | 5.66 | 0.00 | -0.00 |
| **Total** | **137.03** | **13.4** | **174.03** | **2.99** | **12.60** |

137.03 s of narration + 11 × 1.0 s + 13.4 s of delays + 12.60 s of footage past it =
174.03 s, which is what ffprobe measured, against the target of about 2:40 and the hard
ceiling of 179 s (the cutaway is 14.30 s of it). The render refuses a video of
179 s or more, one with no audio stream, and one whose frame count is more than a frame off
the plan (5,221 frames at 30 fps here).

Three waits on searches were longer than 4 s and each plays in 3.0 s under its label: A's
investigation (6 searches, 76.5 s of capture), C's remaining checks (2, 12.9 s) and B's
investigation (4, 38.8 s). C's investigation (2 searches, 2.5 s) plays in real time. The
capture itself ran 276.8 s.

**Searches:** at most 18 in a live take (the per-case budget of 6 for each of A, C and B; the
app is started with that budget pinned, so a larger one in the environment can't make the guard
wrong). The final take spent 14: A 6, C 2 + 2, B 4. The recorded responses spend 10 on the same
path (A stops decisive at 2), and the cutaway replays 2 of them, spending none. Before its
first search the recorder reads "N searches left" and "used" from the header's Account API
badge, and refuses to go on if the worst case could take this month's usage past `--max-usage`
(default 54). `render.json` keeps the counts before and after: 24 used (226 left) and 38 used
(212 left).

## Claims table

Every feature the README and the submission copy name, with the beat that shows it or the
test that proves it.

| Claim | Shown in beat | Proved by |
|---|---|---|
| Runs locally, on 127.0.0.1 only | 1 (bind badge, strip) | `test_server_http.py::test_binding_anything_but_127_0_0_1_raises_before_anything_is_bound` |
| Live SerpApi, and the searches left this month from the Account API (which can trail the call log for a while) | 1, 3, 10 | `test_providers.py::test_account_returns_only_the_five_counts` |
| The call-log strip: engine, live or replay, cache, milliseconds | 1, 3, 5, 6, 7 | `test_ui_e2e.py::test_the_strip_shows_whole_columns_of_the_newest_calls`, `test_server_http.py::test_the_call_log_grows_while_an_investigation_is_running` |
| Claims extracted as chips, highlighted where they came from | 2, 5, 7 | `test_extract.py::test_sample_spans_are_exact` |
| ₹, K, LPA, lakh and +91 numbers read | 2 (₹38,000/month), 5 (Rs. 42,000) | `test_extract.py::test_pay_formats_are_normalised_to_monthly_inr`, `test_extract.py::test_plus_91_phone_formats_and_masks`, `test_salary.py::test_lpa_is_a_years_pay_whatever_period_follows_it` |
| Romanised Hinglish read | — | `test_extract.py::test_sample_claims_and_text_rules` (the `hinglish-*` samples), `test_salary.py::test_hinglish_pay_forms` |
| Claims confirmed or corrected before any search | 2, 5, 7 (confirm) | `test_planner.py::test_investigate_refuses_unconfirmed_claims_and_searches_nothing`, `test_ui_e2e.py::test_a_correction_marks_out_of_date_everything_that_relied_on_it` |
| The page marks claims the agent confirmed as the agent's | — | `test_ui_e2e.py::test_claims_the_agent_confirmed_say_so_until_a_person_confirms_them` |
| Four text rules: fee, documents early, chat-only interview, task-scam pattern | 2 (fee), 5 (documents, chat-only) | `test_extract.py::test_text_rules_are_the_four_in_table_order`, `test_extract.py::test_task_scam_pattern` |
| The message alone leaves the verdict unverified | 2 | `test_rules.py::test_the_bands_never_certify_an_offer` |
| Official domain from the knowledge graph or the top result; look-alike sender | 3 | `test_checks.py::test_sample_a_knowledge_graph_names_the_domain_and_the_sender_is_a_lookalike`, `test_checks.py::test_an_organic_domain_anchors_a_lookalike` |
| A fee asked and the domain known: `site:` search for the employer's own fraud notice | 3 (the search runs; live it found none that day), 4b (replay: the notice is found) | `test_recordings.py::test_sample_as_fraud_notice_search_quotes_the_fee_phrase_from_hcltechs_own_site` |
| Stops when decisive and shows the searches saved | 4b, 5 ("4 searches were not spent") | `test_planner.py::test_sample_a_stops_decisive_after_two_searches_with_four_saved` |
| An unknown firm gets Maps before listings (R1) | 5 | `test_planner.py::test_sample_c_r1_checks_the_office_first_and_stops_decisive` |
| No fee and every link on the official domain: the fraud-notice search is skipped | 7 | `test_planner.py::test_a_link_on_the_official_domain_skips_the_fraud_notice_without_a_knowledge_graph` |
| No Maps place, no reviews query | 6 (the skipped `scan_office_reviews`) | `test_planner.py::test_a_missing_city_skips_the_office_and_its_reviews` |
| Google Maps Reviews scanned for fees at a found office | 3 (the strip's sixth call, `google_maps_reviews`) | `test_checks.py::test_reviews_that_mention_the_fee`, `test_planner.py::test_the_office_reviews_are_scanned_for_fraud_when_no_listing_matched` |
| A real recruiter phone or email searched next to scam reports | 6 (skipped: the sample's contacts are placeholders) | `test_checks.py::test_contact_footprint_searches_the_exact_contact`, `test_checks.py::test_a_contact_on_a_page_next_to_a_scam_report` |
| A look-alike checked against the official site (`confirm_sender_domain`) | 3 (step 3) | `test_checks.py::test_the_official_site_naming_the_candidate_as_fake_replaces_the_lookalike` |
| Google Jobs: listing match and a pay benchmark ("pay found in n of m listings") | 6, 7 | `test_checks.py::test_sample_c_no_listing_and_pay_far_above_the_city_median`, `test_salary.py::test_pay_found_in_n_of_m_listings` |
| Pay above 2x the median of 3 or more listings is flagged | 6 | `test_salary.py::test_pay_outlier_at_exactly_2x_does_not_fire_and_at_2_01x_does`, `test_salary.py::test_pay_outlier_needs_three_data_points` |
| Google News reports of fake offers, weighted weak | 6, 7 | `test_drafts.py::test_b_verdict_cites_three_greens_notes_the_weak_reports_and_never_says_genuine` |
| India parameters per engine, and a `json_restrictor` on every search | — (the trace shows only each search's query) | `test_checks.py::test_google_builders_set_the_india_parameters_and_the_restrictor`, `test_checks.py::test_each_restrictor_keeps_the_fields_its_reader_uses` |
| Every finding is a named rule fired by a quoted search result, with a link where the result has a page | 3, 4b (evidence cards), 8 (board page) | `test_drafts.py::test_a_search_signal_cites_the_step_that_found_it_never_the_line_that_reused_it` |
| Three bands; never "genuine", "verified" or "safe" | 3, 5, 7 | `test_drafts.py::test_no_draft_calls_an_offer_genuine_or_safe`, `test_rules.py::test_the_bands_never_certify_an_offer` |
| **Run remaining checks** is the person's; the agent never spends past a decisive stop | 6 | `test_planner.py::test_run_remaining_checks_refuses_the_agent_and_changes_nothing`, `test_planner.py::test_the_agent_calling_a_check_directly_never_searches_past_a_decisive_stop` |
| Investigating again checks afresh | — | `test_planner.py::test_investigating_again_checks_afresh_and_a_person_can_still_run_the_remaining_checks` |
| Drafted recruiter reply, one question per red flag | 6 | `test_drafts.py::test_c_reply_asks_about_documents_footprint_office_pay_and_chat_but_never_a_fee` |
| Drafted 1930 / cybercrime.gov.in summary; nothing is sent or filed | 8 | `test_drafts.py::test_a_cybercrime_report_says_what_was_asked_when_by_which_contact_and_the_evidence` |
| Publishing to the Offer Board is a person's click | 4, 9 | `test_server_http.py::test_each_human_only_verb_is_refused_for_the_agent_and_changes_nothing`, `test_ui_e2e.py::test_paste_confirm_investigate_publish_in_chrome` |
| "Copy for WhatsApp" | 4 (the button) | `test_ui_e2e.py::test_paste_confirm_investigate_publish_in_chrome` (copies it), `test_drafts.py::test_a_whatsapp_text_carries_the_label_the_note_and_the_evidence_links` |
| The board downloads as an HTML page | 8 | `test_server_http.py::test_the_board_download_has_only_published_verdicts` |
| "No contradictions found" is offered only on the best band, and a red-flag label never on it | — (beat 4 shows an unverified draft, on which a red-flag label is allowed) | `test_ui_e2e.py::test_the_label_follows_the_draft_verdict_and_a_text_only_case_says_why_it_cannot_post` |
| A post is a snapshot, and publishing is refused if the case changed since it was read | — | `test_ui_e2e.py::test_a_label_chosen_before_the_agent_changed_the_case_never_publishes_the_changed_case`, `test_server_http.py::test_an_agent_cannot_rewrite_a_published_post_or_the_board_download` |
| A case on the board can't change until a person retracts it | — | `test_gate.py::test_a_case_on_the_board_is_not_changed_until_a_person_retracts_it` |
| Retract a verdict; record an outcome (the person's) | 4 (Retract…, Outcome buttons on screen) | `test_gate.py::test_once_retracted_the_case_can_be_corrected_and_published_again`, `test_server_http.py::test_the_same_verbs_run_for_a_person` |
| A forwarded copy is answered from the earlier check at 0 searches | — | `test_gate.py::test_a_forwarded_copy_is_answered_from_the_first_check_at_zero_searches`, `test_planner.py::test_the_fingerprint_ignores_case_spacing_and_legal_suffixes` |
| Per-case budget | 3 ("6 searches spent of 6 · stopped: the case's search budget ran out") | `test_planner.py::test_the_budget_cap_stops_before_the_search_that_would_pass_it` |
| The monthly quota guard (a reserve of 20 searches) | — (it never triggers in the film) | `test_planner.py::test_the_quota_guard_stops_below_the_reserve_without_falling_back` |
| The agent's tools and the person's verbs are separate lists; verbs never registered as tools | 9 | `test_mcp.py::test_the_tool_list_is_the_agents_17_tools_and_no_human_only_verb`, `test_server_http.py::test_the_tool_list_is_the_agents_and_names_no_human_only_verb` |
| The MCP adapter over stdio is always the agent, and a refused verb is logged as the agent's | 9 | `test_mcp.py::test_a_human_only_verb_is_a_tool_error_carrying_the_apps_refusal_logged_as_the_agents` |
| The person is the browser that opened the single-use address; a request can't claim to be them | — | `test_server_http.py::test_the_token_address_works_once_and_the_next_one_signs_the_first_browser_out`, `test_server_http.py::test_a_request_without_the_session_cannot_claim_to_be_the_person` |
| The agent's calls show in the open page's activity log | 9 | `test_ui_e2e.py::test_the_agents_calls_appear_without_costing_the_person_what_they_were_doing` |
| Loopback guards: foreign host, origin, non-JSON calls refused | — | `test_server_http.py::test_a_foreign_or_missing_host_is_forbidden_on_every_request`, `test_server_http.py::test_a_fetch_from_another_site_is_forbidden` |
| Replay mode, dated and marked not live, never making a result up | 4b (the badge and the banner) | `test_ui_e2e.py::test_replay_says_when_its_responses_were_recorded`, `test_providers.py::test_replay_never_makes_up_an_unrecorded_query` |
| Live results cached for 24 hours; `OFFER_CHECKPOST_NO_CACHE` bypasses it | — | `test_providers.py::test_the_cache_lasts_24_hours`, `test_providers.py::test_no_cache_skips_the_cache_sends_no_cache_and_refreshes_it` |
| The key is never logged, shown or kept in an error | 1 (no key on screen) | `test_key_hygiene.py::test_a_401_never_carries_the_key`, `test_no_secrets.py::test_the_tree_holds_nothing_key_shaped` |
| Recorded responses are trimmed and identify nobody | — | `test_scrub.py::test_the_whitelist_covers_every_engine_a_check_reads`, `test_no_secrets.py::test_recordings_and_fixtures_identify_nobody` |
| The candidate's own details are never sent | — | `test_checks.py::test_contact_footprint_searches_the_exact_contact`, `test_extract.py::test_sample_contacts_and_links_are_all_synthetic_but_a_real_offers_careers_link` |
| `investigate <file>` from the command line, and `--as agent` | — | `test_cli.py::test_sample_a_prints_its_trace_two_searches_and_four_saved`, `test_cli.py::test_sample_c_as_the_agent_makes_every_call_the_agents` |
| macOS, Linux and Windows commands | — | Neither shown nor tested: the Windows commands in the README are not run by any test. |
