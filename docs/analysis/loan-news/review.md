# PR #85 automated-review follow-up

Historical analysis for commit `3de826a`. The ablations and numbers below are
frozen observations, not the output contract of today's replay tool. See
[latest-review.md](latest-review.md) for the current executable comparison and
fresh-clone validation. Do not overwrite `review-replay.json` with a new run.

This supersedes the implementation design in the original analysis. Baseline:
`e67e3a740b0004e56a0087aa34edfeaffc6264ae`; reviewed first implementation:
`1b4b732c0f4005a7fab80ced58a7fbc9ff658e9e`. No generated reports, query lists,
ML policy, scoring weights, representative ranking or production dependencies
were changed in this follow-up.

## Review verdicts and minimal reproductions

All four inline comments from `chatgpt-codex-connector[bot]` were read (review
5230344588; no additional issue comments). All four are valid. Before production
edits, the 12-case minimal reproduction suite failed 10 cases and passed two
positive controls. The final review suite has 19 parametrized cases.

| Review | Evidence at first implementation | Change |
| --- | --- | --- |
| [Background enforcement](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4032528507) | A policy-proposal headline whose snippet mentions Seoul's market crackdown receives `enforcement:서울시:small_business`. Removing that fingerprint alone still merges through shared snippet terms. | Require illegal-lending and enforcement action in the headline; prevent fallback similarity from merging an enforcement fingerprint with a non-enforcement headline. Snippets may supplement actor/target only after the headline event check. |
| [Place suffix](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4032528521) | `필요시`, `검토시`, `대출시` each become a fake authority; adding one alongside 서울시 removes its otherwise valid singleton authority. | Recognize named metropolitan administrations and explicit 시청/경찰청 suffixes. Normalize full metropolitan names; unknown bare city abbreviations receive no shortcut. |
| [Measurement subject](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4032528515) | 삼성생명/한화생명 200% headlines merge because both entity sets are empty. Even known 가온보험/나래보험 subjects can re-merge through generic similarity after the shortcut refuses them. | Require positive same-subject evidence before the measurement for the shortcut. Extract insurance-company suffixes locally, exclude regulators, and recognize explicit industry aggregates. Conflicting metric subjects veto pair similarity/fingerprints and cluster admission, including bridges through an unknown subject. Later company/subsector comparisons do not redefine the measurement's subject. |
| [Metric whitespace](https://github.com/zetatech-a/finance-news-monitor/pull/85#discussion_r4032528511) | `K-ICS 비율`, `K-ICS비율`, `킥스 비율` yield no reported metric. | Allow an optional spaced 비율 after both K-ICS and 킥스. Retain exclusion of percentage-point changes; test parsing and same-event clustering. |

Positive controls cover same-company aliases, post-value comparisons, actual
city halls/police agencies and full metropolitan names. Negative controls cover
shared regulators, absent metric subjects and all permutations of an insurance
subject bridge. Existing loan bridge/golden tests remain intact. The existing
six high-model-probability noise controls still drop island, public-property
lease, lease fee, entertainer, movie and 대부분 headlines. Safe aliases have
not been broadened or changed during this follow-up.

## Why 162 became 176

One-factor ablations use the exact recorded 972 kept candidates on 09-17:

| First-implementation variant | Largest | Clusters |
| --- | ---: | ---: |
| Unchanged (global title sort + complete compatibility) | 176 | 449 |
| Disable reported metrics | 176 | 455 |
| Restore baseline fingerprints | 176 | 448 |
| Restore input order only | 169 | 450 |
| Restore single-link only (still globally sorted) | 184 | 394 |
| Restore both input order and single-link | 162 | 401 |
| Baseline clustering with revised alias/tagging | 162 | 400 |

The largest group's first-PR membership retains 155 of the original 162,
removes 7, and imports all 21 members of another baseline group: 155 + 21 = 176.
All 21 imports have the pre-existing `macro:bok_base_rate` fingerprint. They
include US rate decisions, issuance-product promotions and market commentary;
this is not a pure event identifier. Same-fingerprint admission bypasses title
similarity, so complete-link does not protect against this broad shortcut.
Changing greedy admission also changes where later articles first fit; groups
are not monotone refinements of baseline groups. Sorting and admission interact.
The metric shortcut and new loan fingerprints are not the cause. Exact imported
and removed titles and all ablation results are in `review-replay.json`.

## Scope and precision/fragmentation decision

Global complete compatibility is removed. Complete compatibility and stable
ordering apply only to lending-sector/illegal-lending/illegal-collection/loan-ad
candidates and the debt-purchase fingerprint. Lending indices are sorted within
their original slots; other candidates retain original order and single-link
admission. Explicit metric-subject conflicts are a narrow cluster-wide veto
across sectors. This keeps the lending A–B–C pollution guard while minimizing
unrelated changes. It does not claim order independence for unrelated sectors.

The additional 18-row labelled sample covers three independent real events:
Jeju Bank transfer protection, Kyobo/Lifeplanet merger, and KB anti-phishing
awards. Pair recall is 40/45 at base, 20/45 with global complete compatibility,
and 40/45 after narrowing; pair precision is 1.0 for all three. This is direct
fragmentation evidence supporting the narrower design, not an inference from
cluster count alone. The original 33-relevant-row loan golden retains precision
1.0 and recall 107/116. Nine missed pairs remain in the conservatively split
Bucheon sentence event; five missed other-sector pairs reflect the existing
Jeju split. Those are measured limitations, not a claim of perfect recall.

## Top-10 inspection and remaining risks

[review-top10.md](review-top10.md) lists each day's ten largest clusters at base,
first PR and revised versions, including representative, sector, size,
fingerprint and member-title samples. JSON also retains fingerprint histograms
and exact membership indices (zero-based among recorded kept rows).

Base → revised: 09-15 introduces zero merged pairs and removes 28 lending pairs;
09-16 changes no pairs. On 09-17, the only 43 new pairs are in the insurance
industry June-end capital-ratio report: its cluster grows 21→23. All 23 member
headlines/snippets were inspected: the two added articles refer to the same
June-end 215.2%, 0.8 percentage-point decrease announcement. No new obvious
cross-event merge was found against baseline in this comparison. This is a
manual check of the changed pairs, not precision ground truth for the corpus.

Exact revised top-10 memberships match baseline top-10 groups for 9/10 on
09-15, 10/10 on 09-16 and 8/10 on 09-17; remaining rank slots change as loan
groups split and the insurance group grows. Among the 60 split pairs on 09-17,
59 are lending pairs and one is an insurance same-announcement pair:
`보험사 지급여력비율 '주춤'…생보↓·손보↑` versus
`보험사 지급여력비율 215.2%로 하락…생·손보 엇갈린 희비`.
The latter joins the earlier 23-article group while the former lacks a numeric
headline anchor and stays separate. This is a small observed fragmentation
cost of the retained greedy admission, not an improvement in purity. Resolving
it by wholesale group union would reopen the bridge problem.

Existing broad macro and digital groups remain impure. Examples: the 09-15 STO
group combines the Hana/Upbit deal with other STO/RWA business stories; the
09-16 BOK-rate group contains US housing, a domestic policy meeting review and
market commentary; the 09-17 BOK group is represented by a Kospi trading-volume
headline. The revised code restores baseline memberships in those areas,
including some old false pairs removed by the first PR. It must not be described
as a corpus-wide purity improvement over that first PR. Tightening those old
fingerprints requires a separate labelled evaluation; it is outside this loan
fix. Same authority/target enforcement and same-subject/value metrics also
remain heuristics, not unique event IDs across time. The limited place/subject
recognition can miss paraphrases or unrecognized names. Do not increase recall
by reverting to missing-subject or bare-city-suffix matching.

## Validation and reproducibility

- Before edits: review reproductions **10 failed, 2 passed**.
- Golden + all issue-clustering tests after edits: **60 passed** on Windows.
- Golden + all issue-clustering/relevance/text-matcher/Phase 9A tests on Linux:
  **103 passed**.
- Full `python -m pytest tests/ -q -p no:cacheprovider` on Python 3.11 Linux:
  **745 passed, 1 skipped** (17.39 seconds). Docker used the existing local
  dependency image, read-only worktree/Git mounts and `--network none`.
- `git diff --check`: passed. Complete source and evidence diff reviewed.
- `python -m scripts.evaluate_cluster_review --output docs/analysis/loan-news/review-replay.json`:
  completed all three dates, three versions, ablations and labelled cohorts.

The primary comparison fixes recorded keep decisions and scores, isolating
clustering/tagging effects. Separate revised rescoring uses recorded rounded
probabilities; the original golden has 33/33 relevant kept and 4/4 negatives
dropped. Collection recall cannot be inferred from stage-2 candidate CSVs.
No live API/model/report regeneration was run. Missing publication timestamps,
query provenance, dedup-absorbed sources and post-extractive-summary re-tagging
mean these candidate-level representative counts are not final HTML counts.
No production-equivalent run is claimed.

## Three-day fixed-cohort replay

| Date | Version | Kept | Clusters | Largest | >=50 | Loan reps |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-15 | base | 652 | 326 | 90 | 2 | 2 |
| 2026-09-15 | first | 652 | 350 | 84 | 2 | 10 |
| 2026-09-15 | revised | 652 | 333 | 90 | 2 | 10 |
| 2026-09-16 | base | 662 | 320 | 118 | 1 | 6 |
| 2026-09-16 | first | 662 | 360 | 111 | 1 | 6 |
| 2026-09-16 | revised | 662 | 320 | 118 | 1 | 6 |
| 2026-09-17 | base | 972 | 400 | 162 | 3 | 2 |
| 2026-09-17 | first | 972 | 449 | 176 | 2 | 4 |
| 2026-09-17 | revised | 972 | 401 | 162 | 3 | 4 |

| Date | Comparison | New merged pairs | New split pairs |
| --- | --- | ---: | ---: |
| 2026-09-15 | base_to_first | 7 | 931 |
| 2026-09-15 | base_to_revised | 0 | 28 |
| 2026-09-15 | first_to_revised | 903 | 7 |
| 2026-09-16 | base_to_first | 63 | 1524 |
| 2026-09-16 | base_to_revised | 0 | 0 |
| 2026-09-16 | first_to_revised | 1524 | 63 |
| 2026-09-17 | base_to_first | 3352 | 4144 |
| 2026-09-17 | base_to_revised | 43 | 60 |
| 2026-09-17 | first_to_revised | 4098 | 3323 |

## Representatives by sector

Cells are base → first → revised, before extractive-summary re-tagging.

| Sector | 09-15 | 09-16 | 09-17 |
| --- | --- | --- | --- |
| IB·자본시장 | 1 → 1 → 1 | 4 → 4 → 4 | 1 → 1 → 1 |
| 감독·제재 | 8 → 9 → 8 | 2 → 3 → 2 | 18 → 24 → 18 |
| 거시·시장 | 22 → 23 → 22 | 41 → 47 → 41 | 45 → 47 → 45 |
| 기타 | 123 → 125 → 122 | 108 → 121 → 108 | 129 → 135 → 129 |
| 대부 | 2 → 10 → 10 | 6 → 6 → 6 | 2 → 4 → 4 |
| 디지털자산 | 79 → 90 → 79 | 63 → 70 → 63 | 77 → 91 → 77 |
| 보험 | 2 → 2 → 2 | 9 → 13 → 9 | 31 → 34 → 30 |
| 상호금융 | 9 → 9 → 9 | 4 → 5 → 4 | 6 → 7 → 6 |
| 여전 | 10 → 10 → 10 | 13 → 13 → 13 | 10 → 10 → 10 |
| 은행 | 28 → 29 → 28 | 36 → 40 → 36 | 35 → 46 → 35 |
| 입법·정책 | 23 → 23 → 23 | 27 → 31 → 27 | 26 → 26 → 26 |
| 자산운용·연기금 | 4 → 4 → 4 | 3 → 3 → 3 | 3 → 3 → 3 |
| 저축은행 | 14 → 14 → 14 | 2 → 2 → 2 | 8 → 8 → 8 |
| 증권(브로커리지/리테일) | 1 → 1 → 1 | 1 → 1 → 1 | 1 → 1 → 1 |
| 핀테크·플랫폼 | 0 → 0 → 0 | 1 → 1 → 1 | 8 → 12 → 8 |

## Labelled pair quality (fixed relevant cohort)

| Dataset | Version | Articles | Clusters | Correct / predicted / expected pairs | Precision | Recall |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| golden_fixed_relevant_cohort | base | 33 | 10 | 74 / 133 / 116 | 0.5564 | 0.6379 |
| golden_fixed_relevant_cohort | first | 33 | 11 | 107 / 107 / 116 | 1.0000 | 0.9224 |
| golden_fixed_relevant_cohort | revised | 33 | 11 | 107 / 107 / 116 | 1.0000 | 0.9224 |
| other_sectors_labelled | base | 18 | 4 | 40 / 40 / 45 | 1.0000 | 0.8889 |
| other_sectors_labelled | first | 18 | 7 | 20 / 20 / 45 | 1.0000 | 0.4444 |
| other_sectors_labelled | revised | 18 | 4 | 40 / 40 / 45 | 1.0000 | 0.8889 |

## Rescored candidate replay (revised)

| Date | Kept | Clusters | Largest | >=50 | Loan reps |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-15 | 652 | 333 | 90 | 2 | 10 |
| 2026-09-16 | 664 | 322 | 118 | 1 | 8 |
| 2026-09-17 | 982 | 407 | 162 | 3 | 10 |
