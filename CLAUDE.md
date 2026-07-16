# CLAUDE.md — Finance News Monitor

This file provides guidance for AI assistants working on this codebase.

## Project Overview

**Finance News Monitor** is a Python-based automated daily aggregator of Korean domestic financial news (loan-business/대부업권 focus). It fetches articles from the Naver News Search API, runs them through a multi-stage filtering pipeline (rule pre-filter → relevance scoring/ML), tags them by financial sector and topic, clusters same-issue duplicates, generates extractive summaries, and produces Markdown + interactive HTML reports plus quality-metrics JSON. A GitHub Actions workflow runs this pipeline **every day** (multiple cron triggers around 08:41–09:17 KST, deduplicated by a sent-marker), commits the output reports back to `main`, and optionally emails the HTML report.

**Primary language**: Python 3.11+
**Domain**: Korean financial media monitoring
**Runtime**: GitHub Actions (scheduled + manual) or local CLI

---

## Repository Structure

```
finance-news-monitor/
├── src/
│   ├── config.py               # KST timezone, NaverConfig/AppConfig, env var loading
│   ├── notify_email.py         # SMTP email sender (single atomic send, envelope-BCC recipients)
│   ├── run_daily.py            # MAIN ENTRY POINT — orchestrates the full pipeline
│   ├── fetchers/
│   │   └── naver.py            # Naver News Search API client (retry/backoff, env-tunable)
│   ├── ml/
│   │   └── relevance_model.py  # joblib model loading + predict_proba wrapper
│   └── pipeline/
│       ├── content_type.py     # Article content-type classification (regulatory/risk/pr/…)
│       ├── dedup.py            # Exact-dup drop + normalized-title clustering (representative pick)
│       ├── extractive_summary.py  # Sentence scoring → 2-sentence, ≤220-char summaries
│       ├── filtering.py        # Stage 1: rule-based pre-filter (sports/entertainment/politics/lease)
│       ├── fulltext_fetch.py   # HTML fetch (charset_normalizer) + main-text extraction (BS4+lxml)
│       ├── issue_cluster.py    # Same-issue clustering across outlets (fingerprints + similarity)
│       ├── normalize.py        # Article dataclass + raw dict → Article
│       ├── quality.py          # Per-run quality metrics JSON (counts/taxonomy/clusters/top10)
│       ├── relevance_filter.py # Stage 2 relevance decision (authoritative/candidate_hybrid/rule_only)
│       ├── relevance_score.py  # Heuristic scoring (hard/soft/negative weights, negative cap)
│       ├── report.py           # Markdown + interactive HTML report, Top-10 ranking, index
│       ├── source_quality.py   # Publisher/source quality classification for ranking
│       ├── summary_cache.py    # JSON URL→summary cache (max 5000 items)
│       ├── tagger.py           # Sector (best-match) + topic (multi-label) tagging + rule overrides
│       └── text_matcher.py     # Safe Korean keyword matching (boundaries, excludes, aliases)
├── scripts/
│   ├── train_relevance.py                  # Manual: data/relevance_labels.csv → models/relevance.joblib
│   ├── generate_relevance_pseudo_labels.py # Auto pseudo-labels from reports/_candidates CSVs
│   ├── train_relevance_candidate_model.py  # Pseudo-labels → models/relevance_candidate.joblib
│   ├── refresh_relevance_candidate_model.py# CI: best-effort daily candidate-model refresh
│   ├── make_relevance_labeling_sample.py   # Optional manual labeling sample (Phase 4A)
│   ├── validate_relevance_labels.py        # Optional label validation (Phase 4A)
│   ├── phase5_delivery.py                  # CI helpers: sent-marker precheck/wait/mark-sent
│   └── prune_reports.py                    # CI: reports retention (reports 180d, artifacts 90d)
├── tests/                      # pytest suite (~30 files, run with `python -m pytest tests/`)
├── data/
│   └── relevance_labels.csv    # Manual labeled data for the operating model (optional)
├── models/                     # NOT committed by default; see "ML Models" below
├── reports/                    # Generated output (committed by CI)
│   ├── YYYY-MM-DD.md / .html   # Daily reports
│   ├── index.html              # 14-day report index
│   ├── _cache/summary_cache.json
│   ├── _candidates/YYYY-MM-DD_candidates.csv   # All stage-2 inputs with scores/decisions
│   ├── _metrics/               # relevance_filter / quality / candidate-model metrics JSON
│   └── _sent/YYYY-MM-DD_email_sent.json        # Email dedup markers
├── queries.yml                 # fetch_queries + sector/topic base keyword lists
├── requirements.txt
├── .env.example                # Template only — .env is NOT auto-loaded (see below)
└── .github/workflows/
    ├── daily.yml               # Primary daily pipeline (see CI/CD section)
    ├── smoke.yml               # Manual smoke test (runs run_daily with defaults)
    ├── train_model.yml         # Manual: train + commit models/relevance.joblib
    └── resend_test.yml         # Manual: Resend API test (separate RESEND_* secrets, not SMTP)
```

---

## Development Setup

### Prerequisites

- Python 3.11+
- Naver Developer account (for `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`)
- `pytest` for running tests (not in requirements.txt — install separately)

### Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest

cp .env.example .env   # template for your values
```

**IMPORTANT**: There is no python-dotenv integration — `.env` is **not** loaded
automatically. `src/config.py` reads `os.environ` directly, so export the
variables into your shell before running:

```bash
set -a; source .env; set +a
```

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `NAVER_CLIENT_ID` | Yes | Naver News API authentication |
| `NAVER_CLIENT_SECRET` | Yes | Naver News API authentication |
| `NAVER_HTTP_TIMEOUT_SECONDS` | No | Naver API timeout (default 10, max 60) |
| `NAVER_RETRY_ATTEMPTS` | No | Naver API retry count (default 3, max 5) |
| `NAVER_RETRY_BACKOFF_SECONDS` | No | Naver API retry backoff base (default 1, max 30) |
| `SMTP_HOST` / `SMTP_PORT` | For email | SMTP server (STARTTLS, typically 587) |
| `SMTP_USER` / `SMTP_PASS` | For email | SMTP credentials |
| `MAIL_FROM` | For email | Sender address (pure email address) |
| `MAIL_FROM_NAME` | No | Sender display name (default "금융동향봇") |
| `MAIL_TO` | For email | Comma-separated recipients (delivered via SMTP envelope only; never exposed in headers) |
| `SMTP_TIMEOUT_SECONDS` | No | SMTP timeout (default 30, max 120) |
| `MAIL_RETRY_ATTEMPTS` | No | Email retry count (default 3, max 5) |
| `MAIL_RETRY_BACKOFF_SECONDS` | No | Email retry backoff base (default 10, max 120) |
| `DEEPSEARCH_API_KEY` | No | Reserved; DeepSearch integration is NOT implemented |

`resend_test.yml` uses separate `RESEND_API_KEY` / `RESEND_FROM` / `RESEND_TO` secrets and does not touch the SMTP path.

---

## Running the Application

### Main Command

```bash
python -m src.run_daily [options]
```

### CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--date YYYY-MM-DD` | today KST | Override the target report date |
| `--window_hours N` | 24.0 | How many hours back to collect articles |
| `--end_hhmm HHMM` | 0730 | Collection window end time in KST (`0900` or `09:00`). Production uses `0855` |
| `--overlap_minutes N` | 15 | Extends window start backward as a safety margin |
| `--max_pages N` | 5 | Max Naver API pages per query (100 items/page) |
| `--dry_run` | false | Print computed time window only, no fetching |
| `--use_deepsearch` | false | Not implemented (logs a warning and continues) |
| `--disable_candidate_model` | false | Ignore `models/relevance_candidate.joblib` when no operating model exists |
| `--candidate_keep_prob` | 0.65 | Candidate hybrid keep threshold |
| `--candidate_drop_prob` | 0.35 | Candidate hybrid drop threshold |
| `--candidate_gray_keep_min_score` | 6 | Gray-zone rule-score threshold (domain-anchored keeps) |
| `--candidate_strong_rule_keep_score` | 8 | Strong domain-rule keep score threshold |
| `--candidate_no_model_keep_min_score` | 5 | No-probability rule-score threshold |

### Common Examples

```bash
# Dry run: verify time window calculation only
python -m src.run_daily --dry_run

# Production-equivalent invocation (matches daily.yml)
python -m src.run_daily --window_hours 24 --end_hhmm 0855 --overlap_minutes 15

# Run the test suite
python -m pytest tests/ -q
```

---

## Pipeline Architecture

The full pipeline runs in `src/run_daily.py::main()` in this order:

```
 1. compute_window()      [start, end) in KST; auto-date rolls back a day if end is in the future
 2. load_queries()        queries.yml → (sectors, topics, fetch_queries)
 3. fetch_news()          [fetchers/naver.py] 100 items/page, retry/backoff, window-filtered
 4. normalize()           raw dicts → Article dataclasses
 5. deduplicate()         [pipeline/dedup.py] exact dups dropped; normalized-title clusters →
                          one representative per cluster (newer / longer description preferred)
 6. filter_articles()     [pipeline/filtering.py] Stage 1 rule pre-filter:
                          sports/entertainment domains+keywords, politics-only,
                          public-lease "대부" context; strong finance anchors rescue
 7. filter_relevance()    [pipeline/relevance_filter.py] Stage 2 — see "Relevance Policies".
                          Writes reports/_candidates/*.csv and
                          reports/_metrics/*_relevance_filter_metrics.json
 8. tag_articles()        [pipeline/tagger.py] best-match sector + multi-label topics
 9. cluster_tagged_articles()  [pipeline/issue_cluster.py] same-issue clustering across
                          outlets (fingerprints, entity/number overlap, title similarity);
                          only cluster representatives continue; related articles attached
10. apply_extractive_summaries()  [run_daily.py] newest-first; cache hit → reuse;
                          else fetch_html + extract_main_text + summarize_with_fallback
                          (2 sentences, ≤220 chars). Caps: MAX_SUMMARIZE=80 successes,
                          MAX_SUMMARY_FETCH_ATTEMPTS=160 network attempts (failures count).
                          Cache: reports/_cache/summary_cache.json (max 5000 entries)
11. tag_articles() again  re-tag representatives so sector/topic reflect the final summary text
12. build_quality_metrics()  [pipeline/quality.py] → reports/_metrics/*_quality_metrics.json
13. keyword_trends() + render_markdown() + render_html() + write_report() + write_index()
```

### Relevance Policies (`filter_relevance`)

`choose_relevance_model_policy()` in `run_daily.py` picks one of three policies:

| Policy | When | Decision |
|--------|------|----------|
| `authoritative` | `models/relevance.joblib` exists | keep if ML prob ≥ 0.55; rule fallback if scoring fails |
| `candidate_hybrid` | no operating model, `models/relevance_candidate.joblib` exists | ML prob ≥ 0.65 keep / ≤ 0.35 drop, guarded by domain anchors, overseas-impact rules, noise filters; gray zone falls back to rule score (≥ 6 with domain anchor) |
| `rule_only` | no model available | keep if heuristic score ≥ 4 (`min_score=4` set in `run_daily.py`) |

Heuristic scoring (`relevance_score.py`): weighted hard/soft/negative term lists.
Without any hard finance anchor the score is capped at 2 (below threshold).
A negative-score cap applies when strong finance context coexists with only
"capped noise" terms (SNS/유튜브/행사 등) — this cap is the **only** place that
carve-out lives; `_rule_decide_relevance` intentionally has no such branch.

---

## Key Data Structures

`Article` is defined in `src/pipeline/normalize.py`; `TaggedArticle` in `src/pipeline/tagger.py`:

```python
@dataclass
class Article:
    title: str
    description: str      # Naver snippet, later replaced by extractive summary
    link: str             # Primary clickable link (original preferred)
    originallink: str | None
    naver_link: str | None
    pub_date: datetime
    query: str
    # + relevance/cluster fields (relevance_score, cluster_id, cluster_size, …)

@dataclass
class TaggedArticle:
    article: Article
    sectors: list[str]           # Best-match sector (usually one; "기타" fallback)
    topics: list[str]            # Multi-label topics
    matched_keywords: list[str]
```

**Caveat**: several pipeline stages attach extra attributes to `Article` via
`setattr` (`decision`, `keep`, `summary_cached`, `model_used`, …). When reading
such fields, use `getattr(article, key, None)`-style access like the existing
`_field` helpers do.

---

## Configuration: `queries.yml`

Controls three things:

- **`fetch_queries`**: ~45 high-precision Korean search terms sent to the Naver API.
- **`sectors`**: 15 sectors (incl. `기타`) with base keyword lists.
- **`topics`**: 24 cross-cutting topics with base keyword lists.

**IMPORTANT**: queries.yml provides only the *base* keyword lists. The actual
tagging behavior is heavily shaped by hardcoded rules in `src/pipeline/tagger.py`
(`SECTOR_RULE_OVERRIDES`, `TOPIC_RULE_OVERRIDES`, `TOPIC_CONTEXT_TOKENS`,
`TOPIC_SECTOR_AFFINITY`, score adjustments). When adding sectors/topics or
keywords, check whether a matching override entry is needed there as well.

### Ambiguous Korean terms

Safe matching is centralized in `src/pipeline/text_matcher.py`:
- Short Korean terms (≤3 chars) get token-boundary matching, so e.g. `은행`
  does NOT match inside `국민은행` — compound terms must be listed explicitly.
- Special cases with context guards/excludes: `신협`(vs 여신협회), `금융위`(vs
  금융위기), `보험`(vs 건강/고용/산재보험), `감독`/`경기`(only in sports context),
  aliases `cp`↔기업어음, `킥스`↔k-ics.
- `tagger.py` additionally guards `대부`/`여전` (token mode) and `리스`
  (excludes `리스크`). Do not simplify these — they prevent significant noise.

---

## ML Models

Two separate models; neither is currently committed to the repository:

1. **Operating model** `models/relevance.joblib` — trained manually from
   human labels (`data/relevance_labels.csv`, ≥40 rows) via
   `scripts/train_relevance.py` (TF-IDF char 2–5-grams + LogisticRegression),
   committed via the `train_model.yml` workflow. When present, policy is
   `authoritative`.
2. **Candidate model** `models/relevance_candidate.joblib` — refreshed
   best-effort at the start of every daily CI run by
   `scripts/refresh_relevance_candidate_model.py`, trained on conservative
   **pseudo-labels** derived from past `reports/_candidates/*.csv` (the current
   report date is excluded). It is regenerated per-run and NOT committed.
   Never copy the candidate model over the operating model path.

If neither model exists or loading/prediction fails, the run continues with
`rule_only` — do not raise.

**Model input format**: both models are trained AND served with
`model_input_text(title, summary)` from `src/ml/relevance_model.py`
(title repeated twice for TF-IDF weighting). Never build model input text
inline — training/serving skew silently corrupts probabilities and metrics.

---

## CI/CD Workflows (`.github/workflows/`)

| File | Trigger | What it does |
|------|---------|--------------|
| `daily.yml` | Cron **every day**: 23:41/23:49/23:57 UTC + 00:07/00:17 UTC (= KST 08:41/08:49/08:57/09:07/09:17); `workflow_dispatch` | phase5 sent-marker precheck → optional wait until ~08:55 KST → candidate model refresh → `run_daily --end_hhmm 0855` → email (if secrets + no marker) → write marker → prune old reports → commit `reports/` to `main` (rebase+push retried 3x) |
| `smoke.yml` | `workflow_dispatch` | `python -m src.run_daily` with defaults (no email/commit) |
| `train_model.yml` | `workflow_dispatch` | Train + commit `models/relevance.joblib` |
| `resend_test.yml` | `workflow_dispatch` | One-off Resend API email test (RESEND_* secrets) |

Notes:
- The multiple cron triggers exist because GitHub cron timing is unreliable.
  The sent-marker (`reports/_sent/YYYY-MM-DD_email_sent.json`) prevents
  duplicate scheduled runs **after a successful email send**; report
  generation itself may still run multiple times a day.
- Concurrency is a single group with `cancel-in-progress: false` (do not change).
- The daily workflow commits reports directly to `main` with
  `git push origin HEAD:main`.
- Manual runs default to no email; `send_email=true` to send, plus
  `force_send=true` to re-send when a marker already exists.

---

## Code Conventions

### Style

- **Type hints everywhere**: `from __future__ import annotations` at the top of each file
- **Dataclasses** for data models
- **No global mutable state**: pass config/data explicitly through function arguments
- **Logging**: `logging.getLogger(__name__)`; root logger configured in `run_daily.py` at INFO

### Error Handling

- **Network errors during summarization**: catch silently and skip (best-effort),
  but respect the fetch-attempt cap (`MAX_SUMMARY_FETCH_ATTEMPTS`)
- **Missing/broken ML model**: fall back (candidate_hybrid → rule paths, or rule_only); never raise
- **Candidate model refresh failures**: best-effort; the daily report must still generate
- **Email**: a single atomic SMTP send to all recipients (envelope BCC).
  Partial recipient refusal raises and is retried; the sent marker is only
  written after a successful send. Do not reintroduce per-recipient send loops —
  they break idempotency across workflow re-runs.
- **Email requires today's report**: if `reports/<today>.html` is missing,
  `notify_email.main()` raises instead of sending — no fallback to older
  reports, no attachment-less sends. The next cron run regenerates and retries.
- **Metrics writing failures**: log a warning, continue
- **Naver API failures in CI**: propagate after retries so the workflow fails visibly

---

## Test Suite

Tests live in `tests/` (~30 files, pytest). Run with:

```bash
python -m pytest tests/ -q
```

Conventions: files named `test_*.py`; no network access (SMTP/HTTP are faked
via monkeypatch). The suite must pass before pushing. Some files are named
after project phases (`test_phase8c_candidate_hybrid.py` etc.) — treat the
assertions, not the phase names, as the spec.

Additional quality checks: CI smoke test (`smoke.yml`), manual review of
`reports/_candidates/*.csv` and `reports/_metrics/*.json`.

---

## Reports Directory

Committed to the repository by CI. Do not manually edit files in `reports/` —
they are regenerated by the pipeline.

Retention (`scripts/prune_reports.py`, run by `daily.yml` before commit):
daily reports are kept 180 days; `_candidates`/`_metrics`/`_sent` files 90 days
(safely above the candidate model's 21-day training lookback). `index.html` and
`_cache/` are never pruned.

| Path | Description |
|------|-------------|
| `reports/YYYY-MM-DD.md` / `.html` | Daily reports (HTML has theme/sort/filter/favorites UI) |
| `reports/index.html` | Index of the 14 most recent reports |
| `reports/_cache/summary_cache.json` | Persistent URL→summary cache (max 5000 entries) |
| `reports/_candidates/YYYY-MM-DD_candidates.csv` | All stage-2 inputs with scores/probs/decisions |
| `reports/_metrics/…` | Relevance-filter, quality, and candidate-model metrics |
| `reports/_sent/YYYY-MM-DD_email_sent.json` | Email sent markers (dedup across cron triggers) |

---

## External APIs

### Naver News Search API

- **Base URL**: `https://openapi.naver.com/v1/search/news.json`
- **Auth**: `X-Naver-Client-Id` + `X-Naver-Client-Secret` headers
- **Pagination**: `display=100`, `start=1/101/201/...` (max 1000 results per query)
- **Sort**: always `sort=date`; paging stops early once a page is older than the window
- **Retry**: 429/5xx and timeouts retried with exponential backoff (env-tunable)

### Full-Text Scraping

`requests` + `charset_normalizer` (byte-level decoding to avoid mojibake) +
`BeautifulSoup`/`lxml`. Naver News pages (`naver_link`) are preferred since their
DOM is well-known; generic sites fall back to `<article>`/`<main>` and a scored
container heuristic. Chrome 120 User-Agent. Scraped bodies are never stored —
only the derived summary is cached.

---

## gstack

Use the `/browse` skill from gstack for **all web browsing**. Never use `mcp__claude-in-chrome__*` tools.

### Available Skills

| Skill | Purpose |
|-------|---------|
| `/office-hours` | Brainstorm new ideas |
| `/plan-ceo-review` | Review a plan (strategy) |
| `/plan-eng-review` | Review a plan (architecture) |
| `/plan-design-review` | Review a plan (design) |
| `/design-consultation` | Create a design system |
| `/review` | Code review before merge |
| `/ship` | Deploy / create PR |
| `/land-and-deploy` | Land and deploy changes |
| `/canary` | Canary deployment |
| `/benchmark` | Run benchmarks |
| `/browse` | Headless web browsing and QA |
| `/qa` | Test the app |
| `/qa-only` | QA testing only |
| `/design-review` | Visual design audit |
| `/setup-browser-cookies` | Configure browser cookies |
| `/setup-deploy` | Set up deployment |
| `/retro` | Weekly retrospective |
| `/investigate` | Debug errors |
| `/document-release` | Post-ship doc updates |
| `/codex` | Second opinion / adversarial code review |
| `/cso` | Chief Security Officer review |
| `/autoplan` | Auto-review a plan (all reviews at once) |
| `/careful` | Working with production or live systems |
| `/freeze` | Scope edits to one module/directory |
| `/guard` | Maximum safety mode |
| `/unfreeze` | Remove edit restrictions |
| `/gstack-upgrade` | Upgrade gstack to latest version |
