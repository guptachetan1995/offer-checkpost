# Live notes: what the live searches did on 30 September 2026

The README states the rule: live results move with Google's ranking, so a live run can differ
from the recordings and from the demo video. This file keeps the dated detail behind that
sentence: which takes returned what, how the notice search's wording was chosen, what the app
does when that search says nothing, and the failure mode it does not yet handle. Nothing here
changes what the app claims: an employer whose notice is not found is never cleared, and
nothing is made up.

Times are IST. "The notice search" is the `site:` search of the employer's own domain that
looks for its recruitment-fraud notice (`read_fraud_notice` in `src/offer_checkpost/checks.py`).

## Sample A's notice search, take by take

Replay serves the responses recorded on 30 September, where the notice search quotes
HCLTech's own notice and the case stops decisive at 2 searches. Live, the same query did not
answer the same way twice that day:

| When | Query | What came back | The case |
|---|---|---|---|
| 29 Sep (video filmed then) | `(fraud OR fake OR scam OR beware) (recruitment OR job OR offer OR hiring)` | No HCLTech notice | Nothing decisive: all 6 searches spent, Unverified |
| 30 Sep 13:45 | current query | Ten results, none from hcltech.com: Google apparently dropped the `site:` restriction and answered with generic fraud pages | The app then said "no recruitment-fraud notice on hcltech.com", an absence the search had not earned. This take is why an answer with no page from the domain is now inconclusive. |
| 30 Sep 15:22 | current query | Ten pages from hcltech.com (annual-report and statutory-filing PDFs), none a recruitment page | No notice quoted, not retried (see the third failure mode below): Unverified after 6 searches |
| 30 Sep 15:50 | current query | The notice quoted | Decisive |
| 30 Sep 16:00 | current query | The notice quoted | Decisive at 2 searches, High risk, 4 not spent, as replay does. This is the demo video's take, and the forwarded copy after it is answered at 0 searches. |

Sample B (Siemens) moved too. The demo video's take at 16:01 found the listing and ended "No
contradictions found" after 4 searches, as replay does. At 15:53 Google Jobs returned ten
listings and none by Siemens for this role, so that case stayed Unverified (one red signal,
no matching listing, against the green office on Maps).

## How the notice search's wording was chosen

The query is `site:<domain> (recruitment OR hiring) (fraud OR scam OR fake) never (fee OR money
OR payment)`. Each OR group stays in parentheses so `site:` scopes the whole query: a probe
with the top-level OR unparenthesised wandered off the domain. `never` next to a fee word put
an employer's own "we never ask for any payment" notice first in the 30 September probes.

Measured on 30 September 2026 on four employers (HCLTech, Infosys, TCS, Wipro), the previous
phrasing found no notice for any of them. This one, the best of five phrasings tried on those
same four, found HCLTech's and Wipro's fee statements and Infosys's notice, not TCS's. It was
chosen on the employers it was scored on, and no test isolates `never` as the cause. That is
one measurement on one day. A notice found today may not be found tomorrow, and an employer
whose notice is not found stays unverified, never cleared.

## When the notice search says nothing: the retry

- A notice search with no result on the employer's domain is **inconclusive**:
  `read_fraud_notice` returns no signal with `inconclusive: true` in its facts, and the trace,
  the evidence panel and the drafts say "no page from <domain>, so it says nothing about a
  notice".
- The planner then runs it **once more** with `wording: "broad"`,
  `site:<domain> ("recruitment fraud" OR "fake job offers" OR "recruitment scams" OR
  "fraudulent")`, the notice's usual titles, and says why in the trace.
- The retry is a numbered search that counts against the case's budget of 6. A second
  inconclusive search stays inconclusive.
- A search that did return pages from the domain but none that is a recruitment-fraud notice
  is a finding ("N pages from <domain> came back, none is a recruitment-fraud notice"), and is
  not retried.
- The retry is covered by fake-provider and browser tests (`tests/test_planner.py`,
  `tests/test_checks.py`). It has not yet fired on a live response, and no replay shows it,
  since replay would then serve invented SerpApi content.
- The broad wording was probed live once, on the morning of 30 September: it returned ten
  results on the domain and a notice for HCLTech and Infosys, and none for TCS and Wipro.
  Google's ranking moves between calls.

## The third failure mode (open)

At 15:22 the query returned ten pages from hcltech.com, none a recruitment page. The app reads
that as "none is a recruitment-fraud notice", does not retry it, and the case stays unverified
after 6 searches. A page from the domain that is not about recruitment is not treated as
inconclusive yet. The band is still honest (Unverified, never cleared), but the search budget
is spent without the retry that might have found the notice.

## What the recorder does with a live take

`demo/record_demo.py` reads what Sample A's live search found, and the narration says it: the
notice found by the first search (`decisive`), found by the retry after a first search that
returned nothing from the domain (`retried`), or not found (`unverified`,
`unverified_retried`). Only for a miss, the live take also records a short replay cutaway of
Sample A on the recorded responses, which hold the notice, and the render splices it in under
its own label, dated. Sample B's words follow what Google Jobs returned in the same way
(`clean` or `unverified`). The demo video linked from the submission is the 30 September
16:00 take: `decisive`, no cutaway, no replay in it.

## If your live run differs

- An **Unverified** band after six searches is correct behaviour when no notice is quoted.
  When the notice search says nothing (no page from the employer's domain, in either
  wording), the trace and the drafts say that, not "no notice".
- `--provider replay` reproduces the recorded traces exactly: Sample A stops at 2 searches
  with the notice quoted.

## Recording the demo video

`demo/record_demo.py` records the app running on 127.0.0.1 in Google Chrome, headless, with
Playwright, then renders the mp4 with ffmpeg and a narration spoken by macOS `say`:

```bash
make demo-record                         # live SerpApi (at most 18 searches), then out/offer-checkpost-demo.mp4
make demo-record DEMO_PROVIDER=replay    # the recorded responses: no search spent
make demo-render                         # the saved capture again, after a narration change: no search
```

Each sentence of the narration starts when the page reaches what it says, so the capture is
paced by the narration and the render lays each clip where the recorder cued it. A caption goes
up at the moment of the on-screen change it describes and comes down when the page moves on.
The render leaves out stretches where the screen did not change and nobody was speaking, and
refuses a capture whose page didn't show what a beat's narration says. It also refuses
narration rewritten since the capture that names text the capture never checked, unless
`--allow-unchecked` (`make demo-render ALLOW_UNCHECKED=1`) says a person has looked at the
footage; `render.json` lists those claims.
