# Sample offers

Fictional job-offer messages, written the way they arrive on WhatsApp, Telegram or email. The
demo uses them, and so do the tests: `tests/test_extract.py` has one row per file with the
claims and text rules that file should produce. A new sample needs a new row there, or the
tests fail.

## The three demo samples

| File | What it is |
|---|---|
| `a.txt` | **Sample A.** Impersonates a large employer: work-from-home data entry at ₹38,000/month, a "refundable registration fee" of ₹2,499, and a recruiter on a look-alike domain. |
| `b.txt` | **Sample B.** A real opening: the recruiter writes from the employer's own domain, the office is named, and no fee is asked. |
| `c.txt` | **Sample C.** An invented firm with no web presence: customer support at ₹42,000/month for freshers, a Telegram-only interview, and an Aadhaar and bank-passbook photo asked for up front. No fee. |

### Placeholders

Three names in these samples are stand-ins. A one-off live SerpApi check replaces them before
anything is recorded or filmed:

- **`Brand`** (Sample A's employer, with its look-alike domains `brand-careers.example` and
  `brnad.example`). The real employer must have a recruitment-fraud notice on its own site that
  Google indexes and that says it never charges candidates, so the evidence is the employer's
  own words.
- **`Contoso`** (Sample B's employer, on `contoso.example`). The real one needs a current
  opening on Google Jobs with an apply link on its own domain, and an office on Google Maps.
- **`Indore`** (Sample C's city). Indore, Pune or Jaipur, whichever has the most Google Jobs
  listings that show pay. Sample C's firm, **Zorvanta Support Services**, is kept only if the
  same check finds no business with that name.

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
- **Every email address and link uses the reserved `.example` domain** (RFC 2606). Nobody can
  register a name under it, so no mail or visit reaches anyone. These contacts are `synthetic`
  too.
- **Company names are invented**, apart from the three placeholders above. Any resemblance to
  a real business is a coincidence. No recruiter is named.
- The Hinglish is romanised, as it's usually typed. The extractor doesn't read Devanagari.
