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
python -m src.run_daily --date 2024-01-01 --window_hours 13.5
```

## 리포트 위치
- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/index.html`

## 참고
- 수집 구간은 기본적으로 전일 07:30 ~ 당일 07:30 (KST)입니다.
- 원문 전문은 저장하지 않고 제목/요약/링크만 저장합니다.
