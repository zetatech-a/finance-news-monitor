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
│                               # 요약 표시 상태(ai/content_rejected/preview)는 summary_state()
│                               # 한 곳에서 판정하고 summary_panel_html()/md_summary_block()이 렌더한다
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
│   ├── check_gemini_smoke.py                # Manual smoke only: strict Gemini result check
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
| `GEMINI_MODEL` | No | Default `gemini-3.6-flash` (`gemini_summary.DEFAULT_MODEL`) |
| `GEMINI_ENABLED` | No | `0` disables the feature even when a key is present |
| `GEMINI_MAX_SUMMARIES` | No | Articles summarized per run (default 300, `0` = off) |
| `GEMINI_BATCH_MAX_ARTICLES` | No | Articles per generateContent request (default 25, verified against the live API) |
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
- 단독으로도 입력 예산을 넘는 기사는 `fit_item_to_budget()`이 본문을 더 잘라 맞춘다.
  배치의 첫 항목이라고 예산을 무시하고 담으면 요청당 상한이 무력화되고, 그 요청은 400을
  받아도 이미 크기 1이라 분할로 복구할 수 없다. 자리가 없으면 보내지 않는다(추출요약).
  자를 때도 `min_body_chars`(= `max(GEMINI_INPUT_MIN_CHARS, MIN_FITTED_BODY_CHARS)`)는
  지킨다 — 예산을 핑계로 운영자가 정한 입력 하한을 우회하면 안 된다.
- `GEMINI_BATCH_MAX_ARTICLES`가 `GEMINI_BATCH_HARD_MAX_ARTICLES`를 넘으면 hard cap으로 낮춘다.
- 기본 배치는 **25건**이다. 실 API에서 50건 요청은 `400 INVALID_ARGUMENT`를 받았고
  분할된 25건 2개는 200이었다. 50 이상은 실험적 설정이며, **실패가 알려진 요청을 먼저
  보내고 400 후 분할하는 경로를 기본 동작으로 두지 마라.**
- 요청 수: 25건→1회, 50건→2회, 100건→4회, 250건→10회, 300건→12회.
- 입력량 실측(제목 40자 기준): 250건×3,000자 = 본문 **750,000자**, 실제 프롬프트 합계
  약 775,000자, 요청 1건 최대 약 146,000자. 300건이면 각각 900,000 / 930,000자.
  평균 본문(1,200자) 250건은 프롬프트 합계 약 325,000자다.
  **평균과 최악을 혼동하지 마라** — 무료 티어 TPM 산정은 요청 1건(≈146,000자) 기준으로 한다.
- 총 요청 수는 `GEMINI_MAX_REQUESTS_PER_RUN`(기본 20)을 절대 넘지 않고, 그중 재시도·분할은
  `GEMINI_MAX_RECOVERY_REQUESTS`(기본 8)까지만 쓴다. 복구 예산이 바닥나도 아직 보내지 않은
  정상 배치는 계속 처리한다 — 앞 배치의 복구가 뒤 배치를 굶기지 않는다.
  상한에 도달하면 남은 기사는 추출요약을 쓴다.
- 복구 요청은 사다리를 위해 worklist 앞에 끼어들지만, 실행 직전에 **아직 보내지 않은 정상
  배치 몫을 총 예산에서 예약**한다(`budget_left - 1 < pending_normal`이면 건너뛴다).
  총 예산이 정상 배치 수에 가까울 때(smoke: 요청 4회 / 정상 배치 2개) 복구 예산만으로는
  굶주림을 막지 못한다. 이 예약을 없애면 뒤쪽 배치가 통째로 전송되지 않는다.
- 크기 1 요청은 분할 사다리의 **최종 복구 수단**으로만 나타난다. 정상 경로에 두지 마라.

**내용 품질 게이트 — usable/reason (v3)**
- 크롤링 본문에는 제목과 무관한 기사·사이드바·인기기사 목록이 섞인다. 실제 스모크에서
  API 오류 0·구조 검증 실패 0인데도 제목과 무관한 요약이 생성됐다.
- 응답 항목은 `{id, usable, reason, lines}`다. `reason`은 `ok` / `title_body_mismatch` /
  `multi_topic` / `insufficient_content`.
- `usable=false`는 **정상 응답**이다. 해당 기사는 AI 요약으로 쓰지 않고, **캐시하지 않고**,
  **재요청하지 않는다**. `items_rejected`나 `api_errors`로 세지 마라.
- 내용 거부 기사의 표시 요약은 **`Article.source_description`(네이버 원본 스니펫)**으로
  되돌린다 — 현재 `description`은 오염된 크롤링 본문에서 만든 추출요약일 수 있다.
  원본이 없거나 24자 미만이면 `description`을 쓴다. 일반 API 장애는 내용 거부가 아니므로
  기존 `description`을 그대로 둔다. `summarize_many(on_content_rejected=...)` 콜백으로
  사유가 전달되고 `run_daily`가 `Article.summary_rejection_reason`에 기록한다.
- `source_description`은 `normalize()`에서 채워져 추출요약이 `description`을 덮어써도
  보존된다. 분류·태깅·클러스터링 입력은 여전히 `description`이다.
- `usable=true`인데 `reason != "ok"`이거나 lines가 3줄 계약을 어기면 **구조 위반**이다
  (재요청 대상). 이 둘을 섞지 마라.
- 반대쪽도 대칭이다. `usable=false`의 정상 형태는 `reason ∈ UNUSABLE_REASONS` **그리고**
  `lines == []`뿐이다. `reason="ok"`이거나 lines가 들어 있으면 구조 위반으로 다룬다 —
  이런 응답까지 내용 거부로 세면 재요청 대상에서 빠지고, smoke strict 검증이 "게이트가
  전부 걸렀다"로 읽어 초록이 된다(모델이 이 형태를 계속 뱉으면 AI 요약이 전부 사라져도
  모른다). 스키마 enum 밖의 사유를 조용히 흡수하지 마라.
- 배치 전체가 usable=false여도 API는 정상이므로 circuit breaker를 열지 않는다
  (`resolved_ids`가 비어 있을 때만 실패로 센다).
- JSON Schema에 조건부 제약을 걸 수 없어 `lines`는 0~3으로 열어두고 앱에서 검증한다.
- 프롬프트는 세 문장이 **제목의 단일 핵심 주제**를 설명하도록 요구한다. 서로 다른 사건을
  한 줄씩 나열하거나 제목으로 사실을 추측해 채우면 안 된다.

**부분 성공 — all-or-nothing 금지**
- `validate_batch_response()`가 응답을 **항목별로** 검증한다. 요청 ID와 일치하고 3줄 계약을
  통과한 항목은 즉시 적용·캐시하고, 나머지만 실패 목록에 넣는다.
- 실패 사유: 누락 ID / 알 수 없는 ID / 중복 ID / 2줄·4줄 / 빈 문자열 / 길이 초과 /
  마크다운·번호 접두사 / **한 줄에 두 문장 또는 종결부호 없는 조각** / 파싱 불가.
  "한 줄 = 완결된 한 문장"은 `is_single_sentence()`가 검사한다 — 소수점·비율(`8.4%`)은
  종결부호 뒤에 공백이 없어 문장 경계로 세지 않는다.
- 국문 날짜 표기(`2026. 8. 4.`)와 `U.S.` 같은 약어는 `_mask_non_sentence_dots()`로 가린 뒤
  문장 경계·번호 접두사를 검사한다. 프롬프트가 "날짜·고유명사는 기사 표기 그대로"를
  요구하므로 이 마침표를 문장 경계로 세면 정상 요약이 거부되어 복구 요청만 낭비하고
  추출요약으로 떨어진다. 마스킹을 지우면 `2026. 8. 4. 기준 …`이 번호 목록으로도 오인된다.
- **이미 성공한 기사는 절대 재전송하지 않는다.**
- 재요청 크기: 일부만 실패하면 그 부분집합을 한 번에(이미 더 작은 배치다), 전량 실패면
  사다리(`SPLIT_LADDER` = 25 → 10 → 1)로 좁힌다. 1까지 가서도 실패하면 기사별 extractive fallback.
- 429/5xx/timeout은 **분할하지 않고** 같은 배치로 제한 재시도한다(크기 문제가 아니다).
  400만 크기 문제일 수 있어 재시도 없이 곧장 분할한다.
- 하나도 못 건진 배치의 **circuit breaker 집계는 분할 재요청을 예약하지 못했을 때만** 한다
  (`summarize_many`가 단독으로 센다 — `_run_batch`에서 같이 세면 1회 실패가 2회로 잡혀
  임계값이 절반이 된다). 응답 즉시 세면 `GEMINI_CIRCUIT_BREAKER_FAILURES=1`에서 사다리를
  쓰기도 전에 breaker가 열려 25 → 10 → 1 회복 경로가 통째로 무력화된다.

**모델 / thinking**
- 모델 ID의 유일한 정의 지점은 `DEFAULT_MODEL` 상수다(기본 `gemini-3.6-flash`).
  이 프로젝트 실 API 검증에서 3.6 Flash는 50건을 오류 없이 처리했고,
  `gemini-3.5-flash-lite`는 같은 조건에서 반복 503으로 적용 0건이었다 —
  후자는 `GEMINI_MODEL`로 수동 선택하는 선택지로만 남긴다.
  **자동 모델 fallback은 없다.** 실패하면 기존 추출요약으로 내려간다.
  `gemini-2.5-flash`는 쓰지 않고, `-latest` alias도 기본값으로 쓰지 않는다.
  모델은 캐시 키에 이미 포함되므로 **모델 교체만을 이유로 `PROMPT_VERSION`/`SCHEMA_VERSION`을
  올리지 마라.**
- 공식 `google-genai` SDK만 사용한다 (구 `google-generativeai` 금지). import는 실제 호출
  직전까지 지연되므로 패키지 미설치 상태에서도 파이프라인과 테스트가 동작한다.
- Gemini 3 계열에서만 `types.ThinkingConfig(thinking_level="minimal")`을 붙인다
  (`supports_thinking_level()`). 그 이전 모델에 주면 오류가 나므로 안전하게 생략한다.
- 그 밖의 생성 파라미터(temperature 등)는 추측해서 넣지 않는다.

**보안 / 검증**
- 기사 본문은 신뢰할 수 없는 외부 입력이다 — system instruction으로 격리하고, 기사 경계를
  넘어 사실이 섞이지 않도록 명시한다. **URL은 프롬프트에 넣지 않는다** (불투명 ID만 사용).
- 제목·본문은 프롬프트에 넣기 전에 `sanitize_article_text()`로 꺾쇠를 전각으로 치환한다 —
  본문의 `</article>`·`<article id="...">`가 블록 경계를 위조하는 것을 막는다. 구조 검증은
  요청 ID 일치만 보므로 경계를 넘어 섞인 내용은 잡지 못한다. 치환은 길이를 보존해
  `estimate_item_chars()`의 배치 예산 계산과 어긋나지 않는다.
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
- 400 오류는 `describe_client_error()`로 **기계 판독 가능한 토큰만** 남긴다
  (`status` / `reason` / `schema_rejected` / `invalid_argument`). 안전 토큰 패턴
  (`^[A-Z][A-Z0-9_]*$`)에 맞지 않으면 `unknown`으로 떨어뜨린다 — 서버 응답 원문이나
  전체 오류 메시지는 절대 출력하지 않는다.

**캐시**
- `reports/_cache/gemini_summary_cache.json` **별도 파일**이다. 기존 `summary_cache.json`
  (평면 `{url: str}`, 상한 5000으로 이미 포화)은 건드리지 않는다.
- **기사별로 저장한다** — 배치 전체를 하나의 entry로 저장하지 않는다.
- 키는 `sha256(canonical_url|model|prompt_version|schema_version|fingerprint)`이라
  모델/프롬프트/스키마가 바뀌거나 **기사 제목이 정정되면** 자동 cache miss가 난다.
  fingerprint는 본문을 다시 받지 않고 얻을 수 있는 값(제목)만 쓴다 — 본문 해시를 쓰면
  캐시의 존재 이유(재크롤링 회피)가 사라진다. 제목 그대로 본문만 고친 정정은 잡히지
  않으므로 `MAX_AGE_DAYS`(14일) 신선도 상한으로 staleness를 제한한다. `GEMINI_MAX_LINE_CHARS`는 키에 없으므로
  캐시 hit도 `validate_lines(..., max_line_chars=config.max_line_chars)`로 **현재 설정에
  다시 검증**한 뒤 쓴다 — 한도를 낮췄을 때 캐시만 통과하는 상태를 막는다. 본문·프롬프트·응답 원문은 저장하지 않으며,
  손상된 항목은 개별적으로 버리고 나머지는 계속 쓴다. 쓰기는 tmp + `os.replace`로 원자적이다.
- `PROMPT_VERSION` / `SCHEMA_VERSION`은 프롬프트나 스키마를 고칠 때 반드시 올린다.

**본문 수집**
- 추출요약 단계가 `body_sink`에 담아둔 본문을 재사용한다 — 같은 URL을 다시 fetch하지 않는다.
- 없는 기사만 `GEMINI_MAX_FETCH_ATTEMPTS`(기본 300, 추출요약 예산과 별개) 안에서 추가 fetch한다.
- **이번 실행에서 보낼 수 없는 기사는 fetch하지 않는다.** 전송 가능량은
  `BatchCapacityPlanner`가 `plan_batches()`와 **같은 규칙**(기사 수 + 입력 문자 예산)으로
  추적하고, 넘으면 `skipped_over_capacity`로 세고 본문 수집을 건너뛴다(캐시 hit은 이 상한과
  무관하게 계속 적용된다). 이 가드가 없으면 요청 1회 설정에서도 300건을 크롤링해 버리고 그
  본문을 그대로 버린다. `요청 수 × 배치 크기`로만 잡으면 문자 예산 때문에 배치가 일찍 닫히는
  설정(예: 예산 5,000자 + 본문 3,000자 → 배치당 1건)에서 여전히 과잉 크롤링이 난다.
- fetch 전 판단(`has_room()`)은 다음 기사를 **최소 크기**로 가정한다. 본문을 받은 뒤에는
  `try_add()`가 **실제 크기로 다시 확인**하고, 담을 수 없으면 준비를 닫는다. 계획 상태가
  예산 너머로 늘어나면 `has_room()`이 계속 True를 돌려줘 전송되지 않을 기사를 계속 크롤링한다.
- 본문 크롤링이 실패해 스니펫(추출요약)으로 만든 요약은 **캐시하지 않는다**. 캐시 키에
  입력 출처가 없으므로, 저품질 입력으로 만든 요약이 박히면 나중에 본문을 정상적으로 받는
  실행에서도 hit되어 신선도 상한까지 재생성되지 않는다(표시는 정상적으로 한다).
- fetch가 실패하면 현재 `description`(추출요약)을 입력 후보로 쓰고, 그마저
  `GEMINI_INPUT_MIN_CHARS` 미만이면 호출 없이 기존 fallback을 그대로 둔다.

**관측**
- 실행이 끝나면 `run_daily.apply_gemini_summaries`가 **sanitized 집계 한 줄**을 남긴다
  (`Gemini run summary: ...`). 값은 전부 숫자/불리언이며 제목·본문·프롬프트·응답·전체 URL·
  API 키는 담지 않는다. 항목: targets / cache_hits / cache_miss / skipped_no_body /
  skipped_over_capacity / batches /
  requests / normal_requests / recovery_requests / sent_articles / sent_chars / gemini_applied /
  extractive_fallback / content_rejected / title_body_mismatch / multi_topic /
  insufficient_content / items_rejected / api_errors / rate_limit_hits / splits /
  breaker_tripped / disabled_reason / elapsed_seconds.
- `quality.py`의 `COUNT_KEYS`는 고정 허용목록이라 Gemini 카운터를 넣어도 버려진다.
  기존 metrics JSON 스키마를 깨지 않기 위해 **의도적으로** 로그로만 남긴다.
- 디버깅 목적으로도 본문·프롬프트 일부를 출력하지 마라.

**수동 smoke의 strict 검증 (daily는 불변)**
- `smoke.yml`만 `GEMINI_RUN_SUMMARY_PATH`를 설정해 `run_daily`가 sanitized 집계 JSON을
  남기게 하고, `scripts/check_gemini_smoke.py`가 그 JSON만 읽어 판정한다(로그 grep 금지).
- 판정은 **캐시 hit을 뺀 신규 적용 건수**(`gemini_applied - cache_hits`)로 한다 —
  `gemini_applied`에 캐시 hit이 포함되므로 그대로 쓰면 라이브 요청 전량 실패가 통과한다.
- 전송 대상이 1건 이상인데 신규 적용 0 + 내용 거부 0이면 **실패**. 내용 거부만 있는 경우는
  거부 건수가 보낸 기사(cache_miss) 전부를 설명할 때만 성공이다 — 일부만 거부이고 나머지가
  구조 위반/누락이면 API 경로가 반쯤 죽은 것이므로 실패. 전송 0인 경우는 **캐시 hit이 대상
  전부를 덮을 때만** 성공이다 — 캐시 1건 + 나머지 본문 부족을 성공으로 보면 라이브 경로를
  한 번도 거치지 않고 초록이 된다. 그 외 전송 0은 명시적 skip(경고).
- `disabled_reason`은 두 갈래다. 요청 전에 꺼진 사유(`no_api_key` / `disabled_by_env` /
  `max_summaries_zero` / `max_requests_zero`)만 skip이고, 호출을 시도한 뒤 런타임에 꺼진
  사유(`auth` / `bad_model` / `consecutive_failures` / `programming_error`)는 **실패**다 —
  전부 skip으로 묶으면 라이브 경로가 완전히 죽어도 워크플로가 초록으로 끝난다.
- **`daily.yml`에는 이 검증을 넣지 마라.** 일일 파이프라인의 fail-open 동작이 깨진다.
- smoke 검증이 실패해도 artifact 업로드는 `always()`로 계속 실행된다.

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
