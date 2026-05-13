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

## 리포트 위치
- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/index.html`

## 관련성 라벨링 데이터 준비
후보 CSV(`reports/_candidates/*.csv`)를 사람이 검수할 라벨링 샘플로 변환합니다. 기존 출력 파일은 `--force` 없이는 덮어쓰지 않습니다.

```bash
python scripts/make_relevance_labeling_sample.py \
  --candidates-dir reports/_candidates \
  --output data/labeling/relevance_labeling_sample.csv \
  --max-samples 700 \
  --seed 42
```

허용 라벨 값은 `1`(관련), `0`(무관), `review`(검토 필요)입니다. 학습 전 검증은 다음처럼 실행합니다.

```bash
python scripts/validate_relevance_labels.py \
  --input data/labeling/relevance_labeling_sample.csv \
  --allow-blank \
  --metrics-output reports/_metrics/relevance_label_validation.json
```

권장 최소 라벨 수는 관련 300건, 무관 300건, 검토 100건입니다. `--strict-min-counts`를 추가하면 권장 최소치 미달도 실패로 처리합니다.

## 참고
- 운영 기준(프로덕션 스케줄)은 전일 08:00 ~ 당일 08:00 (KST) 수집, 매일 08:05 전후(KST) 발송입니다.
- 운영 실행 파라미터는 `--window_hours 24 --end_hhmm 0800 --overlap_minutes 15`이며, 오버랩 15분을 적용하면 실제 수집 시작은 전일 07:45(KST)입니다.
- 수동 실행(`workflow_dispatch`)의 기본값은 메일 미발송이며, 필요할 때만 `send_email=true`로 발송합니다.
- 기본값(`--end_hhmm 0730`)은 로컬/하위호환 용도로 유지되어 기존 07:30 마감 기준 실행도 가능합니다.
- 원문 전문은 저장하지 않고 제목/요약/링크만 저장합니다.
