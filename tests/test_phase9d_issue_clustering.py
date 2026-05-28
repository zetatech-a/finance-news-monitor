from __future__ import annotations

from datetime import datetime, timedelta
import re

from src.pipeline.issue_cluster import cluster_tagged_articles
from src.pipeline.normalize import Article
from src.pipeline.report import render_html
from src.pipeline.tagger import TaggedArticle


def _article(title: str, *, minutes: int = 0, score: int = 6) -> Article:
    a = Article(title=title, description="요약", link=f"https://example.com/{minutes}", originallink=None, naver_link=f"https://n.news.naver.com/{minutes}", pub_date=datetime(2026, 5, 11, 9, 0) + timedelta(minutes=minutes), query="q")
    a.relevance_score = score
    return a


def _tagged(title: str, sector: str = "거시·시장", *, minutes: int = 0, score: int = 6) -> TaggedArticle:
    return TaggedArticle(_article(title, minutes=minutes, score=score), [sector], [sector], [])


def _groups(reps: list[TaggedArticle]) -> list[set[str]]:
    out = []
    for rep in reps:
        group = {rep.article.title}
        group.update(x["title"] for x in (rep.article.related_articles or []))
        out.append(group)
    return out


def test_phase9d_fingerprints_and_guardrails():
    tagged = [
        _tagged("금통위 앞두고 기준금리 동결 전망 확산"), _tagged("한국은행 금융통화위원회 기준금리 동결 유력"), _tagged("한은 금통위, 기준금리 동결 가능성 커져"), _tagged("은행 예금금리 인상"),
        _tagged("채권심리 악화…BMSI 두 달 연속 하락"), _tagged("금리전망 BMSI 급락에 채권시장 경계감"), _tagged("채권시장 심리 위축"),
        _tagged("FOMC 앞두고 미 증시 변동성 확대", "해외·글로벌"), _tagged("연준 금리 경로 불확실성에 글로벌 채권시장 변동", "해외·글로벌"), _tagged("미국 CPI 상승에 뉴욕증시 혼조", "해외·글로벌"), _tagged("미국 PCE 발표 앞두고 국채금리 상승", "해외·글로벌"),
        _tagged("미 국채금리 급등에 뉴욕증시 하락", "해외·글로벌"), _tagged("미국채 금리 상승에 글로벌 채권시장 변동성 확대", "해외·글로벌"), _tagged("미 10년물 국채금리 급등", "해외·글로벌"),
        _tagged("하나금융, 두나무와 원화 스테이블코인 사업 검토", "디지털자산"), _tagged("두나무, 원화 스테이블코인 진출 검토", "디지털자산"), _tagged("업비트 운영사 두나무, 스테이블코인 협력 논의", "디지털자산"),
        _tagged("두나무 피자데이 행사 개최", "디지털자산"), _tagged("두나무 실적 개선", "디지털자산"),
        _tagged("FIU, 가상자산거래소 제재 착수", "디지털자산"), _tagged("금융정보분석원, 코인거래소 제재심 개최", "디지털자산"), _tagged("가상자산거래소 영업정지 제재 논의", "디지털자산"),
        _tagged("비트코인 신고가 경신", "디지털자산"), _tagged("암호화폐 랠리 지속", "디지털자산"),
        _tagged("금융위, 증권사 유동성비율 규제 전체 증권사로 확대", "입법·정책"), _tagged("신조정유동성비율 도입에 증권사 ABCP 관리 강화", "증권"), _tagged("레고랜드 사태 후속 금융투자업규정 개정안 의결", "IB"),
        _tagged("카드채 만기 몰리는데 여전채 금리 4% 돌파", "여전"), _tagged("캐피탈채 금리 상승에 캐피탈사 조달 부담 확대", "여전"), _tagged("여전채 차환 부담 커진 카드사", "여전"),
        _tagged("새도약기금 채권 소각률…대부업권 참여 압박", "대부"), _tagged("대부업계 새도약기금 참여 독려", "입법·정책"), _tagged("장기연체채권 매입 두고 대부업권 압박", "대부"),
    ]
    reps = cluster_tagged_articles(tagged)
    groups = _groups(reps)

    def has_group(*titles: str) -> bool:
        wanted = set(titles)
        return any(wanted.issubset(g) for g in groups)

    assert has_group("금통위 앞두고 기준금리 동결 전망 확산", "한국은행 금융통화위원회 기준금리 동결 유력", "한은 금통위, 기준금리 동결 가능성 커져")
    assert not has_group("금통위 앞두고 기준금리 동결 전망 확산", "은행 예금금리 인상")
    assert has_group("채권심리 악화…BMSI 두 달 연속 하락", "금리전망 BMSI 급락에 채권시장 경계감", "채권시장 심리 위축")
    assert has_group("FOMC 앞두고 미 증시 변동성 확대", "연준 금리 경로 불확실성에 글로벌 채권시장 변동")
    assert has_group("미국 CPI 상승에 뉴욕증시 혼조", "미국 PCE 발표 앞두고 국채금리 상승")
    assert not has_group("FOMC 앞두고 미 증시 변동성 확대", "미국 CPI 상승에 뉴욕증시 혼조")
    assert has_group("미 국채금리 급등에 뉴욕증시 하락", "미국채 금리 상승에 글로벌 채권시장 변동성 확대", "미 10년물 국채금리 급등")
    assert has_group("하나금융, 두나무와 원화 스테이블코인 사업 검토", "두나무, 원화 스테이블코인 진출 검토", "업비트 운영사 두나무, 스테이블코인 협력 논의")
    assert not has_group("두나무, 원화 스테이블코인 진출 검토", "두나무 피자데이 행사 개최")
    assert not has_group("두나무, 원화 스테이블코인 진출 검토", "두나무 실적 개선")
    assert has_group("FIU, 가상자산거래소 제재 착수", "금융정보분석원, 코인거래소 제재심 개최", "가상자산거래소 영업정지 제재 논의")
    assert has_group("비트코인 신고가 경신", "암호화폐 랠리 지속")
    assert not has_group("비트코인 신고가 경신", "FIU, 가상자산거래소 제재 착수")
    assert has_group("금융위, 증권사 유동성비율 규제 전체 증권사로 확대", "신조정유동성비율 도입에 증권사 ABCP 관리 강화", "레고랜드 사태 후속 금융투자업규정 개정안 의결")
    assert has_group("카드채 만기 몰리는데 여전채 금리 4% 돌파", "캐피탈채 금리 상승에 캐피탈사 조달 부담 확대", "여전채 차환 부담 커진 카드사")
    assert has_group("새도약기금 채권 소각률…대부업권 참여 압박", "대부업계 새도약기금 참여 독려", "장기연체채권 매입 두고 대부업권 압박")


def test_phase9d_overclustering_guard_and_determinism_and_report_count():
    base = [
        _tagged("은행 예금금리 인상", "은행", score=5),
        _tagged("기준금리 결정 앞두고 채권시장 관망", "거시·시장", score=8),
        _tagged("여전채 금리 상승에 카드사 조달 부담", "여전", score=7),
        _tagged("미 국채금리 급등에 뉴욕증시 하락", "해외·글로벌", score=6),
        _tagged("금통위 앞두고 기준금리 동결 전망 확산", "거시·시장", score=9),
        _tagged("한국은행 금융통화위원회 기준금리 동결 유력", "거시·시장", score=7),
    ]
    reps1 = cluster_tagged_articles(base)
    reps2 = cluster_tagged_articles(base)
    reps3 = cluster_tagged_articles(list(reversed(base)))

    assert len(reps1) == 4
    map1 = {r.article.cluster_id: r.article.title for r in reps1}
    map2 = {r.article.cluster_id: r.article.title for r in reps2}
    map3 = {r.article.cluster_id: r.article.title for r in reps3}
    assert map1 == map2 == map3

    html = render_html(datetime(2026, 5, 11), reps1, [])
    assert "관련 기사 3건" in html
    m = re.search(r"<strong>전체</strong><span class='count'>(\d+)</span>", html)
    assert m and int(m.group(1)) == 4


def test_phase9d_non_digital_inspection_does_not_join_digital_fiu_sanction():
    tagged = [
        _tagged("금감원 은행권 대출 검사 착수", "은행"),
        _tagged("보험사 현장검사 착수", "보험"),
        _tagged("FIU 가상자산거래소 제재 착수", "디지털자산"),
        _tagged("금융정보분석원, 코인거래소 제재심 개최", "디지털자산"),
    ]
    reps = cluster_tagged_articles(tagged)
    groups = _groups(reps)
    assert any({"FIU 가상자산거래소 제재 착수", "금융정보분석원, 코인거래소 제재심 개최"}.issubset(g) for g in groups)
    assert not any({"금감원 은행권 대출 검사 착수", "FIU 가상자산거래소 제재 착수"}.issubset(g) for g in groups)
    assert not any({"보험사 현장검사 착수", "FIU 가상자산거래소 제재 착수"}.issubset(g) for g in groups)


def test_phase9d_platform_issue_type_separation_and_guards():
    tagged = [
        _tagged("업비트 예치금 증가", "디지털자산"),
        _tagged("빗썸 수수료 인하", "디지털자산"),
        _tagged("코빗 해킹 의혹", "디지털자산"),
        _tagged("증권사 신조정유동성비율 도입", "증권"),
        _tagged("보험사 유동성비율 관리 강화", "보험"),
        _tagged("기준금리 결정 앞두고 채권시장 관망", "거시·시장"),
        _tagged("한은 기준금리 동결 유력", "거시·시장"),
        _tagged("은행 대출 기준금리 산정 방식 변경", "은행"),
        _tagged("카드론 기준금리 조정", "여전"),
        _tagged("대출 기준금리 비교", "은행"),
    ]
    reps = cluster_tagged_articles(tagged)
    groups = _groups(reps)

    assert not any({"업비트 예치금 증가", "빗썸 수수료 인하"}.issubset(g) for g in groups)
    assert not any({"업비트 예치금 증가", "코빗 해킹 의혹"}.issubset(g) for g in groups)
    assert not any({"빗썸 수수료 인하", "코빗 해킹 의혹"}.issubset(g) for g in groups)
    assert not any({"보험사 유동성비율 관리 강화", "증권사 신조정유동성비율 도입"}.issubset(g) for g in groups)

    assert any({"기준금리 결정 앞두고 채권시장 관망", "한은 기준금리 동결 유력"}.issubset(g) for g in groups)
    assert not any({"은행 대출 기준금리 산정 방식 변경", "카드론 기준금리 조정"}.issubset(g) for g in groups)
    assert not any({"은행 대출 기준금리 산정 방식 변경", "대출 기준금리 비교"}.issubset(g) for g in groups)


def test_phase9d_securities_company_alone_does_not_trigger_liquidity_fingerprint():
    tagged = [
        _tagged("증권사 순이익 증가", "증권"),
        _tagged("증권사 신용융자 잔고 증가", "증권"),
        _tagged("증권사 리테일 수수료 경쟁", "증권"),
        _tagged("증권사 신조정유동성비율 도입", "증권"),
    ]
    reps = cluster_tagged_articles(tagged)
    groups = _groups(reps)
    assert len(reps) == 4
    assert not any({"증권사 순이익 증가", "증권사 신용융자 잔고 증가"}.issubset(g) for g in groups)
    assert not any({"증권사 순이익 증가", "증권사 리테일 수수료 경쟁"}.issubset(g) for g in groups)
    assert not any({"증권사 신용융자 잔고 증가", "증권사 리테일 수수료 경쟁"}.issubset(g) for g in groups)
    assert not any({"증권사 순이익 증가", "증권사 신조정유동성비율 도입"}.issubset(g) for g in groups)
    assert not any({"증권사 신용융자 잔고 증가", "증권사 신조정유동성비율 도입"}.issubset(g) for g in groups)
    assert not any({"증권사 리테일 수수료 경쟁", "증권사 신조정유동성비율 도입"}.issubset(g) for g in groups)


def test_phase9d_positive_securities_liquidity_cluster_still_works():
    tagged = [
        _tagged("금융위, 증권사 유동성비율 규제 전체 증권사로 확대", "입법·정책"),
        _tagged("신조정유동성비율 도입에 증권사 ABCP 관리 강화", "증권"),
        _tagged("레고랜드 사태 후속 금융투자업규정 개정안 의결", "IB"),
    ]
    reps = cluster_tagged_articles(tagged)
    groups = _groups(reps)
    assert len(reps) == 1
    assert any(
        {
            "금융위, 증권사 유동성비율 규제 전체 증권사로 확대",
            "신조정유동성비율 도입에 증권사 ABCP 관리 강화",
            "레고랜드 사태 후속 금융투자업규정 개정안 의결",
        }.issubset(g)
        for g in groups
    )
