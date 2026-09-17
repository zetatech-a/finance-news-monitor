# 대부업권 뉴스 누락·오병합 분석 (2026-09-17)

기준 커밋: `e67e3a740b0004e56a0087aa34edfeaffc6264ae` (`origin/main`).
원본 리포트·캐시·후보·metrics는 수정하지 않았다.

## 실제 발생 단계

원본 `reports/2026-09-17.html`, `.md`, `_candidates` CSV와 `_metrics` 두 JSON을 대조했다.

| 단계 | 원본 실행 건수 | 동작과 확인 범위 |
| --- | ---: | --- |
| fetch | 1,746 | `queries.yml` → Naver API, 최신순, 페이지당 100건, 쿼리당 기본 5페이지. 유효 날짜의 `[start,end)` 기사만 보존 |
| normalize | 1,746 | 제목/링크 없는 항목 제외, 원본 description 보존 |
| dedup | 1,318 | 제목+URL exact 제거 후 정규화 제목별 대표. 최신/긴 설명 우선, 흡수 출처 메타 보존 |
| pre-filter | 1,301 | 스포츠·연예 도메인/키워드, 정치 전용, 공유재산 임대 문맥 제거 |
| relevance | 972 | candidate_hybrid; 강한 domain 규칙, 모델 확률, gray-zone 규칙 순으로 판정 |
| tagging | 972 | 제목/설명으로 최적 업권, 복수 주제 부여 |
| issue clustering | 400 | 클러스터마다 대표 하나만 다음 단계로 전달 |
| report | 280 | 추출요약으로 description 갱신 후 재태깅, 기타 업권 노출 제한 적용 |

후보 CSV는 **pre-filter 이후, relevance 판정이 기록된 데이터**다. 그 안에 있는 기사는 수집·정규화·dedup·pre-filter를 이미 통과했다. 없는 기사가 어느 앞 단계에서 빠졌는지는 원시 수집 로그 없이 특정할 수 없다.

문제의 새도약기금 네 기사는 모두 CSV에 있고 `keep=1`이다. 따라서 이 사례는 fetch 누락이 아니다. 한국TI 정책 발표 카드의 원본 cluster_size는 18이며, 관련 기사 목록에 네 기사가 들어 있다. 후보 단위로 재생하면 정책 발표와 다른 사건들이 16건 그룹으로 묶인다. 원본은 dedup 흡수분까지 포함하므로 크기가 다르다.

## 원인

1. `_finance_policy_fingerprint`가 **새도약기금/장기연체채권 + 대부업권**과 **불법사금융/상품권 사채/내구제대출**에 같은 `finance:loan_relief`를 반환했다. 같은 업권이면 제목 유사도 확인 없이 즉시 병합됐고, 이 fingerprint는 업권 간 병합도 허용됐다. `불법대부` 단독 자체는 이 분기에 없지만 다른 rule fingerprint/유사도 경로도 존재한다.
2. 클러스터에 있는 **어느 한 멤버와만** 맞아도 합류하는 greedy single-link 구조였다. 연결요소를 완전히 계산하는 union-find는 아니지만 A–B–C bridge 오염은 가능했고 입력 순서에도 영향을 받았다.
3. 대표 선정은 relevance score, 제목 길이, 메타, 시각 순이었다. 정책 발표의 score=25가 새도약기금 16/6/6/6보다 높아 대표가 됐다. `cluster_tagged_articles`가 대표만 반환하고 관련 링크는 최대 5개만 저장하므로, 다른 사건은 독립 카드에서 사라졌다. 대표를 바꾸는 것만으로는 나머지 사건이 계속 숨는다.
4. 별도 relevance 누락도 있었다. 안전 매칭의 짧은 `대부업` 토큰은 `대부업체/대부업권/대부업자` 안에서 잡히지 않았다. `불법 사금융/미등록 대부/불법 사채`도 붙여 쓴 canonical과 달랐다. 예: 우수대부 공급 집중 기사 score=1, 서울시 띄어쓰기 보도 score=0/1/5. 또한 `대부업법;불법사채`로 score=12인 기사도 domain anchor 목록 불일치 때문에 탈락했다.
5. 태거는 이미 대부업권/업체와 새도약기금의 대부 문맥을 보호한다. 최초 수집 snippet으로 태깅한 대부 대표 수와 추출요약 후 최종 업권 수는 다를 수 있다. Gemini는 그 뒤의 표시 전용 단계여서 이번 오병합 원인이 아니다.

## 수집 recall 판단

`fetch_queries`는 주석으로 단독 `대부`를 노이즈 때문에 의도적으로 배제한다. `대부업`, 감독·제재·법 개정 쿼리, `불법사금융 단속`, `미등록대부` 등은 이미 있다. 제공된 최근 1일 네이버 검색 URL을 열었으나 웹 도구가 non-retryable unsafe/open 오류를 반환해 검색 결과를 확보하지 못했다.

따라서 단독 `대부`의 증분 관련 기사 수, 비금융 노이즈 비율, API 페이지 비용이나 **collection recall 수치는 산출하지 않았다**. 현재 후보만으로 검색엔진의 토큰 매칭이나 미수집 기사를 추측하지 않았다. 이번에는 검색어를 그대로 두고, 실제 관측된 필터·노출 recall을 복구했다. 향후 동일 시간창의 broad-query 결과를 별도 표본으로 저장하고 기존 쿼리 URL 합집합과 비교해야 수집 확장 여부를 판단할 수 있다.

## 선택한 설계

- 채무조정 fingerprint에서 불법금융 전체를 제거했다. 프로그램/장기연체채권, 거래·참여 행위, 대부업권 문맥이 함께 필요하고 프로그램 식별자는 제목에도 있어야 한다. 배경 설명만으로 이슈를 바꾸지 않는다.
- 안정된 제목/URL/설명 순으로 처리하고 **모든 멤버와 pair 판정이 맞을 때만** 합류시킨다. 어떤 클러스터에도 서로 불일치하는 두 멤버가 공존하지 못한다. 대표 선정 정책은 그대로다.
- 강화만 하면 동일 사건 분할이 생겨, 기관+단속 대상+집행 행위가 있는 지역 단속과 제목의 **측정 지표에 직접 결합된 정확한 비율**을 사건 근거로 보완했다. 특정 서울시/날짜/기사 제목/수치는 생산 코드에 넣지 않았다. 공통 변화폭 `%p`만으로 같은 통계라고 보지 않는다.
- 중앙 safe matcher에 명시적인 대부업 복합어와 띄어쓰기 alias를 등록한다. `대부` 자체를 substring으로 풀지 않으며 동일 canonical 점수를 중복 합산하지 않는다. 점수에 있는 금융법·불법금융 앵커를 domain 판정에도 맞췄다.
- pipeline 순서, ML 정책/임계값, query, 대표 랭킹, Gemini, daily workflow, 생성 보고서는 유지했다. production dependency 추가 없음.

## 측정 결과

`before.json`은 코드 변경 전 실행 결과, `after.json`은 최종 코드 실행 결과다. 확률은 CSV에 저장된 반올림 값을 재사용한다. 원시 수집/모델 재추론/본문 크롤링은 하지 않는다. 시간은 중립값으로 놓고 query와 흡수 출처는 비워 두므로, **production-equivalent run이나 재생성된 daily report가 아니다**.

37건 golden의 정답은 관련 33건(8개 사건), 비관련 4건이다. 실제 snippet과 수동 사건 라벨의 출처는 fixture README에 있다.

| golden 지표 | 변경 전 | 변경 후 |
| --- | ---: | ---: |
| 관련 후보 유지 | 23/33 (69.7%) | 33/33 (100%) |
| filter precision | 23/23 (100%) | 33/33 (100%) |
| cluster purity (군집별 최대 정답 사건 수 합 / 기사 수) | 78.3% | 100% |
| 병합 pair precision | 54.3% | 100% |
| 유지 기사 내 same-event pair recall | 100% | 92.2% |
| cluster count | 3 | 11 |
| 대부 대표 수 | 1 | 9 |

분모가 달라지는 효과를 분리했다. **기존 keep 23건만 고정**하면 수정 후 5개 순수 cluster, pair recall 100%다. 새도약기금 4건, 정책 발표 1건, 단속 11건, 보험 4건, PG 3건이 각각 유지된다. 전체 replay에서는 새로 구제된 형사사건 5건이 4개 cluster로 갈라져 pair recall이 낮아진다. 이 conservative fragmentation은 남은 한계이며 숨기지 않는다. 작은 목적 표본의 100% precision을 전체 서비스 precision으로 일반화할 수 없다.

아래는 **전체 후보 CSV의 기록된 keep 집합을 고정한 클러스터 비교**다. 정답 사건 라벨이 없으므로 전체 purity 수치는 만들지 않았다.

| 날짜 | 입력/keep | cluster 전→후 | 50건 이상 전→후 | 최대 크기 전→후 | 대부 대표 전→후 |
| --- | --- | --- | --- | --- | --- |
| 09-17 | 1,301 / 972 | 400→449 | 3→2 | 162→176 | 2→4 |
| 09-16 | 1,041 / 662 | 320→360 | 1→1 | 118→111 | 6→6 |
| 09-15 | 985 / 652 | 326→350 | 2→2 | 90→84 | 2→10 |

점수까지 재계산하면 09-17은 keep 982 / cluster 455 / 대부 대표 10, 09-16은 664 / 362 / 8, 09-15는 652 / 350 / 10이다. 원본 최종 HTML의 대부 1개와 이 **요약 전** 대표 수를 직접 같은 지표로 비교하지 않는다.

고정 입력의 cluster 수 증가는 약 7.4–12.5%다. 기존 테스트와 보험/PG 정답 표본은 유지되지만, 전체 기사에 대한 무회귀를 입증한 것은 아니다. 특히 09-17 거시 최대 cluster는 **162→176으로 증가**했다. 기존 macro/digital fingerprint와 greedy 그룹 배정 영향이 남아 있다. 근거가 넓은 이 fingerprint들을 정비하려면 별도의 사건 라벨과 기존 정책 테스트 재검토가 필요하다.

## 재현 및 검증

```text
python -m scripts.evaluate_loan_news --candidates reports/_candidates/2026-09-17_candidates.csv reports/_candidates/2026-09-16_candidates.csv reports/_candidates/2026-09-15_candidates.csv --output evaluation.json
python -m pytest tests/test_loan_news_regression.py tests/test_text_matcher.py tests/test_issue_cluster.py tests/test_issue_cluster_quality.py tests/test_phase9d_issue_clustering.py tests/test_relevance_false_negative.py tests/test_phase9a_loan_business_sector.py -q
python -m pytest tests/ -q
```

Before 재현은 기준 커밋의 별도 checkout에 평가 스크립트와 fixture만 복사해 실행한다. 스크립트의 후속 추가 출력(기존 keep golden/최대 cluster 표본)은 before JSON에는 없으나 공통 지표 정의는 같다.

집중 검증: 54 passed. 신규 테스트는 실제 09-17 누락·독립 카드·사건 순도·동일 보도 유지, 기관/단속 대상 차이, 다른 불법금융 사건, bridge 입력 순열, 배경의 기금 언급, 비율과 변화폭 구분, pre-filter 통과, 비금융 동음이의어와 alias 중복 점수를 검증한다. 기존 issue-clustering 테스트는 모두 유지했다.

Windows 초기 전체 실행은 718 passed / 1 skipped / 3 failed였다. 실패는 수정과 무관한 Windows 경로 구분자 가정 1건, 실행 불가능한 WSL bash 2건이었다. Linux 컨테이너의 첫 실행도 Windows checkout의 CRLF 설정 누락으로 Git guard가 모든 파일을 변경으로 읽었다. `core.autocrlf=true`를 맞춰 실제 diff만 보도록 교정했다. guard opt-out은 사용하지 않았다.

최종 전체 검증: **Linux / Python 3.11, 726 passed / 1 skipped (16.20s)**.
고정된 requirements와 pytest를 설치한 Docker 이미지에서 repository와 Git 메타데이터를
읽기 전용으로 연결하고 `--network none`으로 실행했다. 명령은
`python -m pytest tests/ -q -p no:cacheprovider`이며, 쓰기 불필요한 pytest cache만 끈다.
`git diff --check`도 통과했다.

API 키를 사용한 Naver/Gemini production 실행과 실제 메일 발송은 수행하지 않았다. 실시간 broad-query 표본, 과거 모델 원본, raw fetch provenance 및 추출요약 후 재태깅의 재현은 이번 결정론적 검증 범위 밖이다.
