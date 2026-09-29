# Recorded search content

Each JSON file in this folder, `<sample>/<engine>-<hash>.json`, is a response SerpApi returned,
with a real SerpApi key, to a Google search (`google`, `google_jobs`, `google_maps`,
`google_news` or `google_maps_reviews`) made with exactly the params the app sends, and written
by `python -m offer_checkpost record`. A response SerpApi had returned for the same search in
the previous 24 hours comes from the local cache, dated when SerpApi returned it.

- **Third-party material.** The search content is Google's results as SerpApi returned them.
  It is not covered by this repository's MIT licence, which covers the project's own code and
  documentation only.
- **Trimmed and scrubbed.** Each response keeps only the fields the app reads. Results that
  tend to name people are dropped: LinkedIn profiles; everything on Facebook, Instagram,
  X/Twitter and Truecaller, brand pages included; everything on a video site (YouTube, Vimeo,
  Dailymotion), whose descriptions name the speakers; and every news headline without both a
  fraud term and a job word, which the app never reads. No reviewer's name or profile is kept,
  phone numbers and email addresses are masked, and anything key-shaped is removed.
- **How these files were fetched.** On 29 September 2026 the app's own live provider ran each
  search, a few at a time, into its local cache, and `record` then wrote every file from that
  cache without a new search. The `site:` search for HCLTech's fraud notice took 66 s, longer
  than the app then waited for SerpApi, so it was sent with a longer client timeout, and with
  `no_cache`. Later that day `record` ran Sample A's remaining checks live itself, taking its
  first two searches from the local cache. No kept title, snippet or headline in these files
  names a person.
- **Dated.** `recordedAt` is when SerpApi returned the response, in IST. Search results change:
  a recording shows what a search found on that date, not today.
- **Why it is here.** Only so the app can replay the demo without a SerpApi key (replay mode,
  which says it is not live), and for the tests. Replay never makes a response up: a search
  that was not recorded fails.
