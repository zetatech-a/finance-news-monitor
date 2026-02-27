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
python -m src.run_daily --date 2024-01-01 --window_hours 24 --end_hhmm 0900 --overlap_minutes 15
```

- `--window_hours` (기본: `24.0`)
- `--end_hhmm` (기본: `0730`, 형식: `0900` 또는 `09:00`)
- `--overlap_minutes` (기본: `15`)
- `--dry_run` (윈도우 계산만 출력 후 종료)

## 리포트 위치
- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/index.html`

## 참고
- 운영 기준 수집 구간은 전일 09:00 ~ 당일 09:00 (KST)이며, 기본 오버랩 15분을 적용하면 실제 수집 시작은 전일 08:45입니다.
- 기본값(`--end_hhmm 0730`)을 유지하면 하위호환으로 기존 07:30 마감 기준도 그대로 사용할 수 있습니다.
- 원문 전문은 저장하지 않고 제목/요약/링크만 저장합니다.
