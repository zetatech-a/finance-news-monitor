# Loan-news golden sample

Source: `reports/_candidates/2026-09-17_candidates.csv` at
`e67e3a740b0004e56a0087aa34edfeaffc6264ae`. Titles, snippets, URLs, recorded
scores, rounded probabilities and keep flags are copied verbatim. No bodies or
generated summaries are substituted. `event` and `relevant` are manually
reviewed labels based on those titles/snippets; production keep flags are not
ground-truth labels.

37 candidates: 33 relevant, 4 irrelevant. Eight relevant events:

| Event | Rows | Rationale |
| --- | ---: | --- |
| fund_purchase | 4 | Same 72% fund purchase / 16% loan-sector participation report |
| civil_policy | 1 | Civil organization publishing 15 policy proposals |
| seoul_crackdown | 14 | Seoul's September–October holiday market-lending crackdown |
| loan_supply | 1 | Preferred lenders' supply concentration |
| credit_access | 1 | Low-credit borrowers losing access to finance |
| bucheon_sentence | 5 | Same Bucheon court sentence involving coerced videos |
| insurance_capital | 4 | June-end industry K-ICS 215.2% announcement |
| pg_security | 3 | Same regulator meeting on PG security |

Negative controls: historical household finance and three North Korea
politics/damages stories, rather than current domestic financial-sector news.
The six synthetic homonym tests (film, entertainer, island, public lease, etc.)
are separate and are **not** represented as observed Naver search results.

This targeted sample overrepresents known failures; its precision/recall is not
an estimate of all collected news. Candidate CSVs only retain stage-2 inputs,
not raw search output, publication timestamps, query provenance or first-stage
duplicate metadata. Collection recall cannot be estimated from this sample.

The test requires complete same-event retention for fund purchases, the Seoul
crackdown and both other-sector comparison groups, and prohibits any mixed
event cluster. It also requires visible, independently represented loan supply,
credit access, fund purchases and civil policy. The newly recovered court
stories still have conservative splits; the evaluator reports pair recall so
that purity alone cannot conceal fragmentation.

## Review follow-up sample

`other_sectors_review.json` contains 18 additional manually checked candidates,
6 per event, from the same base revision: Jeju Bank's call-time transfer
protection launch (09-15), Kyobo Life's Lifeplanet absorption merger (09-16),
and the fourth KB citizen anti-phishing awards (09-17). Headlines/snippets,
URLs and recorded fields are unchanged. Selection takes the first six kept
rows mentioning the named actor/event, excluding the KB news-roundup headline.
These provide a small independent fragmentation check, not representative
precision estimates for the whole three-day corpus. Publication dates are
unavailable; the dates identify candidate files, not inferred event dates.

`scripts/evaluate_cluster_review.py` evaluates both the original 33 relevant
labelled articles and this 18-row sample with identical retained cohorts at
base, first-PR and revised versions. No production keep decision is used as an
event label. Synthetic review tests are in `test_issue_cluster_review.py`.
