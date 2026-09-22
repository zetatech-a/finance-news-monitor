# PR #85 — latest correctness review of 3de826a

Starting revision: `3de826ac4f7f31a53a6b19fa16e8eb8a8da8c715`.
Branch: `fix/loan-news-clustering-recall`. No new branch/PR, merge or thread resolution.
All four latest Codex inline comments were read directly. All four reproduced
before editing production code: **12 failed, 13 passed** in 25 new tests.

## 1. Metric subject aliases

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4033083920): valid.
Raw `kb손보` and `kb손해보험` subject sets are disjoint, so the conflict veto
splits same-company wire variants before the metric shortcut can apply.

The 09-15/16/17 candidate text contains KB손보 (6 occurrences), KB손해보험 (5),
DB손보 (2) and DB손해보험 (2). Counts are case-sensitive text occurrences in
both title/snippet fields, not counts of independently labelled events. The
only new canonical mappings are exact `kb손보 → kb손해보험` and
`db손보 → db손해보험`, applied after extraction in the metric-only path.
General entity extraction is untouched; no suffix-wide replacement or registry.

Tests cover same KB/DB entities, KB versus DB, 삼성생명 versus 한화생명,
unverified 가온손보 versus 가온손해보험, missing/ambiguous subjects and aliases
with low-value formatting. Unknown subjects still cannot authorize the metric
shortcut. Both positive alias cases failed before, pass after.

## 2. Strict admission needs headline evidence

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4033083924): valid.
`feature.issue_terms` includes snippets. Adding the same background sentence
about illegal lending to A, B or C of the existing non-lending bridge changes
one group into two even though all headlines and sector tags stay unchanged.

Strict scope now calculates title-only issue terms from the existing normalized
title once per article. A new dataclass field is unnecessary: this scope check
is already outside the pair loop. The combined `issue_terms` remains intact
for ordinary similarity, as do the existing explicit loan-sector and
headline-guarded debt-purchase signals. No tagger behavior changes.

All three background-insertion variants failed before and pass after. Positive
scope controls retain strict mode for headline 불법사금융, 불법대부, 불법추심,
대부광고 and 대출광고. Existing loan bridge, fund/policy separation and same-wire
golden coverage also pass.

## 3. Reproducibility after squash

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4033083931): valid for the squash/fresh-clone scenario.
The current PR worktree still has its unsquashed objects, so this is not an
assertion that `git show FIRST` fails in the current worktree. It fails when
those unreferenced objects are absent, as the isolated clone demonstrates.

Chosen fix: remove the first-PR executable variants/ablations and keep their
existing result artifacts/documentation unchanged. The default executable
compares the durable main ancestor `e67e3a7` (verified ancestor of current HEAD)
with current code. No historical production-source snapshot is committed.
This is smaller than retaining duplicated production implementations and keeps
the requested main-versus-current comparison executable after squash.

An explicit optional `--compare-revision` supports audits when the caller has
that object locally; it is not a default dependency. Output names are `base`,
`revised`, optional `comparison`/`comparison_revision`. It does not silently
substitute stored numbers for executions. Historical `review-replay.json` and
its first-PR ablations are frozen; new runs must use a new output filename.

The regression test constructs a two-commit squash simulation in a temporary
Git repository: the actual main baseline module, then the current source/tool,
real labelled fixtures and small candidate CSVs. A `git clone --no-local`
creates independent reachable history. Both `1b4b732` and `3de826a` are verified
absent with `git cat-file`. Only the baseline object ID is remapped to the
synthetic parent ID; its implementation is copied from the actual main
ancestor, not mocked. Before the fix, the CLI failed at `snapshot(FIRST)` with
Git exit 128. Afterwards, the CLI completes all three smoke CSVs and both real
labelled fixtures. This test passes on Windows and Linux/Python 3.11.
The full three-day real candidate replay is a separate validation below.

Full-history clone command (default, no transient PR objects required):

```sh
python -m scripts.evaluate_cluster_review --output replay-current.json
```

Follow-up audit performed locally for this change:

```sh
python -m scripts.evaluate_cluster_review --compare-revision 3de826ac4f7f31a53a6b19fa16e8eb8a8da8c715 --output .venv/latest-review-replay.json
```

A shallow clone must obtain the main baseline history before this historical
comparison. Candidate CSVs are subject to repository retention: future runs
after their deletion must restore the cited 09-15/16/17 inputs from history.
Neither limitation is an unsquashed-PR-object dependency.

## 4. Safety veto ordering

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4033083935): valid.
The low-value branch returns before enforcement/metric-subject guards.
Enforcement versus victim-support headlines, and 삼성생명 versus 한화생명
metric headlines, bypass the guards with 단신, 금융 브리핑 or 일정 prefixes.

The two existing vetoes now run before low-value special handling. The low-value
branch and thresholds are unchanged. Empty-title and exact normalized-title
checks retain their previous position: identical normalized headlines have the
same headline event evidence and extracted metric identity. Six negative
prefix cases failed before and pass after; three valid low-value enforcement
pairs and a same-company alias pair continue to cluster.

## Validation

- New regressions before fix: **12 failed, 13 passed**.
- New regressions plus existing review and loan golden: **57 passed**.
- All issue-clustering, loan golden, latest/previous review, fresh-clone,
  relevance/text matcher and Phase 9A tests: **128 passed** on Windows.
  Five existing NumPy/joblib deprecation warnings; no failures.
- Full Python 3.11/Linux suite: **770 passed, 1 skipped** in 29.61 seconds,
  using `python -m pytest tests/ -q -p no:cacheprovider` in the existing Docker
  image with `--network none`, read-only source/Git mounts. Docker was started
  after an initial daemon-unavailable attempt; that attempt ran no tests.
- `git diff --check`: passed; complete source/test/tool/document diff reviewed.
- Main baseline and comparison modules are actually executed on the same
  recorded keep cohort. No fetching, model inference or report regeneration.

`latest-review-replay.json` is a compact measured result: top-10/title listings
are omitted, while all metrics, sector counts and pair changes are retained.
`comparison` means 3de826a; `revised` means this follow-up. The main `base`
results reproduce the prior historical main-baseline numbers.

| Date | Kept | Cluster count before → after | Largest | >=50 clusters | Loan representatives | Newly merged / split pairs |
| --- | ---: | --- | --- | --- | --- | --- |
| 2026-09-15 | 652 | 333 → 333 | 90 → 90 | 2 → 2 | 10 → 10 | 0 / 0 |
| 2026-09-16 | 662 | 320 → 320 | 118 → 118 | 1 → 1 | 6 → 6 | 0 / 0 |
| 2026-09-17 | 972 | 401 → 401 | 162 → 162 | 3 → 3 | 4 → 4 | 0 / 0 |

Every sector's representative count is unchanged, as are representative
titles and top-10 memberships. No unexplained corpus change needs attribution.
These boundary bugs are exposed by the new synthetic regressions even though
the fixed three-day corpus has no changed final pair assignments.

| Fixed labelled cohort | Precision before → after | Recall before → after | Correct / predicted / expected |
| --- | --- | --- | --- |
| 33 relevant golden articles | 1.0000 → 1.0000 | 0.922414 → 0.922414 | 107 / 107 / 116 |
| 18 other-sector articles | 1.0000 → 1.0000 | 0.888889 → 0.888889 | 40 / 40 / 45 |

## Scope and remaining risks

No representative ranking, threshold, query/fetch, ML/Gemini/report/email,
production dependency, general entity extraction or macro/digital fingerprint
changes. Original fund-wire recall, independent policy cards, authority-name
checks, spaced metric matching and safe-alias noise regressions all pass.

Known broad macro/digital fingerprints and conservative court/Jeju splits
remain outside scope. Two explicit insurer aliases are not comprehensive
entity recognition. Existing loan-sector tags remain explicit strict signals;
this change removes snippet-only issue-term scope switching, not upstream
sector tagging. No production API-equivalent run or collection-recall estimate
is claimed. Human review should confirm these narrow choices and the linked
regressions; review threads remain unresolved for that review.


## Follow-up: three correctness findings on b81261c

Baseline: `b81261c4fb579e006fdc51af61ccec92c806bac0`. This section records the
next review round; the earlier sections and `latest-review-replay.json` remain
historical results for the review of 3de826a. No historical artifact is overwritten.
The interrupted session's two source edits and new test file were preserved;
continuation confirmed local and PR HEAD still matched the baseline. Production
code did not need further edits during continuation.

### Findings and fixes

1. [Percentage-point suffix](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4042942207): reproduced.
   The old negative lookahead rejected only directly adjacent Latin `p`.
   `%포인트`, `% 포인트` and `% p` yielded a false level and could trigger the
   same-subject metric shortcut. The regex now rejects optional whitespace
   followed by `p` or `포인트`; `%p` remains rejected. Actual `20%`, `K-ICS 비율
   215.2%` and `킥스 비율 215.2%` still parse, including a headline containing
   both a real level and a separate change. The clustering fixture explicitly
   verifies that removing its metric evidence prevents merging, so independent
   title similarity cannot mask the bug.
2. [Aggregate subjects](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4042942211): reproduced.
   Raw `보험사`/`보험회사` sets incorrectly activated the subject-conflict veto;
   the supplied same-event pair formed two clusters. A separate metric-only
   aggregate mapping canonicalizes **only `보험회사 → 보험사`**. Life/non-life,
   banks/savings banks, named companies/industry aggregates and different
   insurers remain distinct. Existing KB/DB company mappings are unchanged.
3. [Spaced loan alias boundary](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4042942215): reproduced through relevance.
   `불법 대부도 토지거래` and `미등록 대부도 숙박업체` each gained 6 hard-anchor
   points, a domain anchor and a keep decision at candidate probability 0.8.
   Only aliases `불법 대부` and `미등록 대부` now use a right token boundary:
   an immediately following Korean syllable, Latin letter or digit prevents
   that alias match. Spaces, punctuation and end of text remain valid. Other
   phrase/canonical-term semantics are unchanged; no string-specific excludes.

The first boundary-only patch exposed a real recall regression: three golden
court-case articles lost `미등록대부`, fell from score 11 to 5 and missed the
existing gray-zone threshold 6. Merely retaining the separate `대부업` anchor
was insufficient. Explicit `불법 대부업`, `미등록 대부업`, `불법 대부중개업` and
`미등록 대부중개업` aliases preserve the financial compounds, their 업자/업체
forms and Korean particles without reopening the ambiguous short 대부 prefix.
The three-day corpus contains 업/업자/업체 forms. Tests cover both prefixes
with 업, 업체, 업권, 업계, 업자 and 중개업 at probability 0.5. Final golden
recall is restored; public-lease/island noise and 대부abc/대부123 remain rejected.

### Regression validation

- 51 new parameterized cases in `tests/test_metric_alias_review.py`.
  Initial 39-case run against unmodified production code: **14 failed, 25 passed**.
  Final 51-case matrix against the original b81261c modules: **14 failed, 37 passed**.
  Thus the fixes address failing behaviors, not tests that already passed.
- New review plus loan golden: **64 passed**. Continuation reran the broader
  issue-cluster/text-matcher/relevance/prefilter/golden/replay-reproducibility
  selection: **468 passed, 1 skipped**, 10 NumPy/joblib deprecation warnings.
- Continuation reran `python -m pytest tests/ -q -p no:cacheprovider` in the
  existing Linux/Python 3.11 Docker image, network disabled and source/Git
  mounted read-only: **821 passed, 1 skipped** (18.88 seconds).
- `git diff --check` passed. No thresholds, rankings, query/fetch, ML, Gemini,
  report/email, general entity extraction or clustering architecture changed.

### Full before/after comparison

The prior session captured candidate relevance/tagging/clustering before and after in ignored
`.venv/round3-before.json` and `.venv/round3-after.json`, using
`.venv/capture_round3.py`. Continuation parsed and compared both full objects:
**identical**, including golden and other-sector fixtures. The capture's
`revision` field denotes the baseline in both files, not the after source hash.
These temporary multi-megabyte files are not committed.

All 3,327 candidate rows (985 / 1,041 / 1,301) have unchanged relevance scores,
hard/soft/negative matched-term lists and domain-anchor booleans. Fixed and
rescored retained URL sequences, sectors, complete cluster memberships and
pair sets are identical. New merge/split counts are **0 / 0** in all six runs.
To cover representatives beyond the stored top ten, continuation re-prepared
both cohorts, verified their retained URL order/sector tags against the captures,
and applied each revision's actual `_representative_score` to every stored
cluster. Every representative title and URL is identical (333/320/401 fixed;
333/322/407 rescored). No changed pair or relevance evidence needs attribution.

Every cell below is **before = after**, not a comparison between fixed and
rescored policies. Fixed uses recorded keep/score; rescored reuses recorded
probabilities but recalculates rules. Publication times/query provenance are
absent from candidates; this is offline candidate replay, not a live API run.

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest (both) | >=50 (both) | Loan reps fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 2026-09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 2026-09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 2026-09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

Fixed sector representative counts, unchanged before/after (rescored differs
only in the loan counts shown above):

| Sector | 09-15 | 09-16 | 09-17 |
| --- | ---: | ---: | ---: |
| IB·자본시장 | 1 | 4 | 1 |
| 감독·제재 | 8 | 2 | 18 |
| 거시·시장 | 22 | 41 | 45 |
| 기타 | 122 | 108 | 129 |
| 대부 | 10 | 6 | 4 |
| 디지털자산 | 79 | 63 | 77 |
| 보험 | 2 | 9 | 30 |
| 상호금융 | 9 | 4 | 6 |
| 여전 | 10 | 13 | 10 |
| 은행 | 28 | 36 | 35 |
| 입법·정책 | 23 | 27 | 26 |
| 자산운용·연기금 | 4 | 3 | 3 |
| 저축은행 | 14 | 2 | 8 |
| 증권(브로커리지/리테일) | 1 | 1 | 1 |
| 핀테크·플랫폼 | 0 | 1 | 8 |

Golden fixed-relevant pair precision/recall stays **1.0000 / 0.922414**
(107 correct / 107 predicted / 116 expected); other-sector labels stay
**1.0000 / 0.888889** (40 / 40 / 45). End-to-end golden keeps all 33 relevant
articles and rejects all four noise articles. The three corrections affect
new edge-case fixtures while preserving every measured existing corpus result.

### Remaining limits

The prior broad macro/digital fingerprints and conservative court/Jeju splits
remain out of scope. Metric aliases deliberately remain small; this is not a
general entity or numeric parser. Explicit financial compound aliases retain
existing phrase semantics; the boundary change is limited to the two ambiguous
short spaced aliases. Human review should check these narrow semantics and the
new regression matrix before merging. No review threads were resolved and no
merge was performed. Default replay still requires no unsquashed PR object;
this follow-up's b81261c comparison is historical audit provenance, not a new
executable default dependency.


## Follow-up: four correctness findings on 3b6e598

Starting local/PR HEAD: `3b6e59867532833608725b1279073e5cdb1e05d7`, clean
worktree on the same PR branch. All four actual GitHub comments were read;
all reproduced before production edits: **14 failed, 28 passed** in the initial
42-case regression run. Earlier sections/artifacts remain historical results.

### Reporting-period conflict

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043300554).
Equal canonical company and metric/value previously bypassed conflicting
quarters/years. The new metric-local period feature reads explicit year and
ratio snapshot end-month from the headline prefix before its first reported
metric. Observed forms include 1–4분기, 상/하반기 and month-end (with/without a
space). The three-day corpus uses **2분기 / 상반기 / 6월말** for the same ratio
release; these map to month 6, not conflicting periods. Relative years are not
inferred. Missing or internally ambiguous dimensions are unknown, not conflicts.

A narrow veto requires the same singleton subject, an intersecting exact
metric/value and different known years or known end-months. It runs before
low-value/fingerprint/similarity paths and at cluster admission, so a missing-
period bridge cannot rejoin conflicting endpoints. Other-sector single-link
ordering, representative ranking and thresholds are unchanged. Tests cover
quarters, years, half-years, month-end, equivalent periods, one-sided missing
years/periods, ambiguous periods, low-value formatting, later background
comparisons and all six input orders of a missing-period bridge.

### Particles after the two spaced lending aliases

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043300565).
The old `right_token` excluded every following Hangul character, including
complete case/topic particles, losing the sole hard/domain anchor and dropping
relevant candidates. Only `불법 대부` and `미등록 대부` now use `korean_particle`:
optional **을/를, 이/가, 은/는, 의, 에/에서, 와/과, 로/으로**, followed by a token
boundary. This finite grammatical set covers the review's required forms;
no such attached-particle examples were found in the three-day candidate text.
`도` stays excluded because of the 대부도 collision. 가입/가능, Latin/digit
continuations and unsupported stacked suffixes are not accepted by partially
matching a particle. Explicit 업/중개업 compound aliases stay intact. The old
`right_token` mode remains available for faithful historical replay.

Tests check the four supplied sentences through matcher, hard terms, domain
anchor and candidate-hybrid keep at probability 0.8; every allowed suffix also
has a full-boundary positive and Korean/Latin-continuation negative. Previous
island/public-lease noise, financial compounds and golden recall tests pass.

### Exact life/non-life industry synonyms

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043300572).
Entity extraction returned generic 생명보험/손해보험 before the aggregate alias
fallback could run. Both extraction paths now use the same metric-local exact
canonicalizer: 보험회사→보험사, 생명보험→생보사, 손해보험→손보사, alongside the
unchanged KB/DB company aliases. No suffix-wide substitutions or general entity
extraction changes. Tests retain distinctions between insurance/life/non-life,
banks/savings banks, and named companies versus aggregate labels (including
삼성생명, 교보생명, KB손해보험 and DB손해보험).

### Revision-faithful matcher preparation

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043300579).
The bug was reproduced on the real 09-15 cohort: the same BASE clustering
implementation yielded **2 vs 3 loan representatives**, depending on whether
its entry was named `base` or `comparison` (326 clusters and 8,559 pairs in
both). Entry names incorrectly selected old/current aliases.

Preparation now accepts a revision and reads its `_TERM_ALIASES` and optional
`_ALIAS_MATCH_MODES` with AST literal evaluation. Historical matcher source is
not executed. Missing mode configuration means phrase semantics. Current runs
use current configuration; all historical variants use their own configuration,
not BASE's. Context-managed patches restore both dictionaries on success/error;
regex caching includes the mode, so historical/current modes do not collide.

This intentionally reconstructs matcher configuration plus clustering code,
not an entire historical pipeline/environment. Tests cover BASE-vs-BASE tags,
clusters/pairs and CLI dispatch, old aliases, old phrase/right-token modes,
current particle mode, repeated runs and restoration after exceptions. The
fresh-clone fixture now includes the real BASE matcher as well as clustering
source. Default execution remains independent of transient PR objects and
`--compare-revision` remains optional. The CLI with `--compare-revision BASE`
was also run on all three actual candidate CSVs; BASE and comparison results
are identical, including the corrected 09-15 **2 / 2** loan representatives.

### Validation and fixed-cohort audit

- **63 new cases** across `test_period_particle_review.py` (57) and
  `test_replay_matcher_revision.py` (6); initial pre-fix matrix: 14 failures.
- Related clustering/matcher/relevance/prefilter/golden/replay tests:
  **531 passed, 1 skipped**, 10 existing NumPy/joblib deprecation warnings.
- Full Linux/Python 3.11 suite, network disabled, read-only source/Git mounts:
  **884 passed, 1 skipped** in 34.46 seconds.
- `git diff --check`: passed; full source/tool/test/document diff reviewed.

Fresh before/after captures against 3b6e598 were made using ignored
`.venv/capture_round4.py` and `.venv/round4-{before,after}.json`. They include all
representative titles/URLs, all memberships, both fixed and rescored cohorts,
and per-candidate relevance evidence. Parsed JSON objects are **identical**.
All 3,327 candidate rows have unchanged scores, hard/soft/negative terms and
domain anchors. No retained URL, tagged sector, representative, membership or
pair changed. This is candidate replay with recorded probabilities, not a live
fetch or production-equivalent model run. No large replay artifact is committed.

Every numeric cell below is **before = after**:

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest | >=50 | Loan reps fixed / rescored | New merged / split pairs (both) |
| --- | --- | --- | ---: | ---: | --- | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 | 0 / 0 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 | 0 / 0 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 | 0 / 0 |

Every sector representative count remains exactly as listed in the preceding
3b6e598 follow-up table. Golden pair precision/recall remains
**1.0000 / 0.922414** (107/107/116); other-sector labels remain
**1.0000 / 0.888889** (40/40/45). End-to-end golden keeps all 33 relevant items
and rejects four noise items. No unexplained corpus movement needs attribution.

Limits for human review: the period feature uses only explicit, unambiguous
pre-metric evidence and treats ratios as snapshots; it is not a general Korean
date parser. `도` and unlisted stacked particles remain intentionally ambiguous.
Historical replay varies the two matcher configuration dictionaries, not all
historical tagging/relevance code. Existing broad macro/digital fingerprints
and conservative court/Jeju splits remain outside scope. No query, ranking,
threshold, ML/Gemini/report/email changes, new dependencies, merge or thread
resolution were performed.


## Follow-up: metric association and context parity on 676a722

Starting local/PR HEAD: `676a7224815b5de4f063e6d4520094a552f1bd17`, clean
worktree on the existing branch. All four latest comments were read directly.
Initial 36-case tests before production edits: **25 failed, 11 passed**.
Each finding reproduced; no new branch/PR or review-thread resolution.

### Metric subject association and numeric identity (reviews 1 and 4)

[Subject review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043846838)
and [value review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043846847)
were analyzed together. Previously every headline measurement was intersected
against the first measurement's subject. Thus Samsung 200 + Hanwha 180 could
supply a false Samsung 180 fact. Separately, raw `200` versus `200.0` prevented
the period veto from recognizing equal levels, allowing similarity to merge
conflicting quarters.

Chosen bounded strategy: **disable authoritative metric association for any
headline with multiple recognized measurement occurrences**. `_metric_subjects`
returns no authoritative subject there; both the shortcut and subject/period
vetoes therefore cannot misattribute those values. Count occurrences before
numeric deduplication, including repeated equivalent values. Single measurement
with one known subject remains the ordinary fast path; missing/multiple subjects
remain conservative. Ordinary same-wire similarity is retained for multi-metric
headlines. No occurrence parser or new finance fact architecture was needed.

All extracted metric values now share deterministic string normalization:
strip redundant leading integer zeros and trailing fractional zeros, dropping
an empty decimal suffix. `200`, `200.0`, `200.00` become `200`; `215.20` becomes
`215.2`. No floats/dependencies. Shortcut and period veto consume the same
normalized extraction; percentage-point suffix guards remain unchanged.

Tests include the exact false-merge pair through final clustering (removing
metric evidence independently verifies no alternative similarity path), single
subject/single measurement, single subject/multiple measurements, multiple
subjects/measurements, aggregate/missing subjects, ordinary multi-metric wires,
and three numeric format pairs through same-period and conflicting-period
final clustering. Existing subject aliases and period-equivalence tests pass.

### Standalone enforcement verb (review 2)

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043846841).
Substring `잡는다` inside `바로잡는다` created the same local-enforcement
fingerprint as a crackdown. Only `잡는다` now requires both lexical boundaries
(no adjoining Korean syllable, Latin letter or digit). Whitespace/punctuation
and `불법사금융을 잡는다` remain valid. 단속/수사 semantics are unchanged.
Tests reject 바로잡는다/붙잡는다/다잡는다/잡는다며 and check final fingerprint
and cluster separation from 집중 단속, with positive quoted/punctuated verbs.

### Alias/context semantic parity (review 3)

[Review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4043846844).
Hard matching was canonical but raw spaced 불법/미등록 triggered standalone risk
signals. With only 여행 noise, compact 불법사금융 scored **4/drop**, spaced form
**7/keep**. 불법대부/미등록대부/불법추심 changed **2/drop → 5/keep**. 불법사채
changed **2/drop → 7/keep**, including a separate 2-point 사채 context bonus.

Semantic source of truth is the existing conservative compact spelling. This
matches the documented requirement for a strong anchor **and additional risk
or regulatory signal** before reducing a capped-noise penalty. No blanket
upgrade of illegal-lending anchors to risk signals was made.

A relevance-local context helper compacts only supported aliases in those five
families. It reuses the actual matcher alias configuration and match modes,
preserving particles and explicit financial compounds while rejecting 대부도
collisions. Global normalization, raw hard/soft/negative matching and stored
article text are unchanged. Scoring's 사채/lease context checks and negative cap
use that context view; filtering uses the same shared risk-signal function.
Independent 피해/불법 영업 evidence remains available. Tests compare full matched
terms, strong context, score, domain anchor, keep and reason across each family
for 여행/맛집 and an independent-risk positive. Existing lease/island and golden
recall tests also pass.

### Validation and measured corpus effect

44 new cases in `tests/test_metric_context_review.py`; first 36-case pre-fix
matrix failed 25 cases as recorded above. Final related suite:
**575 passed, 1 skipped** (10 existing NumPy/joblib warnings). Full Linux/Python
3.11 suite with network disabled and read-only source/Git mounts:
**928 passed, 1 skipped**. `git diff --check` passed; complete diff reviewed.

Fresh captures `.venv/round5-before.json` / `round5-after.json` compare actual
676a722 and revised execution. Temporary artifacts remain ignored. Both fixed
(recorded keep/score) and rescored (recorded probability, current rules) replay
preserve every retained URL, membership, representative title/URL and sector
representative count. New merged/split pairs are **0 / 0** on all six runs.

All table values are **before = after**:

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest | >=50 | Loan representatives fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All sector counts remain the values tabulated in the prior follow-ups.
Golden pair precision/recall: **1.0000 / 0.922414** (107/107/116);
other-sector labels: **1.0000 / 0.888889** (40/40/45). All 33 relevant golden
articles remain kept, all four noise fixtures dropped.

Of 3,327 candidate rows, hard/soft/negative matches and domain anchors have
**zero changes**. Exactly **one score** changes on 09-17 (zero-based CSV row 6):
[YTN court report](https://www.ytn.co.kr/_ln/0103_202609161700525967),
`'성착취 사채' 50대 1심에서 실형..."용서 못 받아"`. Its snippet contains
`불법 사채업자`, supplying standalone 불법 to the title's 사채 and adding 2 points.
The exact compact-snippet control scored **6 before and after**; original spaced
snippet changes **8 → 6**. Hard match remains 불법사채, no soft/negative matches,
domain anchor remains true. Recorded probability **0.5186**: rescored keep stays
**true → true** at the existing gray threshold 6. Recorded CSV keep is 0, so the
fixed cohort excludes it on both sides. It gains no extra cluster/representative
change. A regression using the existing golden row fixes this real example.
This is an intended alias-parity correction, with no observed valid-finance
recall loss or unexplained broad movement.

Remaining limits: multi-measurement shortcuts trade some potential recall for
safe association; ordinary similarity can still join such headlines. The
context helper is deliberately limited to five supported lending alias families,
not general language normalization. Existing macro/digital fingerprints and
conservative court/Jeju fragmentation remain outside scope. This is deterministic
candidate replay, not live collection/API/model equivalence. Humans should
review the conservative shortcut choice and canonical-context policy before
merge; no thresholds, rankings, query/fetch, ML/Gemini/report/email or replay
configuration behavior was changed.

## Follow-up: period safety independent of value on 3d8f531

[Codex review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4044581830)
was reproduced before production changes: the 14 new cases in
`tests/test_metric_period_value_review.py` yielded **8 failed, 6 passed**.
The continuation preserved that uncommitted test and reused the completed,
parseable `.venv/round6-before.json` captured against 3d8f531.

`_conflicting_metric_periods()` required an intersection of complete
`(metric, canonical_value)` tuples. KB's Q1 200% and Q2 201% therefore bypassed
period safety and merged through ordinary title similarity. Period safety asks
whether the same subject reports the same measurement in conflicting periods;
the measurement's value need not be equal. Only this helper's gate now uses
metric-name intersection. The exact metric shortcut still requires the same
subject AND equal `(metric, canonical_value)` tuples. Subject authority,
period extraction/equivalence, missing-period policy and thresholds are unchanged.
The existing cluster-member veto calls the same helper, protecting admission
through a missing-period bridge without changing single-link architecture.

Regression matrix: different quarter/value and year/value at helper, pair and
final-cluster levels; same/equivalent period with different values; missing
period; different metric identities (delinquency versus loan-deposit spread);
exact shortcut rejecting 200/201 while retaining 200/200.00; same-quarter wire
variants versus next quarter; and missing-period bridge, both three-article
cases across all six input permutations. All **14 passed** after the fix.
Related clustering/golden/prior-review/replay tests: **257 passed**.
Full Linux/Python 3.11, network disabled, read-only source/Git mounts:
**942 passed, 1 skipped**. The first Docker run's Git guard saw CRLF-only changes;
matching the Windows checkout with process-local `core.autocrlf=true` fixed it,
without disabling the guard or modifying files/configuration. Native Windows
full run: **939 passed, 1 skipped, 3 failed** from a POSIX-path assertion and
two unavailable WSL bash invocations, outside this patch. `git diff --check`
passed. No unrelated production or test changes were made for these limitations.

The completed before/after captures are entirely equal (including every
representative title/URL and sector count). All values below are before = after:

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest | >=50 | Loan representatives fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All six cohorts retain identical kept sets and membership; newly merged/split
pairs: **0 / 0**. All 3,327 candidates retain identical relevance scores,
hard/soft/negative terms and domain anchors. Golden pair precision/recall:
**1.0000 / 0.922414** (107/107/116); other-sector labels:
**1.0000 / 0.888889** (40/40/45), unchanged. No corpus pair needed correction
in these cohorts. Temporary capture/comparison files stay ignored in `.venv`.

Remaining limitation: the veto still requires an authoritative single subject
and explicit conflicting period dimensions. Ambiguous multi-measurement or
missing-period headlines do not acquire inferred period conflicts. This is
fixed/rescored candidate replay, not live API/model equivalence. Before merge,
humans should confirm the identity-versus-value role separation and review the
synthetic recurring-report/bridge cases; prior out-of-scope clustering issues
remain unchanged.

## Follow-up: bounded industry-subject particles on 71b930e

[Codex review](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4045018285)
reproduced: the industry fallback accepted 는/가/의/들 but omitted 은/이.
Both banking scopes consequently lost their metric subject and bypassed the
subject veto, merging through ordinary similarity. The final pre-fix matrix
in `tests/test_industry_subject_particle_review.py` gave **20 failed, 15 passed**,
including helper, pair, final-cluster and missing-subject bridge failures.

Only the industry fallback's right boundary changed: optional plural 들,
optional basic particle 은/는/이/가/의, then a boundary excluding Korean letters,
Latin letters and digits. Bare labels and punctuation/whitespace boundaries
remain valid. A suffix must be complete; 은행권이익, 저축은행권역, 보험사가치,
은행권들러리 and ASCII/digit continuations are rejected. Plural+particle forms
such as 은행권들은 remain recognized. General entity extraction, company-name
patterns and canonical identities are unchanged; this is not a tokenizer.

All **35 new cases passed**, covering the four requested 은/이 subjects,
existing 는/가/의/들, same-scope 10/10.0 wire recall, distinct banking scopes,
lexical negatives and missing-subject bridges across all six input orders.
Related metric/clustering/golden/other-sector/replay regression: **292 passed**.
Full Linux/Python 3.11 suite, network disabled, read-only source/Git mounts,
process-local core.autocrlf=true: **977 passed, 1 skipped**. Docker was initially
off and was started for this validation. Native Windows full run:
**974 passed, 1 skipped, 3 failed** (the existing POSIX-path assertion and two
unavailable WSL bash invocations). No tests were disabled or changed to bypass
these environment limitations. `git diff --check` passed.

Fresh 71b930e before and revised after captures are entirely equal. All values
below are before = after:

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest | >=50 | Loan representatives fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All kept sets, memberships, sector representative counts and representative
title/URLs are identical; new merged/split pairs **0 / 0** on all six cohorts.
All 3,327 candidate titles retain identical extracted metric subjects; scores,
hard/soft/negative terms and domain anchors also have zero changes. Thus corpus
mentions of these particles do not affect this metric-extraction path in the
three days. Golden pair precision/recall **1.0000 / 0.922414** (107/107/116);
other sectors **1.0000 / 0.888889** (40/40/45), unchanged. Temporary captures,
comparison scripts and logs stay ignored under `.venv`.

Limits: industry fallback intentionally recognizes only the basic particle set
and plural combination, not arbitrary suffixes such as 에서는/들에게. General
company/entity suffix behavior is outside this finding. Missing/ambiguous
metric subjects remain conservative, and existing unrelated clustering limits
are unchanged. This validates offline candidate replay, not live collection.

## Follow-up: named particles, lending suffixes and change-report safety on 26ec9fc

Read and reproduced the three Codex comments:
[named subjects](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058575370),
[lending suffixes](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058575374),
[percentage-point safety](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058575377).
The first 72-case pre-production matrix gave **48 failed, 24 passed**. The final
79-case matrix was also executed against both original 26ec9fc production modules
loaded from Git in an isolated process: **52 failed, 27 passed**. No working-tree
reset or historical code snapshot was committed.

Named metric subjects now accept complete 은/는/이/가/의/도/과/와/을/를 suffixes,
with a Korean/Latin/digit boundary after the suffix. The metric-local general
entity candidates receive the same boundary validation so known-bank substring
matches cannot bypass it (e.g. 국민은행이익). General `_extract_entities()` and
industry fallback/canonicalization remain unchanged. Tests cover company
particles, lexical negatives, conflict/pair/final clustering and named versus
industry scope through existing alias matrices. Comitative fixtures explicitly
refer to the company's own ratio; this is not general grammatical role inference.

Spaced 불법 대부/미등록 대부 aliases now support complete 까지/만/부터/조차 alongside
the prior suffixes. A small explicit postposition tuple defines their contract;
도 remains excluded due to 대부도, and 대부만기/ASCII/digit continuations fail.
The two aliases use `korean_postposition`; the previous `korean_particle` mode
is retained unchanged so revision-specific alias-mode replay preserves old
semantics without needing unreachable commit objects or changing replay code.
At candidate probability 0.8, all six requested synthetic phrases changed from
**score 0 / no hard anchor / no domain / drop** to **score 6 / canonical hard
anchor / domain / keep**. Existing compounds, particles and noise tests pass.

Metric identity and absolute level now have separate roles. A shared regex
component recognizes the existing metric+numeric-percentage occurrence syntax,
including percentage-point changes. `_metric_identities`, subject extraction
and period extraction use those occurrences. `_reported_metrics` still excludes
%p/% p/%포인트/% 포인트 and retains numeric canonicalization. One additional local
feature set feeds subject/period vetoes; exact shortcut logic continues to use
only `(metric, absolute value)`. Multiple occurrences, including mixed level and
change reports, provide no authoritative subject, preserving conservative
association. Tests exercise all four change suffixes, different subjects,
period conflicts/equivalence/missing periods, all supported metric identities,
absolute shortcut positives, level/change negatives and bridge permutations.

Validation: **79 new tests passed**; final related suite **703 passed, 1 skipped**
(10 existing NumPy/joblib warnings); full Linux/Python 3.11, network disabled,
read-only source/Git mounts, process-local core.autocrlf=true:
**1056 passed, 1 skipped**. `git diff --check` passed. Production changes are
limited to issue_cluster.py and text_matcher.py; no thresholds/ranking, queries,
relevance policy, ML/Gemini/report/email or general normalization changes.

Fresh fixed/rescored 26ec9fc before and revised after captures are entirely equal:

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest | >=50 | Loan representatives fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All kept sets, membership, sector representatives and representative title/URL
are unchanged; newly merged/split pairs **0 / 0** for all six cohorts. All 3,327
candidate scores, hard/soft/negative terms, domain anchors and metric subject
sets are unchanged. No actual candidate acquired a new keep from these suffixes.
Golden precision/recall **1.0000 / 0.922414** (107/107/116); other-sector labels
**1.0000 / 0.888889** (40/40/45), unchanged. Debug/replay artifacts stay ignored
in `.venv`; only this note and regression tests accompany production changes.

Limits: bounded suffix lists do not parse arbitrary stacked particles. Lending
도 stays ambiguous and unsupported. Metric occurrence recognition remains the
existing numeric percentage vocabulary, not bare-label/general financial NLP.
Missing/ambiguous subjects do not authorize metric shortcuts or inferred vetoes;
ordinary similarity remains available. Prior unrelated clustering issues are
out of scope. Humans should review identity/level separation and the explicit
suffix contracts before merge; replay is offline, not live API/model equivalence.

## Follow-up: insurer identity, complete 킥스 label and campaign years on 82522ab

Read and reproduced all three latest Codex findings:
[insurer aliases](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058753158),
[킥스 boundary](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058753162),
[campaign periods](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058753166).
Initial test-first matrix: **14 failed / 25 passed** on unmodified 82522ab.
Final 44-case matrix re-executed against that Git revision in an isolated Python
module: **16 failed / 28 passed**. Pair/final-cluster failures reproduce all three
findings; two additional failures reference the new period-veto helper.

### Exact insurer evidence and scope

Counts below are case-insensitive literal occurrences in candidate title/summary
fields, including repeated snapshots; they are not deduplicated article counts.

| Full / abbreviated name | All stored candidates: full title/summary | Abbreviation title/summary | September 15–17: full / abbreviation totals | Canonical identity |
| --- | ---: | ---: | ---: | --- |
| NH농협손해보험 / NH농협손보 | 6 / 26 | 4 / 0 | 0 / 0 | nh농협손해보험 |
| KB손해보험 / KB손보 | 9 / 60 | 20 / 10 | 5 / 6 | kb손해보험 |
| DB손해보험 / DB손보 | 21 / 59 | 60 / 27 | 2 / 2 | db손해보험 |

`2026-08-07_candidates.csv` directly pairs the title `[핀포인트] [NH농협손보]
1년여 만에 킥스 200%대 회복…금리가 끌어올린...` with a summary naming
NH농협손해보험 and its 226.47% ratio. The three-day scan found no additional
full/short pair needing a new identity; tests/analysis contained no NH alias.
Only exact `nh농협손보 -> nh농협손해보험` is added. 롯데/한화/하나/카카오페이
abbreviations observed without corresponding full names in the three-day cohort
are not added. No suffix-wide replacement or general entity change. Tests retain
KB/DB equivalence, different insurers, industry scope and unverified-name vetoes.

### Issue-term and campaign safety

Unrestricted 킥스 substring matching gave unrelated 킥스타터 projects an extra
shared issue term and merged them. Only this alias gets a complete-label matcher:
킥스 plus optional spaced/unspaced 비율, bounded on the left and after an optional
complete 은/는/이/가/의/도/과/와/을/를. Other distinctive aliases and numeric metric
occurrence/value/subject/period parsing retain their existing semantics. The
numeric occurrence regex cannot directly serve bare issue labels: it requires a
percentage value. No generic issue-term refactor is introduced.

The first boundary version dropped the legitimate issue term in four candidate
snippets (09-16: 교보생명 교보라이프플래닛 합병…; iM라이프, 단기납 종신보험…;
09-17: 계리감독 선진화가 가른 CSM…; 교보생명, 라이프플래닛 흡수합병…). Their
킥스비율도/을/과 forms required complete grammatical suffix support. Three added
corpus regressions failed before that correction; lexical 킥스비율도약/과정 remain
negative. These changes do not alter the spaced lending alias contract.

Recurring enforcement campaigns shared an authority/target fingerprint despite
conflicting years. Keep that fingerprint unchanged and add a headline-only,
unambiguous explicit 19xx/20xx년 feature. Same enforcement family + two different
known years veto the pair before low-value/equal-fingerprint shortcuts and veto
cluster admission against every existing member. Missing or multiple distinct
years do not infer a conflict. All bridge input permutations, same-year/missing
wires and description-only background dates are covered. The three-day campaign
headlines use 추석/한가위 rather than comparable numeric periods; no month parser,
publication-date inference or relative-date interpretation is added.

Validation on final code: **44 new tests passed**; related suite **747 passed,
1 skipped**, with 10 existing NumPy/joblib deprecations. An initial Windows
subprocess encoding warning disappeared on UTF-8 rerun. Full Linux/Python 3.11,
network disabled/read-only mounts: **1100 passed, 1 skipped**. `git diff --check`
passed. Only issue_cluster.py changes production behavior.

Final fresh fixed/rescored replay versus 82522ab is **entirely equal**, including
all 3,327 candidates' relevance scores, hard/soft/negative terms, domain anchors,
metric subjects, issue terms and fingerprints. The four provisional particle
regressions above are fully restored. No actual NH alias correction, Kickstarter
term removal or enforcement-year split occurs in these cohorts.

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest | >=50 | Loan reps fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All six kept sets, cluster memberships, sector counts, representative titles/URLs
are unchanged. Newly merged / split pairs: **0 / 0**. Loan golden precision/recall
**1.0000 / 0.922414** (107/107/116); other-sector **1.0000 / 0.888889**
(40/40/45), unchanged, as is golden end-to-end relevance.

Limits: exact insurer aliases remain deliberately incomplete. The dedicated
킥스 label guard supports bounded basic particles, not arbitrary compound words
or stacked suffixes; other distinctive aliases are untouched. Campaign safety
recognizes only one explicit headline year, not months or relative dates, and
cannot resolve ambiguous/background-year roles with general NLP. Missing-year
articles can join either compatible campaign but cannot bridge known conflicts.
No architecture/threshold/ranking changes; offline replay does not establish live
API/model equivalence. Human review should verify these narrow contracts before
merge. Temporary evidence remains untracked under ignored `.venv`.

## Follow-up: hard-anchor parity, metric labels and police agencies on ce5d635

Read the actual Codex comments for [overlap](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058922171),
[particles](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058922176),
[police](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058922180),
and [KICS](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4058922182).
Added 67 regression cases before production edits: **35 failed / 32 passed** on
ce5d635. Each finding has observed final relevance/pair/cluster failures, not
only missing-helper assertions. Final matrix: **67 passed**.

### Contracts and fixes

* Hard-anchor spelling parity: `불법 대부업 점검` scored 11 and kept at probability
  0.1, while compact `불법대부업 점검` scored 6 and dropped. Reuse the existing
  narrow lending-context canonical view for hard evidence, shared by score and
  matched_terms. Spacing inside a supported lending phrase no longer introduces
  a separate 대부업 anchor. Compact spelling is the source of truth, including its
  finance-entity guard for generic enforcement terms. Independent 대부업 mentions
  elsewhere and independent 금융위/보험사/연체율 evidence remain. Neither weights,
  thresholds, matcher aliases, global text normalization nor domain guard change.
  Both 불법/미등록 대부업 forms now score 6, drop at 0.1 and keep at 0.8 with strong
  finance/domain anchors. An important pre-existing distinction is preserved:
  compact **대부중개업** already matched 대부업 through its explicit phrase alias;
  compact/spaced brokerage examples were and remain 11, rather than silently
  introducing a broader scoring-policy change. Alias/compound recall is retained.
* Metric particles and compact Latin are one parser contract. A private capital
  adequacy label component serves issue terms, numeric occurrences and direct
  identity conversion. It supports 킥스(+ optional spaced 비율), K-ICS/K ICS/KICS,
  and 지급여력 비율. Occurrences add only 도/만 to existing 은/는/이/가 and require
  the numeric percentage next, rejecting 도입/만기. Label starts have Korean/ASCII
  boundaries; complete issue labels reject SKICS/KICSabc/myKICSvalue/킥스타터.
  Direct full-match identity conversion prevents a regex-only KICS fix from
  leaving empty identity sets. Value canonicalization and percentage-point
  exclusion remain unchanged. Tests cover subject and period vetoes, same-company
  shortcut, %p, and multiple-occurrence ambiguity. The corpus scan found existing
  은/는 numeric labels, but no new 도/만 or compact KICS safety case in these days.
* Police authorities: normalize jurisdiction suffix 시/특별시/광역시/특별자치시
  before appending **경찰청**, separately from existing city/city-hall handling.
  서울경찰청/서울시경찰청/서울특별시경찰청 now share 서울경찰청; 부산 variants are
  separately tested. Police versus municipal government and different cities
  remain distinct. Fingerprint equality, final wires, conflicting campaign years
  and all missing-year bridge permutations are tested. No authority registry or
  event-period expansion is introduced.

### Validation and replay

Related suite: **814 passed, 1 skipped**, 10 existing NumPy/joblib warnings.
Full Linux/Python 3.11, network disabled, read-only source/Git mounts:
**1167 passed, 1 skipped**. New tests + loan golden: **80 passed**.
`git diff --check` passed. Production changes are limited to issue_cluster.py
and relevance_score.py; replay engine/configuration contracts are untouched.

Fresh before/after fixed and rescored replay against ce5d635:

| Date | Fixed kept / clusters | Rescored kept / clusters | Largest | >=50 | Loan reps fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All six kept sets, memberships, sector representative counts and representative
URLs/titles are unchanged; newly merged/split pairs **0 / 0**. All 3,327 candidates'
metric identities, reported values, subjects, periods, issue terms, fingerprints,
domain anchors, soft terms and negative terms are unchanged. Four 09-17 rows lose
only the duplicate 대부업 hard term from `미등록 대부업` in their summaries:

| Article | Hard terms before -> after | Score | Fixed keep | Rescored keep |
| --- | --- | --- | --- | --- |
| [서울시, 한가위 앞두고 전통시장 불법 사금융 집중 단속](https://news.bbsi.co.kr/news/articleView.html?idxno=4107034) | 대부업, 미등록대부, 불법사금융 -> 미등록대부, 불법사금융 | 19 -> 14 | drop -> drop | keep -> keep |
| [서울시, 추석 앞두고 전통시장 불법사금융·고금리 대출 집중 단속](https://news.sbs.co.kr/news/endPage.do?news_id=N1008756001) | 대부업, 미등록대부, 불법사금융 -> 미등록대부, 불법사금융 | 20 -> 15 | keep -> keep | keep -> keep |
| [서울시, 추석 앞둔 영세 소상공인 대상 ‘불법사금융’ 집중 단속](https://www.etoday.co.kr/news/view/2626108) | 대부업, 미등록대부, 불법사금융, 불법대부 -> 미등록대부, 불법사금융, 불법대부 | 25 -> 20 | keep -> keep | keep -> keep |
| [서울시, 추석 앞두고 전통시장 불법사금융·고금리대출 집중단속](https://www.yna.co.kr/view/AKR20260916047700004) | 대부업, 미등록대부, 불법사금융 -> 미등록대부, 불법사금융 | 19 -> 14 | keep -> keep | keep -> keep |

This is the intended 5-point spelling correction; none loses relevant recall.
Fixed replay preserves recorded production scores/decisions by design, while
per-candidate score comparisons and rescored replay recompute current evidence.
Loan golden precision/recall **1.0000 / 0.922414** (107/107/116); other-sector
**1.0000 / 0.888889** (40/40/45), unchanged. Golden end-to-end output also identical.
No additional metric recovery or police fingerprint change occurs in this cohort.

Limits: bounded suffixes and explicit supported labels are not a morphological
parser. Compact brokerage anchors retain existing weighting, not a global
semantic-score dedupe policy. Police normalization retains the existing authority
recognizer's coverage. Year-only campaign policy and exact insurer-alias coverage
are unchanged. No registry/architecture/threshold/ranking work is included. Human
review should confirm the compact baseline policy and labelled edge cases before
merge; this is deterministic offline replay, not production API/model equivalence.

## Cumulative review follow-up: event evidence, campaign months and metric owners

Baseline: `d72a623ad663e9fb8e7207cbe73cfeb7ce14fc79`. Read Codex's actual
[event-evidence](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4059927903),
[campaign-month](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4059927908),
[legal-name](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4059927909), and
[aggregate-owner](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4059927911)
comments. Initial 37-case regression matrix on that baseline: **15 failed / 22
passed**, reproducing all four findings before production edits. Two subsequent
stored-title regressions reproduced the overly strict intermediate policy
(**2 failed / 37 passed**); final new matrix: **39 passed**.

### Final contracts

* Equal subject/metric/rounded value alone no longer returns True. The exact
  shortcut additionally requires equal explicit as-of months (including existing
  quarter/half-year equivalences); known conflicting years still veto first.
  A year alone is insufficient. Without the shortcut, ordinary similarity decides.
  Removing the shortcut alone was insufficient: existing entity/number/issue
  overlap could still merge distinct announcements. A narrow pair and member-wide
  check therefore removes the already-counted metric, subject and period from
  headline evidence. For same-subject/equal-level articles without an as-of month,
  two nonempty disjoint residual event-token sets cannot authorize merging or be
  bridged by a bare statistic. Bare/unknown event text is not a conflict. Neither
  missing/ambiguous periods against dated wires nor titles truncated with trailing
  dots establish an event conflict. The guard is not a generic event parser.
* Campaign period becomes `(year, month)` using only explicit YYYY년 and 1–12월
  in enforcement headlines. Each dimension with several distinct values stays
  unknown. Compare only dimensions known on both sides, preserving missing-year/
  month wires and the existing fingerprint. Month/year vetoes run before equal
  fingerprint/low-value shortcuts and against every existing cluster member.
* Add only exact `교보생명보험 -> 교보생명` in the metric-local alias map. The July
  30 stored [capital-security article](https://www.edaily.co.kr/news/newspath.asp?newsid=05717046645519440)
  uses 교보생명 in the title and 교보생명보험 in its summary. The August 31
  [financing preview](https://www.bloter.net/news/articleView.html?idxno=672131)
  supplies another legal-name occurrence. No suffix-wide normalization or other
  unverified legal alias is added; other insurers and industry aggregates remain
  distinct.
* `등/포함한/포함 + industry label` must immediately precede the metric occurrence
  to establish aggregate ownership. Thus examples before 보험사/생보사/손보사 do
  not own that statistic. `보험사 중 삼성생명`, plain named subjects, subsequent
  comparisons and descriptions retain the existing policy. Multiple named owners
  without an explicit governing aggregate are not reduced to an arbitrary one.

Observed alias counts below are **candidate rows containing the name in title or
summary**, with the short spelling excluding the full-name substring:

| Candidate date | 교보생명 | 교보생명보험 | Canonical identity |
| --- | ---: | ---: | --- |
| 07-30 | 1 | 1 | 교보생명 (same row) |
| 08-31 | 0 | 1 | 교보생명 |
| 09-15 | 0 | 0 | 교보생명 |
| 09-16 | 34 | 0 | 교보생명 |
| 09-17 | 21 | 0 | 교보생명 |

The existing period test now explicitly distinguishes "no period conflict" from
"must merge": its missing-period 자본확충/후순위채 fixture no longer gets a free
same-value shortcut. The ambiguous-period case continues to merge through its
ordinary evidence and now separately asserts absence of a period conflict.
Same-period wires, genuine no-period wires, and all event/campaign bridge
permutations are tested. No relevance, thresholds, ranking or replay engine edits.

### Validation and final replay

New tests **39 passed**; new tests plus existing period tests **96 passed**.
Related suite **853 passed, 1 skipped** (10 existing NumPy/joblib warnings).
Full **Linux / Python 3.11 / network disabled: 1206 passed, 1 skipped**.
`git diff --check` passed. The continuation preserved the original changes and
baseline captures; after finding residual fragmentation, it added two failing
stored-title tests and reran related/full suites and all six replay cohorts.

The first strict policy (09-17 fixed 401 -> 404 clusters, 82 split pairs) was
rejected. The intermediate policy still split 43 pairs: four no-period pairs had
nonoverlapping residual wording, but each involved a stored headline truncated
mid-word with trailing dots. Treating incomplete text as a definitive conflict
blocked otherwise valid dated-wire links. Final policy abstains from that veto;
it does not restore the unconditional metric shortcut or lower any threshold.

| Date | Fixed kept / clusters (before = after) | Rescored kept / clusters | Largest | >=50 | Loan reps fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All six cohort dictionaries (kept sets, memberships, per-sector counts and exact
representative titles/URLs) are identical. Newly merged/split pairs **0 / 0**.
All 3,327 candidates' relevance scores, hard/soft/negative terms, domain anchors,
metric identities/subjects/absolute values/periods, issue terms and fingerprints
are unchanged. Campaign period comparison adapts baseline year to `(year, None)`
only for schema comparison; no new month evidence occurs in these candidates.
No article-level correction is claimed where none was measured.

Of 82 former shortcut-only pairs in each 09-17 cohort, **46** still match directly
through equivalent explicit periods. The remaining **36** now fail ordinary pair
similarity (none is event-vetoed) but **all retain the same final cluster** through
existing wire links. The table below identifies every rejected direct pair. Their
subject/value/period and titles, not new labels or dates, drove this audit. Golden's
one shortcut-only pair remains directly connected. Loan golden precision/recall
**1.0000 / 0.922414** (107/116 recall); other-sector **1.0000 / 0.888889** (40/45),
both unchanged; golden end-to-end evaluation also identical.

Limits: exact residual-token overlap is a conservative local heuristic, not
semantic event identity. Dated or incomplete headlines still rely on existing
ordinary rules; different events sharing an explicit as-of month or generic
wording can still require future review. Month/year extraction does not infer
relative dates, publication dates, description dates, or day-level campaigns.
Legal aliases are exact and corpus-verified, and aggregate grammar covers only
the narrow immediate constructions above. No registry/query/threshold/ranking,
ML/Gemini/report/email or unrelated macro/digital changes are included. Human
review should confirm these precision/recall boundaries before merging; this is
stored-cohort deterministic validation, not a production API/model rerun.

### Shortcut-only pair inventory (stored cohort audit)

All entries below are insurance-sector, canonical subject `보험사`, metric
`capital_adequacy_ratio=215.2`. The 19 titles and stored snippets describe the
September 16 release of June-end insurer capital adequacy (down 0.8 percentage
points, required capital rising). Assessment: **82 same-event, 0 different-event,
0 ambiguous** pairs in each September 17 cohort; this is an offline editorial
assessment of titles/snippets, not a production label feed. One snippet omits the
release date but shares the same required-capital cause and level. Neither
September 15/16 nor other-sector fixtures has shortcut-only pairs. Loan golden
has one (titles 7/8 below), preserved by explicit equivalent periods.

| ID | Observed title | Headline period |
| --- | --- | --- |
| 1 | [2분기 보험사 지급여력비율 215.2%… 전기比 0.8%P 하락](https://biz.chosun.com/stock/finance/2026/09/16/BKPDYDNNOBAMBMDDW2XGPLC67Q/?utm_source=naver&utm_medium=original&utm_campaign=biz) | [None, 6] |
| 2 | [6월 말 보험사 킥스비율 215.2%로 소폭 하락…손보는 상승](https://view.asiae.co.kr/article/2026091611191368572) | [None, 6] |
| 3 | [6월말 보험사 지급여력비율 215.2%…전분기 대비 0.8%p 하락](https://biz.sbs.co.kr/article_hub/20000334973?division=NAVER) | [None, 6] |
| 4 | [6월말 보험사 지급여력비율 215.2%…전분기 대비 0.8%p↓](https://www.newsis.com/view/NISX20260916_0003791785) | [None, 6] |
| 5 | [“주가 오르자 위험액도 늘었다”…보험사 지급여력비율 215.2% ‘소폭 하...](https://www.ddaily.co.kr/page/view/2026091614203432260) | [None, None] |
| 6 | [국내 보험사 2분기 K-ICS 비율 215.2%… 전분기 比 0.8%p 하락](https://www.insnews.co.kr/news/articleView.html?idxno=92866) | [None, 6] |
| 7 | [보험사 2분기 킥스비율 215.2%···전분기比 0.8%p↓](https://www.seoulfn.com/news/articleView.html?idxno=638102) | [None, 6] |
| 8 | [보험사 6월 말 킥스비율 215.2%...요구자본 증가](http://www.popcornnews.net/news/articleView.html?idxno=133153) | [None, 6] |
| 9 | [보험사 6월 말 킥스비율 215.2%…3개월 새 0.8%p 하락](https://www.dailian.co.kr/news/view/1691080/?sc=Naver) | [None, 6] |
| 10 | [보험사 6월말 킥스 215.2%…전 분기比 0.8%p↓](http://www.hansbiz.co.kr/news/articleView.html?idxno=865758) | [None, 6] |
| 11 | [보험사 상반기 킥스 215.2%·0.8%p↓…상위사 최대 28%p 하락](https://news.einfomax.co.kr/news/articleView.html?idxno=4435184) | [None, 6] |
| 12 | [보험사 지급여력비율 215.2%로 소폭 하락…주가 상승에 요구자본 증가](http://www.newsian.co.kr/news/articleView.html?idxno=95496) | [None, None] |
| 13 | [보험사 지급여력비율 215.2%로 하락…생·손보 엇갈린 희비](https://www.mydaily.co.kr/page/view/2026091616173998396) | [None, None] |
| 14 | [보험사 킥스 비율 215.2%로 전분기 比 0.8%p↓…손보사 상승·생보사 하...](http://www.srtimes.kr/news/articleView.html?idxno=212609) | [None, None] |
| 15 | [상반기 보험사 K-ICS 비율 215.2%로 소폭↓… 손보 웃고 생보 울고](https://www.dt.co.kr/article/12084229?ref=naver) | [None, 6] |
| 16 | [상반기 보험사 지급여력비율 215.2% … 전분기比 0.8%p 하락](https://biz.newdaily.co.kr/site/data/html/2026/09/16/2026091600263.html) | [None, 6] |
| 17 | [상반기 보험사 지급여력비율 215.2%…0.8%p 하락](https://www.etnews.com/20260916000049) | [None, 6] |
| 18 | [상반기 보험사 지급여력비율 215.2%…소폭 하락](http://www.segyebiz.com/newsView/20260916517510?OutUrl=naver) | [None, 6] |
| 19 | [주가 뛰자 위험액도 껑충…보험사 2분기 킥스비율 215.2%로 '주춤'](https://www.widedaily.com/news/articleView.html?idxno=301155) | [None, 6] |

Each row enumerates all right-hand partners with greater ID (82 unique pairs;
fixed/rescored inventories are identical). This compact inventory preserves
the inspected pair titles without duplicating large replay JSON artifacts.

| Left ID | Right IDs | Direct pair now False (same final cluster) |
| --- | --- | --- |
| 1 | 2, 8, 11, 15, 19 | — |
| 2 | 5, 6, 7, 8, 11, 16, 19 | 5 |
| 3 | 5, 8, 11, 12, 15, 19 | 5, 12 |
| 4 | 5, 8, 11, 12, 15, 19 | 5, 12 |
| 5 | 7, 8, 9, 10, 11, 12, 13, 14, 15, 16 | 7, 8, 9, 10, 11, 12, 13, 14, 15, 16 |
| 6 | 8, 11, 12, 13, 18, 19 | 12, 13 |
| 7 | 8, 12, 13, 15, 18 | 12, 13 |
| 8 | 11, 13, 14, 15, 16, 17, 18, 19 | 13, 14 |
| 9 | 12, 13, 15, 18, 19 | 12, 13 |
| 10 | 12, 13, 15, 18, 19 | 12, 13 |
| 11 | 12, 13, 15, 16, 19 | 12, 13 |
| 12 | 14, 15, 19 | 14, 15, 19 |
| 13 | 14, 15, 19 | 14, 15, 19 |
| 14 | 17, 18, 19 | 17, 18, 19 |
| 15 | 16, 17, 19 | — |
| 16 | 19 | — |
| 17 | 19 | — |

## Metric-event inflection follow-up (b3e3b7a)

Read the actual [Codex inflection finding](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4060642413).
Local/PR HEAD was `b3e3b7a55558c1f6ee49eef62394dacde902c9d1`, branch clean and
PR open. Added 30 regression cases before changing production: **20 failed /
10 passed**. The exact reproduction had raw event tokens `{자본여력, 하락}`
versus `{자본여력은, 하락했다}`; empty intersection incorrectly vetoed the same
statistical wire and split the final cluster.

### Narrow comparison contract

`_metric_event_token_variants` is used only inside the metric-event veto.
It preserves each raw token and offers a comparison alternative for:

* One complete noun particle: 은/는, 이/가, 을/를, 과/와, 의. The remaining
  stem must contain at least two Hangul syllables, with the appropriate
  consonant/vowel allomorph. No recursive suffix removal; 도/만 are excluded.
* Exact statistical predicates 하락/상승/감소/증가/개선/확대 with 했다/한다/
  됐다/된다. Full-token matching rejects longer continuations such as 하락했다는.

The reproduction now shares alternatives 자본여력 and 하락, so the veto is False;
existing ordinary evidence returns True and produces one cluster. No normalization
is injected into global title tokens, issue terms, meaningful overlap, relevance,
metric identity/value/period extraction, or ranking. A test removes ordinary
issue/entity/number evidence and proves equivalent morphology does **not** itself
return True. P1's compatible-month shortcut, missing/ambiguous-period policy,
truncated-title handling and member-wide bridge protection remain unchanged.

The new matrix exercises nine natural noun-particle forms, nine predicate forms,
the exact example, negative lexical/short-stem/unsupported suffix cases, no-global-
mutation/no-shortcut behavior and all bare-metric bridge permutations. Existing
cumulative tests retain 자본확충 versus 회계제도, KB 후순위채 versus 회계제도,
dated/no-period genuine wires and truncated stored headlines.

### Corpus audit and validation

Audit every pair in the September 15/16/17 fixed and rescored cohorts with the
baseline `_metric_match_lacks_event_evidence` and current implementation. In all
six cohorts, **baseline veto pairs = 0, True-to-False changes = 0**. Consequently
there are no changed article pairs/tokens/cluster decisions to classify:
same-event corrections **0**, different-event regressions **0**, ambiguous **0**.
This corpus does not measure the synthetic correction's frequency; no recall
improvement is invented. The audit records raw and alternative event tokens,
subjects, metric/value, periods and pair decisions for changes, if present.
A headline scan found no occurrences of the supported finite predicate forms in
these three stored dates; they are supported by the explicit review regressions.

New matrix **30 passed**; new + cumulative + period tests **126 passed**.
Related suite **883 passed, 1 skipped** (10 existing NumPy/joblib warnings).
Full Linux/Python 3.11 with network disabled: **1236 passed, 1 skipped**.
Docker was initially stopped; the failed connection was not counted as a test
run. After starting the engine, the full suite ran successfully. `git diff --check`
passed.

Fresh b3e3b7a before and current after replays:

| Date | Fixed kept / clusters (before = after) | Rescored kept / clusters | Largest | >=50 | Loan reps fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

All kept sets, cluster memberships, sector representative counts and representative
URLs/titles are unchanged; newly merged/split pairs **0/0**. All 3,327 candidates'
relevance scores, hard/soft/negative terms, domain anchors, metric identities,
subjects, absolute values, periods, raw event tokens, fingerprints and issue terms
are unchanged. Loan golden precision/recall stays **1.0000 / 0.922414** (107/116);
other-sector stays **1.0000 / 0.888889** (40/45), with identical end-to-end golden
output. Temporary replay/audit JSON and scripts are not committed.

Limits: this is bounded morphology tolerance, not a Korean morphological analyzer.
Two-syllable/allomorph checks cannot resolve every lexical-versus-particle ambiguity;
they only relax a veto, leaving ordinary clustering responsible for the final
merge. Unsupported endings, compound particles and broader event semantics remain
outside scope. No P1 policy redesign, registry, dependency, global tokenizer,
relevance, threshold or unrelated architecture changes are included.

## Compound-spacing and bare-month follow-up (ab92e030)

Read the actual Codex findings for [compound spacing](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4067534623)
and [bare months](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4067534629).
Starting local/PR HEAD was `ab92e030699cc0bc09d6f39567d2624b08bc8485`, with
PR open and a clean `fix/loan-news-clustering-recall` worktree. Added 63 tests
before production edits: **40 failed / 23 passed**.

### Two local corrections

1. The exact spacing example yielded `{자본확충}` versus `{자본, 확충}`.
   Single-token morphology could not equate these sets: event veto True,
   pair False, two final clusters. `_metric_event_text` now preserves residual
   order and marks removed subject/metric/period spans as adjacency barriers.
   `_metric_event_comparison_units` adds only joins of two consecutive retained
   Hangul tokens, each at least two syllables, separated solely by whitespace.
   Existing morphology alternatives also apply to the joined unit, so
   `자본 확충은` can compare with `자본확충`. No Cartesian concatenation,
   single-syllable joins, filtered-token skipping or three-token generation.
   The exact pair now has veto False, ordinary pair True and one final cluster.
   A stripped-feature test confirms this is **not** a merge shortcut; global
   tokens, issue terms, similarity, relevance and representative selection do
   not receive the new units. Corpus vocabulary supports 자본확충 (one title
   on 09-16) and 요구자본 (three on 09-17), used for spacing regressions.
2. `_metric_period` additionally recognizes bounded `1월` through `12월` in
   the existing measurement prefix. `3월 기준` works without special date
   inference. Reject 월물/월호/월분기, ASCII/numeric continuations and 13월.
   Existing 월말/월 말 parsing remains; set semantics prevent duplicate month
   evidence. Quarter/half-year equivalence, year extraction, missing/ambiguous
   dimensions and prefix-only ownership are unchanged. The exact 3월/6월,
   200%/201% reproduction changes from unknown periods/pair True/one cluster
   to months 3/6, period veto True/pair False/two clusters. Period safety still
   precedes same-value shortcuts and compound equivalence.

Tests retain disjoint 자본확충 versus 회계제도 and 후순위채 versus 회계제도
announcements, bare-metric bridges in all six orders, existing noun/predicate
morphology, truncated wires, same-month/statistical wires and missing-period
recall. Added adjacency barriers, no-shortcut/no-global-token changes, all twelve
months, quarter/half-year parity, multiple-month ambiguity, measurement-suffix
isolation and conflicting-month bridge permutations.

### Stored-cohort audits and replay

Audit all pairs in all six fixed/rescored cohorts. Baseline metric-event veto
pairs: **0**; compound-spacing True-to-False veto changes: **0**; matching
compact-versus-adjacent-spaced event candidates with the same authoritative
subject/value: **0**. No changed pair needs classification (same-event correction
0, different-event regression 0, ambiguous 0). Five 09-17 pairs share newly
joined comparison units on both sides, but both sides already had the same
spaced tokens; they are not compact/spaced corrections and pair decisions stay
True. This does not demonstrate a measured corpus recall gain.

There are **0 new bare-month articles** among 3,327 candidate rows. A broad
bounded-month scan also sees three existing `6월 말` headlines on 09-17; all
already had `(None, 6)` before and retain it. No period/conflict/pair change.
The audit keeps these existing month-end forms separate from new bare-month
coverage. No publication dates, descriptions or relative dates are inferred.

Fresh before/after replay JSONs compare equal in full:

| Date | Fixed kept / clusters (before = after) | Rescored kept / clusters | Largest | >=50 | Loan reps fixed / rescored |
| --- | --- | --- | ---: | ---: | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 |

Kept sets, memberships, sector representative counts and representative titles/
URLs are identical; merged/split pairs **0/0**. All candidates retain relevance
scores, hard/soft/negative terms, domain anchors, metric identities/subjects/
absolute values/periods, raw event tokens, fingerprints and issue terms.
Loan golden precision/recall: **1.0000 / 0.922414** (107/116); other-sector:
**1.0000 / 0.888889** (40/45), unchanged, as is end-to-end golden output.

New + inflection + cumulative + period targeted tests: **189 passed**, including
all **63 new** cases. Related suite: **946 passed, 1 skipped** (ten existing
NumPy/joblib warnings). Full Linux/Python 3.11, Docker network disabled:
**1299 passed, 1 skipped**. `git diff --check` passed. Temporary capture/audit
scripts and JSONs stay outside the commit.

Limitations: two adjacent Hangul-token comparison only, with the existing bounded
morphology vocabulary; no general compound segmentation or global whitespace
normalization. Month parsing is explicit headline-prefix grammar, not an event
calendar or date parser. P1 event-evidence policy, thresholds, unrelated sector
semantics, registry/recall/query architecture and production dependencies are
unchanged.

## Four scoped blockers follow-up (a353a3c)

Starting local/PR HEAD: `a353a3c5a170375b7e28497492de2e4a83f969d2`, clean existing
branch, PR open. Read all five actual Codex comments, all against that revision.
Only the four requested blockers below are changed. The [industry-particle
coverage finding](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4067727430)
(`은행권도`/`저축은행권도`, etc.) is explicitly deferred. Its fallback regex
and empty-subject behavior remain unchanged; this known false-merge gap remains
relevant to the human merge decision.

### Reproductions and minimal fixes

Added `tests/test_scoped_blockers_review.py` before any production edits:
**52 cases, 14 failed / 38 passed** on a353a3c. No existing tests were weakened.

| Blocker / actual review | Regression tests | Pre-fix failed / passed | Root cause and local fix | Exact pair final clusters before -> after |
| --- | --- | --- | --- | --- |
| [A: quantified aggregate](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4067727427) | `test_a_quantified_aggregate_wire`, modifier/scope controls | 3 / 9 | `등 10개 보험사` missed direct aggregate grammar and returned 삼성생명. Allow one optional positive decimal count plus `개` and required whitespace, immediately before the existing industry label. | 2 -> 1 |
| [B: locative 에](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4067727434) | `test_b_locative_compound_wire`, bounded/recursive/P1 controls | 3 / 6 | 자본확충 and 자본 확충에 나선다 had disjoint comparison units. Add one final 에 to comparison-only particle alternatives, with the existing two-syllable stem rule. | 2 -> 1 |
| [C: regulator fallback](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4067727437) | `test_c_regulator_fallback_wire`, exclusions/commercial banks | 2 / 6 | Generic company-suffix matches re-added 한국은행 after explicit entities excluded it. Apply the same exact three regulator exclusions after fallback extraction, allowing 은행권 to remain the measured subject. | 2 -> 1 |
| [D: agency noun](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4067727441) | `test_d_agency_mention_not_action`, action/non-action controls | 6 / 17 | 수사기관/단속기관 substrings qualified as actions and shared the actual crackdown fingerprint. Bound complete action uses and the supported action compounds/endings. | 1 -> 2 |

A supports only the demonstrated count construction, not arbitrary modifiers,
`.*`, suffix-wide identities or nearest-token ownership. Named-company, whole/
life/non-life insurance and bank/savings-bank scopes remain distinct. C excludes
only 금융감독원/금융위원회/한국은행 locally; 국민/신한/우리/하나은행 and global
entity extraction remain intact. No 한은 alias is added.

B changes neither tokenization nor merge evidence. The stripped-feature regression
still rejects a merge without ordinary evidence. No recursive suffix removal,
extra industry particles or compound adjacency changes are introduced. Distinct
P1 events and all bare-metric bridge permutations remain separate.

For D, stored titles/fixtures include 집중단속, 단속에/단속이/단속을/수사의,
단속해/단속한다 and 수사개시/수사의뢰; existing tests include 특별단속.
The local matcher accepts bounded 단속/수사, optional 집중/특별/합동/보완/인지
prefixes, one supported particle or 해/한다/했다/개시/의뢰 suffix, and standalone
잡는다. Agency nouns and ASCII/numeric continuation fail. An agency mention does
not hide a separate actual 단속 in the same headline. Authority/target spelling,
campaign periods, title-only evidence and cluster admission rules are unchanged.

### Validation and replay

New tests: **52 passed**. Related clustering/metric/enforcement/relevance/loan/
other-sector/replay suite: **998 passed, 1 skipped** (10 existing NumPy/joblib
warnings). Full Linux/Python 3.11 Docker, network disabled: **1351 passed,
1 skipped**. `git diff --check` passed. Full diff review found only issue-cluster
production edits, the new tests and this analysis section; no existing assertion
or unrelated source changed.

Fresh a353a3c BEFORE and current AFTER captures are equal in full. The fresh
baseline also equals the previous round's output except for its revision label;
historical artifacts were not rewritten.

| Date | Fixed kept / clusters (before = after) | Rescored kept / clusters | Largest | >=50 | Loan reps fixed / rescored | Merged / split pairs |
| --- | --- | --- | ---: | ---: | --- | --- |
| 09-15 | 652 / 333 | 652 / 333 | 90 | 2 | 10 / 10 | 0 / 0 |
| 09-16 | 662 / 320 | 664 / 322 | 118 | 1 | 6 / 8 | 0 / 0 |
| 09-17 | 972 / 401 | 982 / 407 | 162 | 3 | 4 / 10 | 0 / 0 |

All six cohorts retain identical kept sets, memberships, every sector's
representative counts and representative titles/URLs. All 3,327 raw candidates
retain relevance scores, hard/soft/negative terms, domain anchors, metric
identities/subjects/absolute values/periods, raw event tokens, issue terms,
fingerprints and enforcement periods. A separate raw-title scan confirms no
metric-subject or enforcement-action classification changed in these dates.
There are no changed final article/pairs to classify under A/B/C/D or unexplained
changes. Loan golden precision/recall stays **1.0000 / 0.922414** (107/116),
other-sector **1.0000 / 0.888889** (40/45); golden end-to-end output is identical.
Temporary replay/proof artifacts remain untracked under ignored `.venv`.

Remaining limits: aggregate grammar covers one decimal `N개` modifier, comparison
morphology remains bounded, and enforcement action forms are an explicit local
vocabulary, not a general Korean parser. The fifth review's industry particles
are knowingly unresolved. No registry, query/recall architecture, threshold,
ranking, relevance, ML/Gemini or delivery changes are included. Passing suites and
unchanged stored cohorts are evidence, not an automatic merge recommendation.
