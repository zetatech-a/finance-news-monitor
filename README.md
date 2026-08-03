# Finance News Monitor (MVP)

국내 금융권(대부업권 중심) 일일 언론동향을 수집하고 리포트로 정리하는 MVP입니다.

## 주요 기능
- Naver News Search API 기반 기사 수집
- 업권별 분류 및 키워드 트렌드 집계
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
