# CLAUDE.md — Finance News Monitor

This file provides guidance for AI assistants working on this codebase.

## Project Overview

**Finance News Monitor** is a Python-based automated daily aggregator of Korean domestic financial news. It fetches articles from the Naver News Search API, runs them through a multi-stage filtering pipeline, tags them by financial sector and topic, optionally generates extractive summaries, and produces Markdown + interactive HTML reports. A GitHub Actions workflow runs this pipeline every weekday morning at 09:05 KST and commits the output reports back to the repository.

**Primary language**: Python 3.11+
**Domain**: Korean financial media monitoring
**Runtime**: GitHub Actions (scheduled + manual) or local CLI

---

## Repository Structure

```
finance-news-monitor/
├── src/                        # All application source code
│   ├── __init__.py
│   ├── config.py               # KST timezone, dataclasses, env var loading
│   ├── notify_email.py         # SMTP email sender (phase 2, continue-on-error in CI)
│   ├── run_daily.py            # MAIN ENTRY POINT — orchestrates the full pipeline
│   ├── fetchers/
│   │   └── naver.py            # Naver News Search API client
│   ├── ml/
│   │   ├── __init__.py
│   │   └── relevance_model.py  # Loads models/relevance.joblib, runs predict_proba()
│   └── pipeline/
│       ├── dedup.py            # Deduplication by title + canonical link
│       ├── extractive_summary.py  # TF-IDF sentence scoring for 3-sentence summaries
│       ├── filtering.py        # Stage 1: rule-based pre-filter
│       ├── fulltext_fetch.py   # HTML fetching + main-text extraction (BS4 + lxml)
│       ├── normalize.py        # Article / TaggedArticle dataclasses
│       ├── relevance_filter.py # Stage 2: dual-gate (ML prob OR heuristic score)
│       ├── relevance_score.py  # Heuristic scoring (hard/soft/negative weights)
│       ├── report.py           # Markdown + interactive HTML report generation
│       ├── summary_cache.py    # JSON-based URL→summary cache (max 5000 items)
│       └── tagger.py           # Sector (best-match) + topic (multi-label) tagging
├── scripts/
│   └── train_relevance.py      # Offline ML training: CSV → models/relevance.joblib
├── data/
│   └── relevance_labels.csv    # Labeled training data (id,date,title,summary,url,label)
├── reports/                    # Generated output (committed by CI)
│   ├── YYYY-MM-DD.md           # Daily markdown report
│   ├── YYYY-MM-DD.html         # Daily interactive HTML report
│   ├── index.html              # 14-day report index
│   ├── _cache/
│   │   └── summary_cache.json  # Persistent summary cache across runs
│   └── _candidates/
│       └── YYYY-MM-DD_candidates.csv  # All filtered candidates with scores
├── queries.yml                 # Search queries + sector/topic taxonomy (CRITICAL CONFIG)
├── requirements.txt            # Python dependencies
├── .env.example                # Required environment variables template
├── .gitignore
├── README.md                   # Korean user documentation
├── SPEC.md                     # Korean product specification
└── .github/workflows/
    ├── daily.yml               # Primary: weekday 09:05 KST cron
    ├── smoke.yml               # Manual smoke test
    ├── train_model.yml         # Manual ML model training
    └── resend_test.yml         # Manual email API test
```

---

## Development Setup

### Prerequisites

- Python 3.11+
- Naver Developer account (for `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`)

### Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with real Naver API credentials
```

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `NAVER_CLIENT_ID` | Yes | Naver News API authentication |
| `NAVER_CLIENT_SECRET` | Yes | Naver News API authentication |
| `DEEPSEARCH_API_KEY` | No | DeepSearch integration (not yet implemented) |
| `SMTP_HOST` | No | Email sending (phase 2) |
| `SMTP_PORT` | No | Email sending (phase 2) |
| `SMTP_USER` | No | Email sending (phase 2) |
| `SMTP_PASS` | No | Email sending (phase 2) |
| `MAIL_FROM` | No | Email sending (phase 2) |
| `MAIL_TO` | No | Comma-separated recipients (phase 2) |

Config is loaded in `src/config.py::load_config()` via `os.environ`.

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
| `--end_hhmm HHMM` | 0730 | Collection window end time in KST (e.g., `0900`) |
| `--overlap_minutes N` | 15 | Extends window start backward as a safety margin |
| `--max_pages N` | 5 | Max Naver API pages per query (100 items/page → max 500/query) |
| `--dry_run` | false | Print computed time window only, no actual fetching |
| `--use_deepsearch` | false | Not yet implemented |

### Common Examples

```bash
# Dry run: verify time window calculation only
python -m src.run_daily --dry_run

# Standard production invocation (matches daily.yml CI)
python -m src.run_daily --window_hours 24 --end_hhmm 0900 --overlap_minutes 15

# Backfill a specific date
python -m src.run_daily --date 2026-02-20 --window_hours 24 --end_hhmm 0900

# Train ML model (after labeling data/relevance_labels.csv)
python scripts/train_relevance.py
```

---

## Pipeline Architecture

The full pipeline runs in `src/run_daily.py` in this order:

```
1. compute_window()
   Determines [start_kst, end_kst) collection range.
   end = today@end_hhmm KST; start = end - window_hours - overlap_minutes

2. load_queries()
   Parses queries.yml → (sectors dict, topics dict, fetch_queries list)
   Supports both legacy format and new fetch_queries block.

3. fetch_news()    [src/fetchers/naver.py]
   Calls Naver News Search API for each fetch_query.
   100 items/page, up to max_pages pages, filtered by pub_date in window.

4. normalize()    [src/pipeline/normalize.py]
   Converts raw API dicts → Article dataclasses.

5. deduplicate()  [src/pipeline/dedup.py]
   Drops exact duplicates by (title.lower() ∪ canonical_link).
   canonical_link priority: naver_link > originallink > link

6. filter_articles()    [src/pipeline/filtering.py]
   Stage 1 rule-based pre-filter. Drops: sports/entertainment domains,
   pure politics articles, lease-context "대부" mentions, etc.

7. filter_relevance()   [src/pipeline/relevance_filter.py]
   Stage 2 dual-gate filter:
   - If models/relevance.joblib exists: keep if ML prob >= 0.55 OR score >= 2
   - Else: keep if heuristic score >= 2 (src/pipeline/relevance_score.py)
   Exports all candidates with scores to reports/_candidates/YYYY-MM-DD_candidates.csv

8. tag_articles()   [src/pipeline/tagger.py]
   Assigns one best sector and zero or more topics per article.

9. Extractive summarization    [src/pipeline/extractive_summary.py]
   Processes up to 80 most recent articles.
   fetch_html() + extract_main_text() → summarize() (3 sentences, max 320 chars)
   Results cached in reports/_cache/summary_cache.json

10. keyword_trends()
    Top 10 matched keywords across all tagged articles.

11. Report generation    [src/pipeline/report.py]
    render_markdown() → reports/YYYY-MM-DD.md
    render_html()     → reports/YYYY-MM-DD.html  (dark/light theme, filter UI)
    write_index()     → reports/index.html (14-day links)
```

---

## Key Data Structures

Defined in `src/pipeline/normalize.py`:

```python
@dataclass
class Article:
    title: str
    description: str      # Naver snippet or extracted summary
    link: str             # Primary clickable link (original preferred)
    originallink: str | None
    naver_link: str | None
    pub_date: datetime
    query: str            # The fetch_query that matched this article

@dataclass
class TaggedArticle:
    article: Article
    sectors: list[str]           # Best-match sector (usually one)
    topics: list[str]            # Multi-label topics (zero or more)
    matched_keywords: list[str]  # Union of sector + topic keyword matches
```

---

## Configuration: `queries.yml`

This is the most important configuration file. It controls:

- **`fetch_queries`**: The ~40 search terms sent to the Naver API. These should be high-precision and specific to Korean financial news (e.g., `"대부업 최고금리"`, `"PF 브릿지론"`, `"회사채 스프레드"`).

- **`sectors`**: 15 financial sectors, each with a list of Korean keywords for classification. One best-matching sector is assigned per article.

- **`topics`**: 13 cross-cutting topics (e.g., `부동산PF`, `불법사금융`, `소비자보호`) with keywords. Multiple topics can match a single article.

When editing `queries.yml`, be careful with these ambiguous Korean terms that have special regex handling in `src/pipeline/tagger.py`:
- `리스` → uses `(?!크)` lookahead to exclude `리스크` (risk)
- `대부` → word-boundary check to exclude lease-contract context (`공유재산 대부계약`)
- `여전` → similar word-boundary enforcement

---

## ML Model (Optional)

The ML relevance filter at `src/ml/relevance_model.py` uses:
- **Algorithm**: TF-IDF (character n-grams, analyzer='char_wb') + Logistic Regression
- **Input features**: `title + " " + summary` (Korean text)
- **Output**: Binary classification (relevant=1 / not relevant=0), probability score
- **Threshold**: `prob >= 0.55` to keep article (OR heuristic score >= 2)
- **Serialization**: `joblib`, stored at `models/relevance.joblib`

**To train the model**:
1. Add labeled rows to `data/relevance_labels.csv` (columns: `id,date,title,summary,url,label`)
2. Run `python scripts/train_relevance.py`
3. Commit `models/relevance.joblib`

If `models/relevance.joblib` does not exist, the system falls back to heuristic scoring only.

---

## Heuristic Relevance Scoring (`src/pipeline/relevance_score.py`)

Articles are scored before the ML model (and used as fallback when no model exists). The score is computed from three weighted term lists:

- **Hard terms** (finance-specific, high weight): e.g., `대출`, `금리`, `보험료`, `회사채`, `PF`
- **Soft terms** (contextually relevant, lower weight): e.g., `경기`, `소비자`, `공정위`
- **Negative terms** (penalize irrelevant articles): e.g., `축구`, `아이돌`, `드라마`, `공모전`

Keep threshold: **score >= 2** (conservative to minimize false negatives).

---

## CI/CD Workflows (`.github/workflows/`)

| File | Trigger | What it does |
|------|---------|--------------|
| `daily.yml` | Cron Mon–Fri 00:05 UTC | Full pipeline run → commit reports → push to `main` |
| `smoke.yml` | `workflow_dispatch` | Same as daily, no args override |
| `train_model.yml` | `workflow_dispatch` | Train ML model, commit `models/relevance.joblib` |
| `resend_test.yml` | `workflow_dispatch` | Test Resend/SMTP email API |

The daily workflow commits generated reports directly to `main` with `git push origin HEAD:main`. Concurrency is set to a single instance with `cancel-in-progress: false` (do not change this).

---

## Code Conventions

### Style

- **Type hints everywhere**: Use `from __future__ import annotations` at the top of each file
- **Dataclasses** for all data models (use `@dataclass`, prefer frozen where appropriate)
- **No global mutable state**: pass config/data explicitly through function arguments
- **Logging**: Use `logging.getLogger(__name__)` in modules; the root logger is configured in `run_daily.py` at INFO level

### Error Handling

- **Network errors during summarization**: Catch silently and skip (full-text fetch is best-effort)
- **Missing ML model**: Fall back to heuristic scoring; do not raise
- **Email failures**: `continue-on-error: true` in CI; do not block report generation
- **API failures in CI**: Let them propagate so the workflow fails visibly

### Keyword Matching Pattern

Special regex patterns for ambiguous Korean terms (see `src/pipeline/tagger.py` and `src/pipeline/filtering.py`):

```python
# Exclude "리스크" when matching "리스"
re.search(r'리스(?!크)', text)

# Exclude lease-context "대부"
re.search(r'대부(?!계약|료)', text)
```

Do not simplify these patterns — they prevent significant false positive noise.

### Adding New Search Queries

Edit `queries.yml` under `fetch_queries`. Keep queries:
- Specific enough to return finance-relevant results (low noise)
- In Korean (the primary language of target articles)
- Focused on actionable financial news (regulation changes, rate movements, incident reports)

### Adding New Sectors or Topics

Edit `queries.yml` under `sectors` or `topics`. Then verify:
1. No keyword conflicts with the ambiguous-term regex patterns
2. The new keywords do not appear in `filtering.py`'s exclusion lists

---

## No Test Suite

There are currently **no unit or integration tests**. Quality is maintained through:
- CI smoke tests (`smoke.yml`) that run the full pipeline on demand
- Manual review of `reports/_candidates/YYYY-MM-DD_candidates.csv` to assess filter quality
- Manual review of generated HTML reports

If adding tests, use `pytest`. Place test files in a `tests/` directory and name them `test_*.py`.

---

## Reports Directory

The `reports/` directory is committed to the repository by the CI workflow. Key contents:

| Path | Description |
|------|-------------|
| `reports/YYYY-MM-DD.md` | Daily markdown report |
| `reports/YYYY-MM-DD.html` | Interactive HTML report with dark/light theme, sort, filter, favorites |
| `reports/index.html` | Index of the 14 most recent reports |
| `reports/_cache/summary_cache.json` | Persistent URL→summary cache (max 5000 entries) |
| `reports/_candidates/YYYY-MM-DD_candidates.csv` | All articles that passed stage 1, with scores |

Do not manually edit files in `reports/`. They are fully regenerated by the pipeline.

---

## External APIs

### Naver News Search API

- **Base URL**: `https://openapi.naver.com/v1/search/news.json`
- **Auth**: `X-Naver-Client-Id` + `X-Naver-Client-Secret` headers
- **Pagination**: `display=100`, `start=1/101/201/...` (max 1000 results per query)
- **Sort**: Always `sort=date` (most recent first)
- **Rate limit**: Not documented; current usage is well within typical limits

### Full-Text Scraping

Articles are scraped via `requests` + `BeautifulSoup` + `lxml`. Naver News pages are preferred (via `naver_link`) since their DOM structure is well-known. The scraper respects a Chrome 120 User-Agent header. Scraped content is never stored permanently — only the derived summary is cached.
