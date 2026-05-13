# Finance News Monitor (MVP)

국내 금융권(대부업권 중심) 일일 언론동향을 수집하고 리포트로 정리하는 MVP입니다.

## 주요 기능
- Naver News Search API 기반 기사 수집
- 업권별 분류 및 키워드 트렌드 집계
- `reports/YYYY-MM-DD.md` 및 `reports/YYYY-MM-DD.html` 리포트 생성
- 최근 14일 리포트 링크를 모은 `reports/index.html` 생성

## 요구사항
- Python 3.11+
- 환경변수 설정
  - `NAVER_CLIENT_ID`
  - `NAVER_CLIENT_SECRET`

## 설치
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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
- `--end_hhmm` (기본: `0730`, 형식: `0800` 또는 `08:00`)
- `--overlap_minutes` (기본: `15`)
- `--dry_run` (윈도우 계산만 출력 후 종료)
- `--disable_candidate_model` (운영 모델이 없을 때 후보 모델을 무시하고 룰만 사용)
- `--candidate_keep_prob` (기본: `0.65`, 후보 모델 hybrid keep 임계값)
- `--candidate_drop_prob` (기본: `0.35`, 후보 모델 hybrid drop 임계값)

## 리포트 위치
- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/index.html`

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

## 참고
- 운영 기준(프로덕션 스케줄)은 전일 08:00 ~ 당일 08:00 (KST) 수집, 매일 08:05 전후(KST) 발송입니다.
- 운영 실행 파라미터는 `--window_hours 24 --end_hhmm 0800 --overlap_minutes 15`이며, 오버랩 15분을 적용하면 실제 수집 시작은 전일 07:45(KST)입니다.
- 수동 실행(`workflow_dispatch`)의 기본값은 메일 미발송이며, 필요할 때만 `send_email=true`로 발송합니다.
- 기본값(`--end_hhmm 0730`)은 로컬/하위호환 용도로 유지되어 기존 07:30 마감 기준 실행도 가능합니다.
- 원문 전문은 저장하지 않고 제목/요약/링크만 저장합니다.
