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
python -m src.run_daily --date 2024-01-01 --window_hours 24 --end_hhmm 0830 --overlap_minutes 15
```

- `--window_hours` (기본: `24.0`)
- `--end_hhmm` (기본: `0730`, 형식: `0830` 또는 `08:30`)
- `--overlap_minutes` (기본: `15`)
- `--dry_run` (윈도우 계산만 출력 후 종료)

## 리포트 위치
- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/index.html`

## 참고
- 운영 기준(프로덕션 스케줄)은 전일 08:30 ~ 당일 08:30 (KST) 수집, 당일 08:43(KST) 발송입니다.
- 운영 실행 파라미터는 `--window_hours 24 --end_hhmm 0830 --overlap_minutes 15`이며, 오버랩 15분을 적용하면 실제 수집 시작은 전일 08:15(KST)입니다.
- 수동 실행(`workflow_dispatch`)의 기본값은 메일 미발송이며, 필요할 때만 `send_email=true`로 발송합니다.
- 기본값(`--end_hhmm 0730`)은 로컬/하위호환 용도로 유지되어 기존 07:30 마감 기준 실행도 가능합니다.
- 원문 전문은 저장하지 않고 제목/요약/링크만 저장합니다.
