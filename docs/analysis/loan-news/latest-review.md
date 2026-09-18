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
