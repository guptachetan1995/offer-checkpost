# Sample offers

Fictional job-offer messages, written the way they arrive on WhatsApp, Telegram or email. The
demo uses them, and so do the tests: `tests/test_extract.py` has one row per file with the
claims and text rules that file should produce. A new sample needs a new row there, or the
tests fail.

## The three demo samples

The demo runs these three, and replay mode answers them with no key: their SerpApi responses
were recorded with a real key on 29 and 30 September 2026 and are in `recordings/`. The messages are
fictional. Two of the employers are real, on purpose.

| File | What it is |
|---|---|
| `a.txt` | **Sample A.** A scam that impersonates **HCLTech**: work-from-home data entry at ₹38,000/month, a "refundable registration fee" of ₹2,499, and a recruiter on the look-alike `hcltech-careers.example`. |
| `b.txt` | **Sample B.** A real opening: **Siemens**'s Application Support Engineer role in Bengaluru (Req ID 509009), open when it was recorded. Its one link is that listing on Siemens's own careers site; no fee is asked. |
| `c.txt` | **Sample C.** An invented firm, Kavrellon Support Services, with no web presence: customer support in Indore at ₹42,000/month for freshers, a Telegram-only interview, and an Aadhaar and bank-passbook photo asked for up front. No fee. |

### Why these names

- **HCLTech, in Sample A, is the victim, not the sender.** Scam offers borrow the names of large
  employers, and the strongest evidence against one is the employer's own words. HCLTech's own
  site carries a recruitment-fraud warning that Google indexes, "will never ask for any payment
  of money", so the check quotes the employer contradicting the message, from the employer's own
  domain, with the date SerpApi returned it. The message, its sender and the sender's
  `.example` domain are invented; nothing in it comes from HCLTech.
- **Siemens, in Sample B, is a real opening**, because "No contradictions found" only means
  something against a real listing and a real office. The message is written for the demo, but
  it invents nothing about Siemens: it names no pay figure and no recruiter, and its only link
  is the listing on the employer's official domain.
- **Kavrellon Support Services, in Sample C, is invented**, and no business by that name
  turned up: a Google search found nothing for it, and the recorded SerpApi searches for it
  (Search, Maps, News) came back empty. An invented firm keeps red flags off any real small
  business.
- **Indore** is Sample C's city because Google Jobs showed pay on enough of its
  customer-support listings for the pay benchmark (6 of 10 when it was recorded).
- `a-forwarded.txt` and `lookalike-kit-charges.txt` reuse Sample A's employer. The first makes
  Sample A's searches, so replay answers it; the second, from `hlctech.example` (two letters
  swapped) in Gurugram, needs a key.

The tests keep synthetic stand-ins of the three, with a made-up employer in each, in
`tests/fixtures/offers/`: the fake provider's fixtures answer those, never these.

## The variants

| File | What it tests |
|---|---|
| `a-forwarded.txt` | Sample A forwarded again, with WhatsApp's bold markers and different spacing: the same claims, for de-duplication |
| `hinglish-registration-fee.txt` | "registration ke liye ₹1,999 bhejo"; pay written as `18k pm` |
| `hinglish-security-deposit.txt` | A laptop security deposit, Aadhaar and PAN photos asked for, an interview on WhatsApp; no city |
| `hinglish-joining-fees-rupees.txt` | "Joining fees 2500 rupees"; pay as "15,000 se 20,000 tak har mahine" |
| `hinglish-telegram-processing.txt` | A processing charge, a Telegram-only interview, pay as `12k-15k per month` |
| `task-per-like.txt` | Paid YouTube likes; no company named |
| `task-prepaid-deposit.txt` | "Prepaid tasks" with a deposit; no company named |
| `task-hinglish-reviews.txt` | Paid hotel reviews, "Roz ₹3000 tak kamao" |
| `task-daily-earning.txt` | A daily-earning promise for simple online tasks |
| `task-hinglish-no-claims.txt` | A task scam that names nothing checkable at all |
| `no-company-training-fee.txt` | A training fee and a WhatsApp interview, with no company named |
| `fee-without-amount.txt` | A registration fee with no amount (not enough for the fee rule); pay as `3.5 lakh per annum` |
| `lookalike-kit-charges.txt` | Sample A's employer again, from a domain with two letters swapped; kit charges |
| `otp-request.txt` | An OTP asked for before any interview |
| `walk-in-genuine-shape.txt` | A walk-in drive with no red flags: bring your Aadhaar to the interview, no fee |
| `training-fee-bangalore.txt` | A training fee; `4.5 LPA`; "Bangalore" read as Bengaluru |
| `bank-details-email.txt` | Bank account number, IFSC and PAN asked for by email |
| `internship-stipend.txt` | "Pay ₹999 for registration" for an internship; `Rs. 12000/- per month` |

## No real people, no real contacts

- **Phone numbers are masked.** Every number keeps only its first digit, 6 to 9 (the digits an
  Indian mobile number can start with), and writes the other nine as the letter `X`:
  `+91 9XXXX XXXXX`. A number with letters in it can't be dialled and belongs to no subscriber.
  The samples spell it several ways (`+91-8XXXX-XXXXX`, `+917XXXXXXXXX`, `09XXXXXXXXX`,
  `(+91) 6XXXX XXXXX`) so the tests cover each `+91` format. The extractor marks a masked
  number `synthetic`, and synthetic contacts are never searched.
- **Every email address and link uses the reserved `.example` domain** (RFC 2606), except
  Sample B's link to the real listing on Siemens's own careers site. Nobody can register a
  name under `.example`, so no mail or visit reaches anyone. These contacts are `synthetic`
  too.
- **Company names are invented**, apart from HCLTech and Siemens in the demo samples and the
  two variants of Sample A. Any resemblance to a real business is a coincidence. No recruiter
  is named.
- The Hinglish is romanised, as it's usually typed. The extractor doesn't read Devanagari.
