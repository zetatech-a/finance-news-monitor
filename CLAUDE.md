# CLAUDE.md — Finance News Monitor

This file provides guidance for AI assistants working on this codebase.

## Project Overview

**Finance News Monitor** is a Python-based automated daily aggregator of Korean domestic financial news (loan-business/대부업권 focus). It fetches articles from the Naver News Search API, runs them through a multi-stage filtering pipeline (rule pre-filter → relevance scoring/ML), tags them by financial sector and topic, clusters same-issue duplicates, generates extractive summaries (plus optional Gemini
3-line display summaries), and produces Markdown + interactive HTML reports plus quality-metrics JSON. A GitHub Actions workflow runs this pipeline **every day** (multiple cron triggers around 08:41–09:17 KST, deduplicated by a sent-marker), commits the output reports back to `main`, and optionally emails the HTML report.

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
│       ├── fields.py           # field_value()/unwrap_article() — dict/Article 공용 접근자
│       ├── filtering.py        # Stage 1: rule-based pre-filter (sports/entertainment/politics/lease)
│       ├── fulltext_fetch.py   # HTML fetch (charset_normalizer) + main-text extraction (BS4+lxml)
│       ├── gemini_cache.py     # Gemini 3-line cache (versioned key, atomic write, separate file)
│       ├── gemini_summary.py   # Gemini API 3-line Korean summary (optional, display-only)
│       ├── issue_cluster.py    # Same-issue clustering across outlets (fingerprints + similarity)
│       ├── normalize.py        # Article dataclass + raw dict → Article
│       ├── quality.py          # Per-run quality metrics JSON (counts/taxonomy/clusters/top10)
│       ├── relevance_filter.py # Stage 2 relevance decision (authoritative/candidate_hybrid/rule_only)
│       ├── relevance_score.py  # Heuristic scoring (hard/soft/negative weights, negative cap)
│       ├── report.py           # Markdown + interactive HTML report, Top-10 ranking, index
│       ├── templates/          # report.css / report.js / light.css (HTML에 인라인 삽입됨)
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
│   └── prune_reports.py                    # CI: reports retention (daily.yml passes 60d; artifacts 90d)
├── tests/                      # pytest suite (run with `python -m pytest tests/`)
├── data/
│   └── relevance_labels.csv    # Manual labeled data for the operating model (optional)
├── models/                     # NOT committed by default; see "ML Models" below
├── reports/                    # Generated output (committed by CI)
│   ├── YYYY-MM-DD.md / .html   # Daily reports
│   ├── index.html              # 14-day report index
│   ├── _cache/summary_cache.json          # extractive summaries
│   ├── _cache/gemini_summary_cache.json   # Gemini 3-line summaries
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
- Naver Cloud Platform account with a NAVER API HUB application
  (for `NCP_APIGW_API_KEY_ID` / `NCP_APIGW_API_KEY`)
- `pytest` for running tests (not in requirements.txt — install separately)

### Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest

cp .env.example .env   # template for your values
```

`.env` is auto-loaded (best-effort, no python-dotenv dependency) by the
`run_daily` and `notify_email` entry points via
`src/config.py::load_dotenv_if_present()` — **already-exported variables always
win** (CI secrets are never overridden). Standalone scripts under `scripts/`
do not load `.env`; export variables manually for those:

```bash
set -a; source .env; set +a
```

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `NCP_APIGW_API_KEY_ID` | Yes | **NAVER API HUB** Client ID |
| `NCP_APIGW_API_KEY` | Yes | **NAVER API HUB** Client Secret |

Naver migrated its open APIs to **NAVER API HUB** (Naver Cloud Platform) in
2026. Legacy NAVER Developers Center credentials (`NAVER_CLIENT_ID` /
`NAVER_CLIENT_SECRET`) were invalidated by the migration and are no longer
read anywhere in this codebase.
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
| `GEMINI_API_KEY` | No | Gemini 3-line summary. Unset ⇒ feature off, extractive summary used |
| `GEMINI_MODEL` | No | Default `gemini-3.5-flash-lite` (`gemini_summary.DEFAULT_MODEL`) |
| `GEMINI_ENABLED` | No | `0` disables the feature even when a key is present |
| `GEMINI_MAX_SUMMARIES` | No | Articles summarized per run (default 300, `0` = off) |
| `GEMINI_BATCH_MAX_ARTICLES` | No | Articles per generateContent request (default 50) |
| `GEMINI_BATCH_HARD_MAX_ARTICLES` | No | Hard cap on batch size (default 100); larger settings are clamped |
| `GEMINI_BATCH_MAX_INPUT_CHARS` | No | Input-char budget per request (default 150000) |
| `GEMINI_ARTICLE_MAX_CHARS` | No | Per-article body cap (default 3000) |
| `GEMINI_MAX_REQUESTS_PER_RUN` | No | Total generate requests per run incl. retries/splits (default 20) |
| `GEMINI_MAX_RECOVERY_REQUESTS` | No | Of those, the cap for retries/splits (default 8) |
| `GEMINI_MAX_FETCH_ATTEMPTS` | No | Gemini-only body fetch cap (default 300; separate from the extractive budget) |
| `GEMINI_INPUT_MIN_CHARS` | No | Below this the article is not sent (default 200) |
| `GEMINI_MAX_LINE_CHARS` | No | Per-line validation limit (default 90) |
| `GEMINI_REQUEST_TIMEOUT_SECONDS` | No | Request timeout (default 90, max 600) |
| `GEMINI_RETRY_ATTEMPTS` | No | Total attempts for retryable errors (default 2, max 5) |
| `GEMINI_MIN_INTERVAL_SECONDS` | No | Minimum gap between request **starts** (default 2) |
| `GEMINI_CIRCUIT_BREAKER_FAILURES` | No | Consecutive failures before the breaker opens (default 3) |

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
12. visible_report_items() + top_report_items()  [pipeline/report.py] 노출 대상 + Top 10 확정
13. apply_gemini_summaries()  [run_daily.py] **표시 전용** Gemini 3줄 요약 (선택).
                          Top 10 우선, 최대 GEMINI_MAX_SUMMARIES(기본 300)건.
                          캐시 hit 기사는 배치 구성 **전에** 제외되어 전송되지 않는다.
                          cache miss만 마이크로배치(기본 50건/요청)로 묶어 보낸다.
                          article.summary_lines만 채우고 description은 건드리지 않는다.
14. build_quality_metrics()  [pipeline/quality.py] → reports/_metrics/*_quality_metrics.json
15. keyword_trends() + render_markdown() + render_html() + write_report() + write_index()
```

### 요약이 분류에 미치는 영향 (중요)

10단계의 **추출요약은 `Article.description`을 덮어쓰고**, 그 값이 11단계 재태깅과
12단계 Top 10 랭킹(`report.py::_top_rank_score`, `content_type.py`, `source_quality.py`)의
입력이 된다. 즉 추출요약 결과는 분류·순위를 바꾼다.

반면 **Gemini 3줄은 표시 전용**이다. 13단계는 노출 대상과 Top 10이 확정된 뒤에 실행되고
`description`이 아니라 `Article.summary_lines`만 채우므로, Gemini 사용 여부와 무관하게
관련성 판정·태깅·클러스터링·대표 기사 선정·Top 10 결과가 동일하다. 이 순서를 바꾸거나
Gemini 결과를 `description`에 넣으면 매일 LLM 출력에 따라 분류가 흔들린다.

### Gemini 3줄 요약 (`pipeline/gemini_summary.py`)

**처리량 구조 — 적응형 마이크로배치**
- 일일 표시 기사 전체(적은 날 40~50건, 많은 날 200~250건, 상한 300건)를 요약하는 것이 목표다.
- **기사 1건당 1회 호출하는 정상 경로는 존재하지 않는다.** 일반(동기) `generateContent`
  요청 하나에 여러 기사를 담고, 기사별 불투명 ID(`article-0001`)와 3줄을 구조화 응답으로 받는다.
  **Google의 비동기 Batch API는 쓰지 않는다.**
- 배치는 `GEMINI_BATCH_MAX_ARTICLES`(기사 수)와 `GEMINI_BATCH_MAX_INPUT_CHARS`(입력 문자
  예산) 중 **먼저 걸리는 쪽**에서 닫힌다. `plan_batches()`가 이 두 제한을 함께 적용한다.
- `GEMINI_BATCH_MAX_ARTICLES`가 `GEMINI_BATCH_HARD_MAX_ARTICLES`를 넘으면 hard cap으로 낮춘다.
- 요청 수(평균 본문 ~1,200자): 50건→1회, 100건→2회, 250건→5회, 300건→6회.
  **최악(전 기사가 `GEMINI_ARTICLE_MAX_CHARS`=3,000자)**: 문자 예산이 먼저 걸려 배치가
  47건에서 닫히므로 250건→6회, 300건→7회.
- 입력량 실측(제목 40자 기준): 250건×3,000자 = 본문 **750,000자**, 실제 프롬프트 합계
  약 775,000자, 요청 1건 최대 약 146,000자. 300건이면 각각 900,000 / 930,000자.
  평균 본문(1,200자) 250건은 프롬프트 합계 약 325,000자다.
  **평균과 최악을 혼동하지 마라** — 무료 티어 TPM 산정은 요청 1건(≈146,000자) 기준으로 한다.
- 총 요청 수는 `GEMINI_MAX_REQUESTS_PER_RUN`(기본 20)을 절대 넘지 않고, 그중 재시도·분할은
  `GEMINI_MAX_RECOVERY_REQUESTS`(기본 8)까지만 쓴다. 복구 예산이 바닥나도 아직 보내지 않은
  정상 배치는 계속 처리한다 — 앞 배치의 복구가 뒤 배치를 굶기지 않는다.
  상한에 도달하면 남은 기사는 추출요약을 쓴다.
- 크기 1 요청은 분할 사다리의 **최종 복구 수단**으로만 나타난다. 정상 경로에 두지 마라.

**내용 품질 게이트 — usable/reason (v3)**
- 크롤링 본문에는 제목과 무관한 기사·사이드바·인기기사 목록이 섞인다. 실제 스모크에서
  API 오류 0·구조 검증 실패 0인데도 제목과 무관한 요약이 생성됐다.
- 응답 항목은 `{id, usable, reason, lines}`다. `reason`은 `ok` / `title_body_mismatch` /
  `multi_topic` / `insufficient_content`.
- `usable=false`는 **정상 응답**이다. 해당 기사는 AI 요약으로 쓰지 않고, **캐시하지 않고**,
  **재요청하지 않으며**, 추출요약으로 표시한다. `items_rejected`나 `api_errors`로 세지 마라.
- `usable=true`인데 `reason != "ok"`이거나 lines가 3줄 계약을 어기면 **구조 위반**이다
  (재요청 대상). 이 둘을 섞지 마라.
- 배치 전체가 usable=false여도 API는 정상이므로 circuit breaker를 열지 않는다
  (`resolved_ids`가 비어 있을 때만 실패로 센다).
- JSON Schema에 조건부 제약을 걸 수 없어 `lines`는 0~3으로 열어두고 앱에서 검증한다.
- 프롬프트는 세 문장이 **제목의 단일 핵심 주제**를 설명하도록 요구한다. 서로 다른 사건을
  한 줄씩 나열하거나 제목으로 사실을 추측해 채우면 안 된다.

**부분 성공 — all-or-nothing 금지**
- `validate_batch_response()`가 응답을 **항목별로** 검증한다. 요청 ID와 일치하고 3줄 계약을
  통과한 항목은 즉시 적용·캐시하고, 나머지만 실패 목록에 넣는다.
- 실패 사유: 누락 ID / 알 수 없는 ID / 중복 ID / 2줄·4줄 / 빈 문자열 / 길이 초과 /
  마크다운·번호 접두사 / 파싱 불가.
- **이미 성공한 기사는 절대 재전송하지 않는다.**
- 재요청 크기: 일부만 실패하면 그 부분집합을 한 번에(이미 더 작은 배치다), 전량 실패면
  사다리(50 → 25 → 10 → 1)로 좁힌다. 1까지 가서도 실패하면 기사별 extractive fallback.
- 429/5xx/timeout은 **분할하지 않고** 같은 배치로 제한 재시도한다(크기 문제가 아니다).
  400만 크기 문제일 수 있어 재시도 없이 곧장 분할한다.

**모델 / thinking**
- 모델 ID의 유일한 정의 지점은 `DEFAULT_MODEL` 상수다(기본 `gemini-3.5-flash-lite`).
  `gemini-2.5-flash`는 쓰지 않고, `-latest` alias도 기본값으로 쓰지 않는다.
  `GEMINI_MODEL`로 `gemini-3.6-flash` 등으로 교체할 수 있다.
- 공식 `google-genai` SDK만 사용한다 (구 `google-generativeai` 금지). import는 실제 호출
  직전까지 지연되므로 패키지 미설치 상태에서도 파이프라인과 테스트가 동작한다.
- Gemini 3 계열에서만 `types.ThinkingConfig(thinking_level="minimal")`을 붙인다
  (`supports_thinking_level()`). 그 이전 모델에 주면 오류가 나므로 안전하게 생략한다.
- 그 밖의 생성 파라미터(temperature 등)는 추측해서 넣지 않는다.

**보안 / 검증**
- 기사 본문은 신뢰할 수 없는 외부 입력이다 — system instruction으로 격리하고, 기사 경계를
  넘어 사실이 섞이지 않도록 명시한다. **URL은 프롬프트에 넣지 않는다** (불투명 ID만 사용).
- SDK의 structured output을 받은 뒤에도 `validate_lines()`로 재검증한다(문자열 3개, 타입,
  빈 문자열, 줄바꿈, 마크다운/불릿/번호, 최대 길이, 추가 필드). 실패하면 추출요약으로 fallback.
- 스키마의 `summaries.maxItems`는 해당 요청의 기사 수를 넘지 않게 배치마다 생성한다
  (`response_json_schema(n)`).

**오류 처리**
- **fail-open이되 조용히 삼키지 않는다.** API 오류는 분류(`classify_error`)해서 경고 로그를
  남기고, 401/403/404는 즉시 비활성화, 400은 재시도 없음, 429/5xx/timeout은 제한적 재시도,
  연속 실패가 임계값에 도달하면 circuit breaker가 열린다.
- 프로그래밍 오류(TypeError 등)는 `GeminiProgrammingError`로 **raise**되어 테스트에 드러나고,
  `run_daily.apply_gemini_summaries`가 파이프라인 경계에서 스택과 함께 로깅한 뒤 흡수한다 —
  리포트 생성은 절대 중단되지 않는다.
- 로그에 API 키·기사 전문·전체 프롬프트·전체 응답·전체 URL을 남기지 않는다
  (host는 `safe_host()`로만).

**캐시**
- `reports/_cache/gemini_summary_cache.json` **별도 파일**이다. 기존 `summary_cache.json`
  (평면 `{url: str}`, 상한 5000으로 이미 포화)은 건드리지 않는다.
- **기사별로 저장한다** — 배치 전체를 하나의 entry로 저장하지 않는다.
- 키는 `sha256(canonical_url|model|prompt_version|schema_version)`이라 모델/프롬프트/
  스키마가 바뀌면 자동 cache miss가 난다. 본문·프롬프트·응답 원문은 저장하지 않으며,
  손상된 항목은 개별적으로 버리고 나머지는 계속 쓴다. 쓰기는 tmp + `os.replace`로 원자적이다.
- `PROMPT_VERSION` / `SCHEMA_VERSION`은 프롬프트나 스키마를 고칠 때 반드시 올린다.

**본문 수집**
- 추출요약 단계가 `body_sink`에 담아둔 본문을 재사용한다 — 같은 URL을 다시 fetch하지 않는다.
- 없는 기사만 `GEMINI_MAX_FETCH_ATTEMPTS`(기본 300, 추출요약 예산과 별개) 안에서 추가 fetch한다.
- fetch가 실패하면 현재 `description`(추출요약)을 입력 후보로 쓰고, 그마저
  `GEMINI_INPUT_MIN_CHARS` 미만이면 호출 없이 기존 fallback을 그대로 둔다.

**관측**
- 실행이 끝나면 `run_daily.apply_gemini_summaries`가 **sanitized 집계 한 줄**을 남긴다
  (`Gemini run summary: ...`). 값은 전부 숫자/불리언이며 제목·본문·프롬프트·응답·전체 URL·
  API 키는 담지 않는다. 항목: targets / cache_hits / cache_miss / skipped_no_body / batches /
  requests / normal_requests / recovery_requests / sent_articles / sent_chars / gemini_applied /
  extractive_fallback / content_rejected / title_body_mismatch / multi_topic /
  insufficient_content / items_rejected / api_errors / rate_limit_hits / splits /
  breaker_tripped / disabled_reason / elapsed_seconds.
- `quality.py`의 `COUNT_KEYS`는 고정 허용목록이라 Gemini 카운터를 넣어도 버려진다.
  기존 metrics JSON 스키마를 깨지 않기 위해 **의도적으로** 로그로만 남긴다.
- 디버깅 목적으로도 본문·프롬프트 일부를 출력하지 마라.

**무료 티어**
- RPM/TPM/RPD 수치를 코드에 하드코딩하지 않는다. 실제 한도는 AI Studio에서 확인하고
  배치 크기·요청 상한·호출 간격 환경변수로 맞춘다.

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

**Field conventions**: every pipeline-populated attribute (`decision`, `keep`,
`summary_cached`, `model_used`, `matched_*`, cluster fields, …) is formally
declared on the `Article` dataclass — do NOT attach new attributes via
`setattr`; declare them. Consumers that accept both `Article` objects and
plain dicts (tests/scripts pass dicts) read fields via
`src/pipeline/fields.py::field_value()` / `unwrap_article()` — use those
instead of re-implementing per-module `_field` helpers.

---

## Configuration: `queries.yml`

Controls three things:

- **`fetch_queries`**: ~50 high-precision Korean search terms sent to the Naver API.
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
- Missing SMTP secrets **skip** the email step (with a workflow-summary
  warning) instead of failing the run — repos without email configured
  stay green.

---

## Code Conventions

### Style

- **Type hints everywhere**: `from __future__ import annotations` at the top of each file
- **Dataclasses** for data models
- **No global mutable state**: pass config/data explicitly through function arguments
- **Logging**: `logging.getLogger(__name__)`; root logger configured in `run_daily.py` at INFO

### Error Handling

- **Network errors during extractive summarization**: skip the article (best-effort),
  but respect the fetch-attempt cap (`MAX_SUMMARY_FETCH_ATTEMPTS`)
- **Gemini summarization**: fail-open per article, but **never silently**. Classify the
  error, log a sanitized warning, and let the circuit breaker stop the rest of the run when
  appropriate. `except Exception: pass` around Gemini calls is prohibited — programming
  errors must raise (`GeminiProgrammingError`) so tests catch them; only the pipeline
  boundary in `run_daily.apply_gemini_summaries` absorbs them, with `exc_info=True`.
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

Tests live in `tests/` (pytest). Run with:

```bash
python -m pytest tests/ -q
```

Conventions: files named `test_*.py`; no network access (SMTP/HTTP are faked
via monkeypatch). The suite must pass before pushing. Some files are named
after project phases (`test_phase8c_candidate_hybrid.py` etc.) — treat the
assertions, not the phase names, as the spec.

One guard test inspects the **uncommitted git diff** and fails while
`.github/workflows/daily.yml` or `src/ml/` have local modifications. When
editing those intentionally, run with `ALLOW_DELIVERY_SCHEDULE_CHANGES=1`
to skip it (it passes again after commit).

Additional quality checks: CI smoke test (`smoke.yml`), manual review of
`reports/_candidates/*.csv` and `reports/_metrics/*.json`.

---

## Reports Directory

Committed to the repository by CI. Do not manually edit files in `reports/` —
they are regenerated by the pipeline.

Retention (`scripts/prune_reports.py`, run by `daily.yml` before commit):
daily reports are kept 60 days (`daily.yml` passes `--report-keep-days 60`, overriding
the script default of 180); `_candidates`/`_metrics`/`_sent` files 90 days
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

### Naver News Search API (NAVER API HUB)

- **Base URL**: `https://naverapihub.apigw.ntruss.com/search/v1/news`
  (2026 migration from the retired `openapi.naver.com/v1/search/news.json`;
  note the path reordering and the dropped `.json` suffix)
- **Auth**: `X-NCP-APIGW-API-KEY-ID` + `X-NCP-APIGW-API-KEY` headers
  (legacy `X-Naver-Client-*` headers/credentials no longer work)
- Request params (`query`/`display`/`start`/`sort`) and the response JSON are
  unchanged from the legacy API (per the migration guide)
- **Pagination**: `display=100`, `start=1/101/201/...` (max 1000 results per query)
- **Sort**: always `sort=date`; paging stops early once a page is older than the window
- **Retry**: 429/5xx and timeouts retried with exponential backoff (env-tunable).
  Note: API HUB is API-Gateway-based, so error response bodies/status codes may
  differ from the legacy endpoint.

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
