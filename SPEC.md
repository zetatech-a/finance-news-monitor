# 금융권(대부업 중심) 일일 언론동향 수집/정리 프로그램 명세 (MVP)

## 1. 목적
- 국내 금융권 전반(대부업권 중심)의 주요 언론기사 동향을 매일 자동으로 수집하고 정리한다.
- 결과는 파일로 누적 저장하고(필수), 이메일로 요약을 발송한다(2단계).

## 2. 실행 스케줄
- 평일 08:43 KST 실행/발송 (GitHub Actions, 라운드 타임 혼잡 회피)
- 수집 구간 운영 기준: 전일 08:30 ~ 당일 08:30 (KST)
- 안전 오버랩 15분 적용 시 실제 수집 시작: 전일 08:15 (KST)

## 3. 수집 범위
- 국내 기사 중심
- 데이터 소스
  - Naver News Search API (필수)
  - DeepSearch News API (선택/보강용, 기본 OFF)

## 4. 산출물(파일)
- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/index.html` : 최근 14일 리포트 링크 목록

## 5. 리포트 구성(목차)
1) 오늘의 Top 이슈 10 (제목 + 1~2줄 요약 + 링크)
2) 업권별 주요 기사
   - 은행 / 보험 / 증권 / 카드 / 캐피탈 / 저축은행 / 대부 / 핀테크 / 감독·입법
3) 대부업권 집중 섹션 (정책/감독/입법/소비자이슈/사건사고 등)
4) 키워드 트렌드 (언급량 상위 키워드)

## 6. 저장/저작권 원칙
- 원문 전문 저장 금지(링크 + 제목 + 요약 패시지 중심)
- 모든 항목에 원문 링크 포함

## 7. 실행 명령(로컬/CI 공통)
- 기본 실행:
  - `python -m src.run_daily`
- 옵션:
  - `--date YYYY-MM-DD` (기본: 오늘 날짜, KST 기준)
  - `--window_hours N` (기본: 24.0)
  - `--end_hhmm HHMM` (기본: `0730`, 운영 권장: `0830`, 예: `0900`, `09:00`)
  - `--overlap_minutes N` (기본: 15)
  - `--dry_run` (API 호출/리포트 생성 없이 window 계산만 출력)
  - `--use_deepsearch` (기본: OFF)

## 8. 환경변수
- Naver:
  - `NAVER_CLIENT_ID`
  - `NAVER_CLIENT_SECRET`
- DeepSearch:
  - `DEEPSEARCH_API_KEY`


## 9. GitHub Actions 운영 규칙
- 스케줄: `43 23 * * 0-4` (UTC Sun~Thu) = KST 월~금 08:43
- 실행 시 기준 날짜는 `TZ=Asia/Seoul`로 계산한다.
- 운영 실행 파라미터: `--date $(TZ=Asia/Seoul date +%Y-%m-%d) --window_hours 24 --end_hhmm 0830 --overlap_minutes 15`
- 수동 실행(`workflow_dispatch`)은 기본 메일 미발송이며, 필요할 때만 `send_email=true`로 발송한다.
