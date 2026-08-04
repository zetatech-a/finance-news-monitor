# Finance News Monitor (MVP)

국내 금융권(대부업권 중심) 일일 언론동향을 수집하고 리포트로 정리하는 MVP입니다.

## 주요 기능
- Naver News Search API 기반 기사 수집
- 업권별 분류 및 키워드 트렌드 집계
- 기사 본문 추출식 요약 + **(선택) Gemini API 기반 한국어 3줄 요약**
- `reports/YYYY-MM-DD.md` 및 `reports/YYYY-MM-DD.html` 리포트 생성
- 최근 14일 리포트 링크를 모은 `reports/index.html` 생성

## 요구사항
- Python 3.11+
- 환경변수 설정 — **NAVER API HUB** (네이버 클라우드 플랫폼)
  - `NCP_APIGW_API_KEY_ID` / `NCP_APIGW_API_KEY`
  - 2026년 이관으로 기존 NAVER Developers Center 키(`NAVER_CLIENT_ID`/`SECRET`)는
    무효화되어 더 이상 사용하지 않습니다.

## 설치
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

환경변수는 `.env.example`을 참고해 설정합니다. `.env` 파일은 `run_daily`와
`notify_email` 실행 시 자동으로 로드됩니다(이미 export된 값은 덮어쓰지 않음).
`scripts/` 하위 스크립트를 직접 실행할 때는 수동 export가 필요합니다:

```bash
cp .env.example .env   # 값 편집 후
set -a; source .env; set +a   # scripts/ 직접 실행 시에만 필요
```

## 실행
```bash
python -m src.run_daily
```

옵션:
```bash
python -m src.run_daily --date 2024-01-01 --window_hours 24 --end_hhmm 0800 --overlap_minutes 15
```

- `--window_hours` (기본: `24.0`)
- `--end_hhmm` (기본: `0730`, 형식: `0800` 또는 `08:00`. 운영은 `0855` 사용)
- `--overlap_minutes` (기본: `15`)
- `--max_pages` (기본: `5`, 쿼리당 Naver API 페이지 수 상한)
- `--dry_run` (윈도우 계산만 출력 후 종료)
- `--disable_candidate_model` (운영 모델이 없을 때 후보 모델을 무시하고 룰만 사용)
- `--candidate_keep_prob` (기본: `0.65`, 후보 모델 hybrid keep 임계값)
- `--candidate_drop_prob` (기본: `0.35`, 후보 모델 hybrid drop 임계값)

## Gemini 3줄 요약 (선택 기능)

기사 카드의 요약을 **AI가 만든 정확히 3줄의 한국어 문장**으로 보여줍니다.
① 무슨 일이 있었는지 ② 핵심 주체·날짜·금액·비율 ③ 기사에 명시된 영향·후속 조치.
자세한 내용은 기존과 동일하게 기사 제목이나 `네이버`/`원문` 버튼으로 확인합니다.

**목표는 일일 리포트에 표시되는 기사 전체를 요약하는 것입니다.** 실제 표시 기사 수는
적은 날 40~50건, 많은 날 200~250건이며 안전 상한은 300건입니다.

**완전히 선택 기능입니다.** `GEMINI_API_KEY`가 없거나 API 호출이 실패해도
리포트 생성과 이메일 발송은 그대로 성공합니다.

### 처리 방식: generateContent 마이크로배치

기사 1건당 1회 호출하지 않습니다. **일반(동기) `generateContent` 요청 하나에 여러 기사를
담고**, 기사별 불투명 ID(`article-0001`)와 3줄 요약을 구조화 응답으로 돌려받습니다.

> Google의 **비동기 Batch API는 사용하지 않습니다.** 일반 동기 요청만 씁니다.

배치는 **기사 수**와 **입력 문자 예산** 중 먼저 걸리는 쪽에서 닫힙니다.

| 표시 기사 | 평균 본문(~1,200자) | 최악: 전 기사가 3,000자 상한 |
|---|---|---|
| 40~50건 | 1회 | 2회 |
| 100건 | 2회 | 3회 |
| 250건 | 5회 | 6회 |
| 300건 | 6회 | 7회 |

최악 조건에서는 기사 수(50)보다 문자 예산이 먼저 걸려 배치가 **47건**에서 닫힙니다.

실제 입력량(측정값, 제목 40자 · 본문 3,000자 가정):

| | 250건 | 300건 |
|---|---|---|
| 기사 본문 합계 | 750,000자 | 900,000자 |
| 실제 프롬프트 합계(ID·구분자·지시문 포함) | 약 775,000자 | 약 930,000자 |
| 요청 1건 최대 | 약 146,000자 | 약 146,000자 |

> 평균 본문 길이와 최악 본문 길이를 혼동하지 마세요. 평균(1,200자) 기준 250건의
> 실제 프롬프트 합계는 약 325,000자로, 최악 조건의 절반 이하입니다.
> 요청 1건이 14만 자 규모이므로 **무료 티어의 분당 토큰 한도(TPM)에 먼저 걸릴 수 있습니다.**
> 걸린다면 `GEMINI_BATCH_MAX_INPUT_CHARS`를 낮추거나 `GEMINI_MIN_INTERVAL_SECONDS`를 올리세요.

캐시에 있는 기사는 배치를 만들기 **전에** 제외되므로, 표시 50건 중 43건이 캐시에 있으면
Gemini에는 7건만 전송됩니다.

### API 키 설정

[Google AI Studio](https://aistudio.google.com/apikey)에서 발급한 키를 환경변수로 전달합니다.

```bash
# 로컬 실행 — .env에 넣으면 run_daily 실행 시 자동 로드됩니다
echo 'GEMINI_API_KEY=your-key-here' >> .env
python -m src.run_daily

# 또는 셸에서 직접
GEMINI_API_KEY=your-key-here python -m src.run_daily --date 2026-08-01 --end_hhmm 0855
```

> ⚠️ **API 키를 저장소에 커밋하지 마세요.** `.env`는 `.gitignore` 대상이며,
> `.env.example`에는 실제 값이 아니라 자리표시자만 둡니다.
> CI에서는 GitHub Secret(`GEMINI_API_KEY`)으로만 주입합니다.

### 모델

기본 모델은 **`gemini-3.5-flash-lite`** 입니다(코드 상수 `DEFAULT_MODEL`, 유일한 정의 지점).
대량의 단순 문서를 JSON으로 뽑아내는 고처리량 작업에 맞춰 선택했고, 지원 모델에서는
thinking level을 최소(`minimal`)로 명시합니다. `-latest` alias는 대상 모델이 예고 없이
바뀔 수 있어 기본값으로 쓰지 않습니다.

```bash
GEMINI_MODEL=gemini-3.6-flash python -m src.run_daily   # 더 강한 모델로 교체
```

모델을 바꾸면 캐시 키가 달라져 이전 모델의 요약을 재사용하지 않습니다.

### 처리량 제어

무료 티어의 RPM/TPM/RPD 수치는 프로젝트·리전마다 달라 코드에 고정하지 않았습니다.
**실제 한도는 [AI Studio](https://aistudio.google.com/)에서 직접 확인**하고, 아래 환경변수로
배치 크기와 처리량을 맞춰 조정하세요.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `GEMINI_API_KEY` | (없음) | 미설정 시 Gemini 전체 비활성화 |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | 사용할 모델 ID |
| `GEMINI_ENABLED` | `1` | `0`이면 키가 있어도 호출하지 않는 kill switch |
| `GEMINI_MAX_SUMMARIES` | `300` | 한 실행에서 요약할 최대 기사 수. `0`이면 비활성 |
| `GEMINI_BATCH_MAX_ARTICLES` | `50` | 요청 1건에 담는 기사 수 |
| `GEMINI_BATCH_HARD_MAX_ARTICLES` | `100` | 배치 크기 hard cap. 이를 넘는 설정은 이 값으로 낮춤 |
| `GEMINI_BATCH_MAX_INPUT_CHARS` | `150000` | 요청 1건의 입력 문자 예산(기사 수보다 먼저 걸릴 수 있음) |
| `GEMINI_ARTICLE_MAX_CHARS` | `3000` | 기사 1건의 본문 최대 길이(문장 경계 보존해 절단) |
| `GEMINI_MAX_REQUESTS_PER_RUN` | `20` | 한 실행의 총 generate 요청 상한(재시도·분할 포함) |
| `GEMINI_MAX_RECOVERY_REQUESTS` | `8` | 그중 재시도·분할에 쓸 수 있는 상한 |
| `GEMINI_MAX_FETCH_ATTEMPTS` | `300` | Gemini 전용 본문 크롤링 상한(추출요약 예산과 별개) |
| `GEMINI_INPUT_MIN_CHARS` | `200` | 이보다 짧은 입력은 호출하지 않음 |
| `GEMINI_MAX_LINE_CHARS` | `90` | 한 줄 최대 길이(초과 시 그 항목만 거부) |
| `GEMINI_REQUEST_TIMEOUT_SECONDS` | `90` | 요청 타임아웃(배치 응답은 단건보다 큼) |
| `GEMINI_RETRY_ATTEMPTS` | `2` | 일시적 오류의 총 시도 횟수 |
| `GEMINI_MIN_INTERVAL_SECONDS` | `2` | 요청 시작 사이의 최소 간격 |
| `GEMINI_CIRCUIT_BREAKER_FAILURES` | `3` | 연속 실패가 이 횟수에 도달하면 남은 기사는 호출하지 않음 |

잘못된 값(음수, 숫자가 아님, 과도하게 큰 값)은 경고 후 기본값을 사용합니다.

### 부분 성공 처리

배치 응답을 all-or-nothing으로 다루지 않고 **항목별로 검증**합니다.

- 요청한 ID와 일치하고 3줄 계약을 만족하는 항목 → **즉시 적용·캐시**
- 누락/알 수 없는 ID/중복 ID/2줄·4줄/빈 문자열/길이 초과/마크다운·번호 → 그 항목만 실패
- 실패한 항목만 더 작은 배치로 재요청합니다. **성공한 항목은 절대 다시 보내지 않습니다.**
- 일부만 실패하면 그 부분집합을 한 번에 재요청하고, 배치 전체가 깨지면 50 → 25 → 10 → 1로
  좁혀 들어갑니다. 개별 호출은 정상 경로가 아니라 **최종 복구 수단**입니다.
- 요청 예산은 **정상 배치와 복구(재시도·분할)를 분리**합니다. 복구 예산
  (`GEMINI_MAX_RECOVERY_REQUESTS`)을 다 써도 아직 보내지 않은 정상 배치는 계속 처리되므로,
  앞쪽 배치의 복구 때문에 뒤쪽 배치가 통째로 굶는 일이 없습니다.
- 총 요청 수가 `GEMINI_MAX_REQUESTS_PER_RUN`에 도달하면 남은 기사는 추출식 요약을 씁니다.

측정한 최악 시나리오 — 250건, 5배치, 각 배치 10% 누락, 재요청에서 1건 재누락:

| 상한 | 실제 요청 수 | Gemini 적용 | extractive fallback |
|---|---|---|---|
| 12 (이전 기본값) | 12 | 196/250 | **54건** — 5번째 배치가 통째로 누락 |
| 20 + 복구 8 (현재 기본값) | 13 | 241/250 | 9건 |
| 20 + 복구 20 | 15 | 245/250 | 5건 |

### 실패 시 동작 (fallback)

표시 요약은 다음 순서로 결정됩니다.

1. 유효한 Gemini 캐시 (`reports/_cache/gemini_summary_cache.json`)
2. Gemini API가 만든, 검증을 통과한 3줄
3. 기존 추출식 요약 (`extractive_summary.py`)
4. 네이버 스니펫(원본 `description`)

캐시는 **기사별**로 저장되며(배치 전체를 한 entry로 저장하지 않음), 키에 canonical URL·모델·
prompt version·schema version이 들어가 모델이나 프롬프트가 바뀌면 자동으로 무효화됩니다.
기사 전문은 캐시에 저장하지 않습니다.

키가 없거나, 인증·쿼터·타임아웃 오류가 나거나, 응답이 계약을 어기면 **해당 기사만** 기존
추출식 요약으로 표시되고 파이프라인은 계속 진행됩니다. 인증 실패(401/403)·잘못된 모델
ID(404)·연속 실패가 임계값에 도달하면 그 실행의 남은 기사에 대해서는 Gemini를 호출하지
않습니다.

AI 요약은 **표시 전용**입니다. 관련성 판정·업권/주제 태깅·이슈 클러스터링·대표 기사
선정·Top 10 순위는 Gemini 사용 여부와 무관하게 동일한 결과를 냅니다.

> 무료 티어는 입력이 Google의 제품 개선에 사용될 수 있습니다. 이 파이프라인이 보내는
> 것은 공개된 뉴스 기사 본문과 제목뿐이며, URL은 전송하지 않습니다.

## 테스트
```bash
pip install pytest   # requirements.txt에 포함되지 않음
python -m pytest tests/ -q
```
테스트는 네트워크에 접근하지 않으며(SMTP/HTTP는 monkeypatch로 대체), 푸시 전에
전체 통과를 확인합니다.

## 리포트 위치
- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/index.html`
- `reports/_candidates/`, `reports/_metrics/` (필터 관측성/품질 지표)
- `reports/_sent/` (이메일 발송 marker)
- `reports/_cache/summary_cache.json` (추출요약 캐시), `reports/_cache/gemini_summary_cache.json` (Gemini 3줄 캐시)

## 관련성 라벨링/후보 모델 준비
Phase 4A의 수동 라벨링 샘플/검증 스크립트는 선택적인 진단·감사용 도구입니다. 기본 워크플로는 사람이 라벨을 편집하지 않는 자동 pseudo-label 기반입니다.

후보 CSV(`reports/_candidates/*.csv`)에서 보수적인 자동 pseudo-label을 생성합니다. `review` 행은 애매한 사례로 표시되며 학습에서 제외됩니다.

```bash
python scripts/generate_relevance_pseudo_labels.py \
  --candidates-dir reports/_candidates \
  --output data/auto_labels/relevance_pseudo_labels.csv \
  --max-rows 5000 \
  --seed 42 \
  --force
```

자동 pseudo-label의 `1`/`0` 행만 사용해 후보 관련성 모델을 학습하고, 평가 지표와 불일치 리포트를 생성합니다.

```bash
python scripts/train_relevance_candidate_model.py \
  --input data/auto_labels/relevance_pseudo_labels.csv \
  --model-output models/relevance_candidate.joblib \
  --metrics-output reports/_metrics/relevance_candidate_eval.json \
  --report-output reports/_metrics/relevance_candidate_eval.txt \
  --disagreements-output reports/_metrics/relevance_disagreements.csv \
  --force
```

일일 GitHub Actions 실행은 `run_daily` 전에 과거 후보 CSV만 사용해 후보 모델을 best-effort로 자동 갱신합니다. 현재 리포트 날짜의 `reports/_candidates/YYYY-MM-DD_candidates.csv`는 학습 입력에서 제외되며, 상태/평가/불일치 파일은 `reports/_metrics` 아래 날짜별 파일로 기록됩니다. 학습 데이터가 부족하거나 갱신에 실패해도 일일 리포트/메일 생성은 계속 진행하고, 기존 유효 후보 모델 또는 `rule_only` fallback을 사용합니다.

```bash
python scripts/refresh_relevance_candidate_model.py \
  --candidates-dir reports/_candidates \
  --model-output models/relevance_candidate.joblib \
  --metrics-output reports/_metrics/2026-05-13_relevance_candidate_eval.json \
  --report-output reports/_metrics/2026-05-13_relevance_candidate_eval.txt \
  --disagreements-output reports/_metrics/2026-05-13_relevance_disagreements.csv \
  --status-output reports/_metrics/2026-05-13_candidate_model_refresh.json \
  --report-date 2026-05-13 \
  --force \
  --best-effort
```

후보 모델은 반드시 `models/relevance_candidate.joblib`로 저장합니다. 일일 실행은 운영 모델 `models/relevance.joblib`가 있으면 기존 authoritative 정책을 사용하고, 운영 모델이 없고 후보 모델이 있으면 자동으로 `candidate_hybrid` 정책을 사용합니다. 후보 hybrid 정책은 명백한 negative 신호를 drop하고 강한 룰 기반 금융 anchor는 보존하며, 확신 구간 밖의 gray-zone은 룰 점수로 fallback합니다. 후보 모델을 운영 모델로 복사하거나 직접 덮어쓰지 않습니다.

필터링 관측성은 `reports/_candidates/YYYY-MM-DD_candidates.csv`와 `reports/_metrics/YYYY-MM-DD_relevance_filter_metrics.json`에 기록됩니다. 후보 모델이 없거나 읽기/예측에 실패해도 일일 실행은 `rule_only` fallback으로 계속 진행합니다.

수동 샘플이 필요할 때만 Phase 4A 유틸리티를 사용합니다.

```bash
python scripts/make_relevance_labeling_sample.py \
  --candidates-dir reports/_candidates \
  --output data/labeling/relevance_labeling_sample.csv \
  --max-samples 700 \
  --seed 42

python scripts/validate_relevance_labels.py \
  --input data/labeling/relevance_labeling_sample.csv \
  --allow-blank \
  --metrics-output reports/_metrics/relevance_label_validation.json
```

## Cloudflare Workers 외부 스케줄러 (`cloudflare-scheduler/`)

GitHub Actions의 cron 트리거는 지연이 잦아, 장기적으로 예약 실행을 Cloudflare
Workers Cron으로 옮기기 위한 스케줄러입니다.

```text
Cloudflare Cron Trigger
  → Worker scheduled()
  → GitHub workflow_dispatch API (daily.yml)
  → GitHub-hosted runner
  → 기존 daily.yml 파이프라인
```

Cloudflare는 **실행 요청만** 담당합니다. 뉴스 수집·리포트 생성·이메일 발송은
기존 파이프라인에서 그대로 수행됩니다. Worker에는 공개 `fetch()` 핸들러가 없고
cron으로만 실행됩니다.

### Cron (KST/UTC)
- Worker cron: `59 23 * * *` (UTC 23:59 = **KST 08:59**)
- 09:03(KST) 전후 메일 도착을 목표로 기존 GitHub schedule보다 조금 앞당긴 값입니다.
- Cloudflare Cron Trigger는 UTC 기준입니다.

### Canary → 운영 전환
- 초기 canary는 `DISPATCH_SEND_EMAIL=false`로 배포합니다. 워크플로는 실행되지만
  이메일은 발송되지 않아 기존 GitHub schedule 발송과 충돌하지 않습니다.
- 검증이 끝나면 `wrangler.jsonc`의 `DISPATCH_SEND_EMAIL`을 `"true"`로 바꿔
  재배포합니다. dispatch는 `force_send=false`로 보내므로 날짜별 sent marker가
  중복 발송을 계속 막아줍니다.

### PAT 최소 권한
- fine-grained PAT, 대상 저장소 `zetatech-a/finance-news-monitor` 하나만 선택
- 권한은 **Actions: Read and write** 만 부여 (workflow_dispatch 호출에 필요)
- 토큰은 반드시 secret으로 저장합니다. `wrangler.jsonc`의 `vars`에 넣지 않습니다.

`wrangler.jsonc`의 `secrets.required`가 `GITHUB_TOKEN`을 필수로 선언하므로,
토큰 없이 배포하면 wrangler가 배포를 **거부**합니다(조용히 성공한 뒤 다음 cron에서
실패하는 일이 없습니다). 다만 최초 배포와 이후 배포의 절차가 다릅니다.

**최초 배포 (Worker가 아직 존재하지 않을 때)**

Worker가 없으면 `wrangler secret put`을 미리 쓸 수 없습니다(설정할 대상이 없음).
이때는 배포와 동시에 secret을 올립니다. 토큰이 셸 히스토리나 저장소에 남지 않도록
저장소 밖 임시 파일을 쓰고 즉시 지웁니다.

```bash
cd cloudflare-scheduler
umask 077
secrets_file="$(mktemp -t fnm-scheduler-secrets.XXXXXX)"
trap 'rm -f "$secrets_file"' EXIT
read -rs -p "GitHub PAT: " pat && echo
printf 'GITHUB_TOKEN=%s\n' "$pat" > "$secrets_file"
unset pat
npx wrangler deploy --secrets-file "$secrets_file"
```

**이후 배포 (Worker가 이미 있을 때)**

```bash
cd cloudflare-scheduler
npx wrangler secret put GITHUB_TOKEN   # 토큰 교체가 필요할 때만
npm run deploy
```

> ⚠️ `wrangler dev`는 `.dev.vars`에 `GITHUB_TOKEN`이 없으면 **셸 환경변수의
> `GITHUB_TOKEN`을 그대로 사용**합니다. 다른 용도의 광범위한 토큰이 환경에 떠 있으면
> 그 토큰으로 dispatch가 나갈 수 있으니, 로컬 실행 시에는 `.dev.vars`에 명시적으로
> 값을 넣거나 `env -u GITHUB_TOKEN npm run dev`로 실행하세요.

### 로컬 검사 / 실행 / 배포
```bash
cd cloudflare-scheduler
npm ci
npm run check   # TypeScript 타입 검사
npm test        # 단위 테스트 (네트워크·실제 PAT 미사용)
npm run dev     # 로컬 실행 (wrangler dev)
npm run deploy  # 배포 (wrangler deploy)
```

로컬에서 scheduled 핸들러를 직접 실행하려면 `npm run dev` 상태에서:

```bash
curl "http://localhost:8787/cdn-cgi/handler/scheduled?cron=59+23+*+*+*&format=json"
```

> ⚠️ 실제 Secret(PAT, Account ID 등)은 저장소에 커밋하지 않습니다.
> 로컬 값은 `.dev.vars`(git-ignored)에만 두고, `.dev.vars.example`에는
> 자리표시자만 유지합니다.

### 기존 GitHub schedule 제거 절차
1차 PR에서는 기존 GitHub schedule 5개를 **그대로 유지**합니다. Cloudflare Cron이
안정적으로 동작하는 것을 확인한 뒤(요청 성공률, 실행 시각, 메일 도착 시각),
별도의 2차 PR에서 `daily.yml`의 `schedule:` 블록을 제거하고 예약 실행을 Cloudflare로
일원화합니다.

## 참고
- 운영 기준(프로덕션 스케줄)은 전일 08:55 ~ 당일 08:55 (KST) 수집, 매일 09:00 전후(KST) 발송을 목표로 합니다.
- 운영 실행 파라미터는 `--window_hours 24 --end_hhmm 0855 --overlap_minutes 15`이며, 오버랩 15분을 적용하면 실제 수집 시작은 전일 08:40(KST)입니다.
- GitHub Actions 스케줄은 08:41/08:49/08:57/09:07/09:17(KST)에 다중 트리거됩니다. 이른 실행은 최대 20분 동안 08:55(KST)까지 대기하고, `reports/_sent/YYYY-MM-DD_email_sent.json` sent-marker로 중복 발송을 방지합니다.
- 수동 실행(`workflow_dispatch`)의 기본값은 메일 미발송이며, 필요할 때만 `send_email=true`로 발송합니다. 이미 sent-marker가 있으면 `force_send=true`를 지정해야 수동 재발송합니다.
- 기본값(`--end_hhmm 0730`)은 로컬/하위호환 용도로 유지되어 기존 07:30 마감 기준 실행도 가능합니다.
- 원문 전문은 저장하지 않고 제목/요약/링크만 저장합니다.
- 보존 정책: 일별 리포트는 180일, `_candidates`/`_metrics`/`_sent`는 90일 보관 후
  일일 워크플로에서 자동 정리됩니다(`scripts/prune_reports.py`). `index.html`과
  `_cache`는 정리 대상이 아닙니다.
