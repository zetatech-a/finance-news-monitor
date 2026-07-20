from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown

from src.pipeline.tagger import TaggedArticle
from src.pipeline.content_type import classify_content_type
from src.pipeline.source_quality import source_quality_rank_adjustment

import re
import html as ihtml


def md_escape(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"<[^>]+>", "", t)
    t = ihtml.unescape(t)
    t = t.replace("\\", "\\\\")
    t = t.replace("[", r"\[").replace("]", r"\]")
    t = t.replace("(", r"\(").replace(")", r"\)")
    return t


def md_link(title: str, url: str) -> str:
    safe_title = md_escape(title)
    safe_url = (url or "").strip()
    return f"[{safe_title}](<{safe_url}>)"


def _truncate(text: str, n: int = 170) -> str:
    t = (text or "").strip().replace("\n", " ").replace("\r", " ")
    if len(t) > n:
        return t[: n - 3].rstrip() + "..."
    return t


def _h(text: str) -> str:
    return ihtml.escape((text or "").strip(), quote=True)


def _fmt_dt(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, datetime):
        return x.strftime("%m-%d %H:%M")
    try:
        s = str(x)
        return s[:16]
    except Exception:
        return ""


def _ts_dt(x: Any) -> float:
    if isinstance(x, datetime):
        try:
            return float(x.timestamp())
        except Exception:
            return 0.0
    return 0.0


def _get_press(article: Any) -> str:
    for key in ("press", "publisher", "office", "company", "source"):
        v = getattr(article, key, None)
        if v:
            return str(v).strip()
    return ""


def _link_naver(article: Any) -> str:
    return (getattr(article, "naver_link", None) or "").strip()


def _link_original(article: Any) -> str:
    return (getattr(article, "originallink", None) or "").strip()


def _link_fallback(article: Any) -> str:
    return (getattr(article, "link", None) or "").strip()


def _primary_link(article: Any) -> str:
    # 제목 클릭은 네이버 우선, 없으면 원문, 없으면 link
    return _link_naver(article) or _link_original(article) or _link_fallback(article)


def _field(article: Any, key: str) -> Any:
    if isinstance(article, dict):
        return article.get(key)
    return getattr(article, key, None)


def _numeric_field(article: Any, *keys: str) -> float | None:
    for key in keys:
        v = _field(article, key)
        if v in (None, ""):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            continue
    return None


def _is_high_confidence_misc(article: Any) -> bool:
    prob = _numeric_field(article, "relevance_prob", "prob")
    if prob is not None:
        return prob >= 0.80
    score = _numeric_field(article, "relevance_score", "score")
    return score is not None and score >= 6


# 리포트 섹션 표시 순서(대부업권 우선). "기타"는 마크다운에서만 목록 말미에 붙는다.
SECTOR_ORDER: tuple[str, ...] = (
    "대부",
    "은행",
    "저축은행",
    "상호금융",
    "여전",
    "보험",
    "증권(브로커리지/리테일)",
    "자산운용·연기금",
    "IB·자본시장",
    "핀테크·플랫폼",
    "디지털자산",
    "거시·시장",
    "감독·제재",
    "입법·정책",
)


def _primary_sector(item: TaggedArticle) -> str:
    return item.sectors[0] if item.sectors else "기타"


def _is_visible_in_main_report(item: TaggedArticle) -> bool:
    sector = _primary_sector(item)
    return sector != "기타" or _is_high_confidence_misc(item.article)


def _build_visible_sector_buckets(
    items: list[TaggedArticle],
) -> tuple[dict[str, list[TaggedArticle]], list[str]]:
    by_sector: dict[str, list[TaggedArticle]] = defaultdict(list)
    misc_review_items: list[TaggedArticle] = []
    for item in items:
        if not _is_visible_in_main_report(item):
            continue
        sector = _primary_sector(item)
        if sector == "기타":
            misc_review_items.append(item)
            continue
        by_sector[sector].append(item)

    misc_review_items = sorted(
        misc_review_items,
        key=lambda x: _ts_dt(getattr(x.article, "pub_date", None)),
        reverse=True,
    )[:10]
    if misc_review_items:
        by_sector["기타"] = misc_review_items

    ordered_sectors = list(SECTOR_ORDER) + [s for s in by_sector.keys() if s not in SECTOR_ORDER]
    return by_sector, ordered_sectors


def _relevance_value(article: Any) -> float | None:
    """
    관련도 값은 score 우선, 없으면 확률값 fallback.
    """
    for k in ("relevance_score", "score"):
        v = _field(article, k)
        if isinstance(v, (int, float)):
            return float(v)
    for k in ("relevance_prob", "prob", "relevance"):
        v = _field(article, k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _relevance_label(v: float) -> tuple[str, str]:
    # (label, css_class)
    if v >= 8:
        return ("High", "r-high")
    if v >= 4:
        return ("Med", "r-med")
    return ("Low", "r-low")


def _relevance_label_for_article(article: Any, rel: float) -> tuple[str, str]:
    score_v = _field(article, "relevance_score")
    if not isinstance(score_v, (int, float)):
        score_v = _field(article, "score")
    if isinstance(score_v, (int, float)):
        return _relevance_label(float(score_v))
    return _relevance_label(rel * 10.0 if 0.0 <= rel <= 1.0 else rel)


def _relevance_sort_value_for_article(article: Any, rel: float) -> float:
    score_v = _field(article, "relevance_score")
    if not isinstance(score_v, (int, float)):
        score_v = _field(article, "score")
    if isinstance(score_v, (int, float)):
        return float(score_v)
    return rel * 10.0 if 0.0 <= rel <= 1.0 else rel


def _is_schedule_notice_article(text: str) -> bool:
    schedule_notice_patterns = (
        "다음주",
        "이번주",
        "주요 일정",
        "브리핑 일정",
        "일정",
        "행사 안내",
        "개최 안내",
        "세미나 개최",
        "포럼 개최",
    )
    return any(p in text for p in schedule_notice_patterns)


def _has_material_policy_action(text: str) -> bool:
    material_patterns = (
        "입법예고",
        "시행령",
        "시행규칙",
        "제도 개편",
        "규제 완화",
        "규제 강화",
        "제재",
        "과징금",
        "행정처분",
        "검사 결과",
        "대책 발표",
    )
    return any(p in text for p in material_patterns)


def _top_rank_score(item: TaggedArticle) -> float:
    sector = _primary_sector(item)
    topics = set(item.topics or [])
    title = (getattr(item.article, "title", None) or "").lower()
    summary = (getattr(item.article, "description", None) or "").lower()
    text = f"{title} {summary}"

    def _has_any(needles: tuple[str, ...]) -> bool:
        return any(n in text for n in needles)

    score = 0.0
    relevance = _relevance_value(item.article)
    if isinstance(relevance, float):
        score += relevance if relevance > 1.0 else relevance * 7.5
    cluster_size = _field(item.article, "cluster_size")
    if isinstance(cluster_size, int):
        score += min(max(cluster_size, 1), 5) * 0.28

    sector_weights = {
        "대부": 2.1,
        "저축은행": 1.6,
        "상호금융": 1.5,
        "여전": 1.5,
        "보험": 1.3,
        "감독·제재": 1.8,
        "입법·정책": 1.8,
        "IB·자본시장": 1.25,
    }
    score += sector_weights.get(sector, 0.0)

    anchor_weights = {
        ("불법사금융", "불법추심", "보이스피싱", "고리대금", "새도약기금"): 2.0,
        ("연체", "부실", "pf", "부동산pf", "건전성", "자본규제"): 1.5,
        ("자금시장", "유동성", "여전채", "카드채", "캐피탈채", "abcp"): 1.4,
        ("금감원", "금융위", "검사", "제재", "과징금", "입법예고", "제도개선"): 1.3,
        ("k-ics", "킥스", "지급여력", "실손", "불완전판매"): 1.2,
        ("fiu", "sto", "스테이블코인", "가상자산거래소"): 1.5,
        ("fomc", "cpi", "pce", "기준금리", "국채금리", "달러", "외환"): 0.9,
    }
    score += sum(weight for keys, weight in anchor_weights.items() if _has_any(keys))
    topic_text = " ".join(topics).lower()
    score += sum(weight * 0.7 for keys, weight in anchor_weights.items() if any(k in topic_text for k in keys))

    if _has_any(("비트코인 신고가", "암호화폐 랠리", "코인 시세", "뉴욕증시 상승 마감", "나스닥 상승 마감")):
        score -= 1.2
    if _has_any(("피자데이", "이벤트", "행사 개최", "브랜드 캠페인")):
        score -= 1.3
    if _has_any(("금융권 소식", "금융 레이더", "업계 소식", "종합")):
        score -= 1.1

    content_type_weights = {
        "regulatory": 1.5,
        "risk": 1.3,
        "hard_news": 0.6,
        "market": 0.2,
        "product": -0.2,
        "price_quote": -0.8,
        "schedule": -2.5,
        "opinion": -2.3,
        "profile": -2.0,
        "event": -1.8,
        "pr": -1.7,
        "local_social": -1.6,
        "briefing": -1.4,
    }
    content_type = classify_content_type(item)
    score += content_type_weights.get(content_type, 0.0)
    score += source_quality_rank_adjustment(item)

    low_value_topic_penalties = {
        "일정·브리핑": -1.5,
        "업계동정·사회공헌": -1.2,
        "칼럼·오피니언": -1.8,
    }
    score += sum(weight for topic, weight in low_value_topic_penalties.items() if topic in topics)
    if content_type == "price_quote" and "증시·시장시황" in topics:
        score -= 0.4

    if _is_schedule_notice_article(text):
        score -= 0.8
        if not _has_material_policy_action(text):
            score -= 0.6

    score += _ts_dt(getattr(item.article, "pub_date", None)) / 1_000_000_000_000
    return score


def _select_top_items(tagged: list[TaggedArticle], limit: int = 10) -> list[TaggedArticle]:
    pool = [it for it in tagged if _is_visible_in_main_report(it)]
    ranked = sorted(
        pool,
        key=lambda it: (
            _top_rank_score(it),
            _ts_dt(getattr(it.article, "pub_date", None)),
            (_field(it.article, "title") or ""),
        ),
        reverse=True,
    )

    selected: list[TaggedArticle] = []
    seen_clusters: set[str] = set()
    seen_titles: set[str] = set()
    sector_counts: dict[str, int] = defaultdict(int)
    topic_counts: dict[str, int] = defaultdict(int)
    cap_by_sector = {"거시·시장": 2, "디지털자산": 2, "기타": 1}
    sector_limit = 3

    def norm_title(x: str) -> str:
        return re.sub(r"\s+", " ", (x or "").strip().lower())

    def is_global_topic(item: TaggedArticle) -> bool:
        return "해외·글로벌" in set(item.topics or [])

    def is_generic_market_move(item: TaggedArticle) -> bool:
        text = f"{(_field(item.article, 'title') or '').lower()} {(_field(item.article, 'description') or '').lower()}"
        needles = ("비트코인 신고가", "암호화폐 랠리", "코인 시세", "뉴욕증시 상승 마감", "나스닥 상승 마감", "피자데이", "행사 개최")
        return any(n in text for n in needles)

    global_count = 0
    generic_move_count = 0

    for item in ranked:
        if len(selected) >= limit:
            break
        sector = _primary_sector(item)
        cluster_id = str(_field(item.article, "cluster_id") or "").strip()
        title_key = norm_title(getattr(item.article, "title", None) or "")
        if not title_key or title_key in seen_titles:
            continue
        if cluster_id and cluster_id in seen_clusters:
            continue
        if sector_counts[sector] >= cap_by_sector.get(sector, sector_limit):
            continue
        if is_global_topic(item) and global_count >= 2:
            continue
        if is_generic_market_move(item) and generic_move_count >= 2:
            continue
        selected.append(item)
        seen_titles.add(title_key)
        if cluster_id:
            seen_clusters.add(cluster_id)
        sector_counts[sector] += 1
        top_topic = (item.topics or [""])[0]
        if top_topic:
            topic_counts[top_topic] += 1
        if is_global_topic(item):
            global_count += 1
        if is_generic_market_move(item):
            generic_move_count += 1

    if len(selected) < limit:
        def _has_remaining_non_capped_candidate() -> bool:
            for cand in ranked:
                cluster_id = str(_field(cand.article, "cluster_id") or "").strip()
                title_key = norm_title(getattr(cand.article, "title", None) or "")
                if not title_key or title_key in seen_titles:
                    continue
                if cluster_id and cluster_id in seen_clusters:
                    continue
                if _primary_sector(cand) not in {"거시·시장", "디지털자산"}:
                    return True
            return False

        for item in ranked:
            if len(selected) >= limit:
                break
            sector = _primary_sector(item)
            cluster_id = str(_field(item.article, "cluster_id") or "").strip()
            title_key = norm_title(getattr(item.article, "title", None) or "")
            if not title_key or title_key in seen_titles:
                continue
            if cluster_id and cluster_id in seen_clusters:
                continue
            if sector in {"거시·시장", "디지털자산"} and _has_remaining_non_capped_candidate():
                continue
            selected.append(item)
            seen_titles.add(title_key)
            if cluster_id:
                seen_clusters.add(cluster_id)

    if len(selected) < limit:
        for item in pool:
            if len(selected) >= limit:
                break
            cluster_id = str(_field(item.article, "cluster_id") or "").strip()
            title_key = norm_title(getattr(item.article, "title", None) or "")
            if not title_key or title_key in seen_titles:
                continue
            if cluster_id and cluster_id in seen_clusters:
                continue
            selected.append(item)
            seen_titles.add(title_key)
            if cluster_id:
                seen_clusters.add(cluster_id)

    return selected


def visible_report_items(tagged: list[TaggedArticle]) -> list[TaggedArticle]:
    """Return the same article set that the HTML report renders in sector sections."""
    by_sector, ordered_sectors = _build_visible_sector_buckets(tagged)
    return [item for sector in ordered_sectors for item in by_sector.get(sector, [])]


def top_report_items(
    tagged: list[TaggedArticle], limit: int = 10
) -> list[TaggedArticle]:
    """Return the same Top issue items selected for the HTML report."""
    return _select_top_items(visible_report_items(tagged), limit=limit)


def render_markdown(
    report_date: datetime,
    tagged: list[TaggedArticle],
    keyword_trends: list[tuple[str, int]],
) -> str:
    header = f"# 금융권 일일 언론동향 ({report_date.strftime('%Y-%m-%d')})"
    lines = [header, ""]

    top_items = _select_top_items(tagged, limit=10)

    lines.append("## 오늘의 Top 이슈 10")
    if top_items:
        for item in top_items:
            a = item.article
            lines.append(
                f"- {md_link(a.title or '', _primary_link(a))} — {md_escape(_truncate(a.description, 180))}"
            )
    else:
        lines.append("- 해당 기간 기사 없음")

    lines.append("")
    lines.append("## 업권별 주요 기사")

    # HTML 리포트와 동일한 노출 규칙 사용 — '기타'는 고신뢰 기사만 최대 10건.
    # (과거에는 MD가 모든 기타 기사를 노출해 HTML과 목록이 어긋났다)
    by_sector, ordered_sectors = _build_visible_sector_buckets(tagged)
    for sector in ordered_sectors:
        if sector not in by_sector:
            continue
        lines.append(f"### {sector}")
        for item in sorted(
            by_sector[sector],
            key=lambda x: _ts_dt(getattr(x.article, "pub_date", None)),
            reverse=True,
        )[:10]:
            a = item.article
            lines.append(
                f"- {md_link(a.title or '', _primary_link(a))} — {md_escape(_truncate(a.description, 170))}"
            )
        lines.append("")

    lines.append("## 키워드 트렌드")
    if keyword_trends:
        for keyword, count in keyword_trends:
            lines.append(f"- {keyword}: {count}")
    else:
        lines.append("- 키워드 데이터 없음")

    lines.append("")
    lines.append("---")
    lines.append("본 리포트는 Naver News Search API 기반으로 생성되었습니다.")
    return "\n".join(lines)


# =========================
# ✅ 제품형 UI용 CSS / JS — templates/ 파일에서 로드
# =========================

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# import 시점에 1회 로드 후 HTML에 인라인 삽입 — 산출물 형태는 기존과 동일하다.
# UI 수정은 파이썬 문자열이 아니라 templates/*.css, *.js 파일에서 한다.
_UI_CSS = (_TEMPLATES_DIR / "report.css").read_text(encoding="utf-8")
_UI_JS = (_TEMPLATES_DIR / "report.js").read_text(encoding="utf-8")
_LIGHT_CSS = (_TEMPLATES_DIR / "light.css").read_text(encoding="utf-8")




def render_html(
    report_date: datetime,
    tagged: list[TaggedArticle],
    keyword_trends: list[tuple[str, int]],
) -> str:
    date_str = report_date.strftime("%Y-%m-%d")

    by_sector, ordered_sectors = _build_visible_sector_buckets(tagged)
    visible_items = [item for s in ordered_sectors for item in by_sector.get(s, [])]
    top_items = _select_top_items(visible_items, limit=10)
    sector_counts = {s: len(by_sector.get(s, [])) for s in ordered_sectors}
    def pill_html(sector: str, count: int) -> str:
        return f"<button class='pill' data-sector-pill data-sector='{_h(sector)}'><strong>{_h(sector)}</strong><span class='count'>{count}</span></button>"

    pills = [
        "<button class='pill active' data-sector-pill data-sector='ALL'><strong>전체</strong><span class='count'>{}</span></button>".format(
            len(visible_items)
        )
    ]
    for s in ordered_sectors:
        count = sector_counts[s]
        if count <= 0:
            continue
        pills.append(pill_html(s, count))

    topic_counts: dict[str, int] = defaultdict(int)
    no_topic_count = 0
    for item in visible_items:
        topics = item.topics or []
        if not topics:
            no_topic_count += 1
        for topic in topics:
            topic_counts[topic] += 1
    topic_pills = [
        "<button class='pill active' data-topic-pill data-topic='ALL'><strong>전체 주제</strong></button>"
    ]
    for topic, count in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        topic_pills.append(
            f"<button class='pill' data-topic-pill data-topic='{_h(topic)}'><strong>{_h(topic)}</strong><span class='count'>{count}</span></button>"
        )
    if no_topic_count > 0:
        topic_pills.append(
            f"<button class='pill' data-topic-pill data-topic='__NO_TOPIC__'><strong>주제 없음</strong><span class='count'>{no_topic_count}</span></button>"
        )

    def card_html(item: TaggedArticle, is_top: bool) -> str:
        a = item.article
        sector = item.sectors[0] if item.sectors else "기타"
        topics = item.topics or []
        topic_joined = "|".join(topics)

        title = a.title or ""
        summary = a.description or ""
        pub = _fmt_dt(getattr(a, "pub_date", None))
        ts = _ts_dt(getattr(a, "pub_date", None))
        press = _get_press(a)

        naver = _link_naver(a)
        orig = _link_original(a)
        primary = _primary_link(a)

        rel = _relevance_value(a)
        cluster_id = _field(a, "cluster_id")
        cluster_size = _field(a, "cluster_size")
        try:
            cluster_size_int = int(cluster_size or 1)
        except (TypeError, ValueError):
            cluster_size_int = 1
        related_articles = _field(a, "related_articles") or []
        if not isinstance(related_articles, list):
            related_articles = []
        rel_label = None
        rel_class = ""
        rel_val = 0.0
        if isinstance(rel, float):
            rel_val = _relevance_sort_value_for_article(a, rel)
            rel_label, rel_class = _relevance_label_for_article(a, rel)

        cached = bool(getattr(a, "summary_cached", False))
        hay = " ".join([title, summary, sector, press, " ".join(topics)]).strip()

        btns: list[str] = []
        if naver:
            btns.append(
                f"<a class='btn small primary' href='{_h(naver)}' target='_blank' rel='noopener noreferrer'>네이버</a>"
            )
        if orig and orig != naver:
            btns.append(
                f"<a class='btn small' href='{_h(orig)}' target='_blank' rel='noopener noreferrer'>원문</a>"
            )
        if not btns and primary:
            btns.append(
                f"<a class='btn small primary' href='{_h(primary)}' target='_blank' rel='noopener noreferrer'>열기</a>"
            )

        badges = [f"<span class='badge'>{_h(sector)}</span>"]
        if is_top:
            badges.append("<span class='badge'>TOP</span>")
        if rel_label is not None:
            badges.append(f"<span class='badge {rel_class}'>Rel {rel_label}</span>")
        if cached:
            badges.append("<span class='badge'>⚡ 캐시</span>")
        if cluster_size_int > 1:
            badges.append(f"<span class='badge'>관련 기사 {cluster_size_int}건</span>")
        topic_badges = (
            "".join(f"<span class='badge'>{_h(t)}</span>" for t in topics)
            or "<span class='badge'>주제 없음</span>"
        )
        related_html = ""
        if cluster_size_int > 1 and related_articles:
            related_items = []
            for related in related_articles[:3]:
                if not isinstance(related, dict):
                    continue
                related_title = str(related.get("title") or "").strip()
                if not related_title:
                    continue
                related_link = str(related.get("link") or "").strip()
                if related_link:
                    related_items.append(
                        f"<li><a href='{_h(related_link)}' target='_blank' rel='noopener noreferrer'>{_h(_truncate(related_title, 70))}</a></li>"
                    )
                else:
                    related_items.append(f"<li>{_h(_truncate(related_title, 70))}</li>")
            if related_items:
                related_html = "<ul class='related' aria-label='관련 기사'>" + "".join(related_items) + "</ul>"

        return (
            f"<article class='card' data-card "
            f"data-sector='{_h(sector)}' data-top={'1' if is_top else '0'} "
            f"data-hay='{_h(hay)}' data-topics='{_h(topic_joined)}' data-ts='{ts}' data-rel='{rel_val}' "
            f"data-cluster='{_h(str(cluster_id or ''))}' data-cluster-size='{cluster_size_int}' "
            f"data-url='{_h(primary)}'>"
            f"  <div class='card-head'>"
            f"    <h3 class='title'>"
            f"      <a href='{_h(primary)}' target='_blank' rel='noopener noreferrer' data-title>{_h(title)}</a>"
            f"    </h3>"
            f"    <button class='clip' type='button' title='저장' data-clip>☆</button>"
            f"  </div>"
            f"  <div class='meta-row'>"
            f"    <span>{_h(pub)}</span>"
            f"    {f'<span>·</span><span>{_h(press)}</span>' if press else ''}"
            f"    <span>·</span>{''.join(badges)}"
            f"  </div>"
            f"  <div class='meta-row'>{topic_badges}</div>"
            f"  <p class='summary' data-summary>{_h(summary)}</p>"
            f"  {related_html}"
            f"  <div class='actions'>{''.join(btns)}</div>"
            f"</article>"
        )

    top_cards = (
        "\n".join(card_html(it, True) for it in top_items)
        if top_items
        else "<div class='note'>해당 기간 Top 이슈가 없습니다.</div>"
    )

    sector_sections: list[str] = []
    for s in ordered_sectors:
        items = sorted(
            by_sector.get(s, []),
            key=lambda x: _ts_dt(getattr(x.article, "pub_date", None)),
            reverse=True,
        )
        if not items:
            continue
        cards = "\n".join(card_html(it, False) for it in items)
        section_note = "고신뢰 기타 기사만 최대 10건 표시" if s == "기타" else "섹터 클릭/검색/정렬 가능"
        sector_sections.append(
            f"<section data-group id='sec-{_h(s)}'>"
            f"  <div class='section-head'>"
            f"    <h2>{_h(s)}<span class='count'>{len(items)}</span></h2>"
            f"    <div class='note'>{_h(section_note)}</div>"
            f"  </div>"
            f"  <div class='grid'>{cards}</div>"
            f"  <div class='load-more-wrap'><button class='btn' type='button' data-load-more data-offset='20'>더보기</button></div>"
            f"</section>"
        )

    if keyword_trends:
        chips = []
        for kw, n in keyword_trends[:20]:
            chips.append(
                f"<span class='kchip'>{_h(kw)} <span class='n'>{n}</span></span>"
            )
        chips_html = "<div class='kchips'>" + "".join(chips) + "</div>"
    else:
        chips_html = "<div class='note'>키워드 데이터 없음</div>"

    html_page = f"""<!doctype html>
<html lang="ko" data-theme="light">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>금융권 일일 언론동향 {date_str}</title>
  <style>{_UI_CSS}</style>
</head>
<body>
  <div class="wrap">
    <div class="mobile-mini">
      <div class="mobile-mini-head">
        <div>
          <h1>금융권 일일 언론동향</h1>
          <div class="meta">{date_str}</div>
        </div>
        <div class="mobile-mini-actions">
          <button id="mobileSearchBtn" class="btn" type="button" aria-label="검색/필터 열기" aria-controls="filterSidebar">🔎</button>
          <button id="mobileFilterBtn" class="btn" type="button" aria-label="필터 열기" aria-controls="filterSidebar" aria-expanded="false">필터</button>
          <button id="mobileTopBtn" class="btn" type="button" aria-label="Top만 토글">Top만</button>
        </div>
      </div>
    </div>

    <div class="layout">
      <aside class="sidebar" id="filterSidebar" aria-label="필터 패널">
        <div class="sheet-backdrop" data-sheet-close></div>
        <div class="filter-shell">
          <div class="header-top">
            <div>
              <h1>금융권 일일 언론동향 <span style="color:var(--muted);">({date_str})</span></h1>
              <div class="meta">대부업권 우선 · 전 금융업권 주요 기사 요약</div>
            </div>
          </div>
          <div class="controls">
            <div class="input" title="제목/요약/업권/주제에서 검색">
              <span style="color:var(--muted); font-size:12px;">🔎</span>
              <input id="searchInput" type="text" placeholder="키워드로 검색 (예: 연체, PF, 국민연금)"/>
            </div>
            <span class="select" title="정렬"><span style="color:var(--muted); font-size:12px;">정렬</span><select id="sortSel"><option value="new" selected>최신순</option><option value="rel">관련도순</option></select></span>
            <label class="toggle"><input id="topOnly" type="checkbox"/> Top만</label>
            <label class="toggle"><input id="favOnly" type="checkbox"/> 저장만</label>
            <button id="themeBtn" class="btn">다크</button>
          </div>
          <div class="nav-wrap" data-scroll-hint><div class="nav">{''.join(pills)}</div><span class="nav-hint left">‹</span><span class="nav-hint right">›</span></div>
          <div class="nav-wrap" data-scroll-hint><div class="nav">{''.join(topic_pills)}</div><span class="nav-hint left">‹</span><span class="nav-hint right">›</span></div>
        </div>
      </aside>

      <div class="main">
        <div id="emptyState" class="note" style="display:none; margin-bottom:12px;">필터 조건에 맞는 기사가 없습니다. 검색어/필터를 조정해 보세요.</div>
        <section data-group id="sec-TOP"><div class="section-head"><h2>오늘의 Top 이슈 10<span class="count">{len(top_items) if top_items else 0}</span></h2><div class="note">전 금융권 주요 기사 중 대부·시장 영향도가 큰 이슈 우선</div></div><div class="grid">{top_cards}</div><div class='load-more-wrap'><button class='btn' type='button' data-load-more data-offset='20'>더보기</button></div></section>
        {''.join(sector_sections)}
        <section data-group id="sec-KW"><div class="section-head"><h2>키워드 트렌드</h2><div class="note">상위 20개</div></div>{chips_html}</section>
        <div class="footer">본 리포트는 Naver News Search API 기반으로 자동 생성되었습니다.</div>
      </div>
    </div>
  </div>
  <script>{_UI_JS}</script>
</body>
</html>
"""
    return html_page


# -----------------------------
# write_report / write_index 유지
# -----------------------------



def write_report(
    report_date: datetime,
    markdown_text: str,
    output_dir: Path,
    html_override: str | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = report_date.strftime("%Y-%m-%d")
    md_path = output_dir / f"{date_str}.md"
    html_path = output_dir / f"{date_str}.html"

    md_path.write_text(markdown_text, encoding="utf-8")

    if html_override:
        html_path.write_text(html_override, encoding="utf-8")
        return {"markdown": md_path, "html": html_path}

    html_content = markdown.markdown(
        markdown_text,
        extensions=["tables"],
        output_format="html5",
    )

    html_page = (
        "<!doctype html>\n"
        "<html lang='ko'>\n"
        "<head>\n"
        "  <meta charset='utf-8'/>\n"
        "  <meta name='viewport' content='width=device-width, initial-scale=1'/>\n"
        f"  <title>금융권 일일 언론동향 {date_str}</title>\n"
        f"  <style>{_LIGHT_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "  <div class='wrap'>\n"
        "    <div class='header'>\n"
        "      <div class='header-top'>\n"
        f"        <div>\n"
        f"          <h1>금융권 일일 언론동향 <span style='color:var(--muted)'>({date_str})</span></h1>\n"
        "          <div class='meta'>대부업권 우선 · 전 금융업권 주요 기사 요약</div>\n"
        "        </div>\n"
        "        <div class='pills'>\n"
        "          <div class='pill'>생성: 자동</div>\n"
        "          <div class='pill'>출처: Naver News Search API</div>\n"
        "        </div>\n"
        "      </div>\n"
        "      <div class='notice'>Tip: 기사 제목을 클릭하면 원문으로 이동합니다. (요약은 170자 내외로 자동 축약)</div>\n"
        "    </div>\n"
        "    <div class='main'>\n"
        f"{html_content}\n"
        "    </div>\n"
        "    <div class='footer'>\n"
        "      본 리포트는 자동 생성 문서입니다. 분류/필터 기준은 향후 학습 데이터에 따라 개선됩니다.\n"
        "    </div>\n"
        "  </div>\n"
        "</body>\n"
        "</html>"
    )

    html_path.write_text(html_page, encoding="utf-8")
    return {"markdown": md_path, "html": html_path}


def write_index(recent_reports: list[Path], output_dir: Path) -> Path:
    index_path = output_dir / "index.html"

    items = []
    for path in recent_reports:
        items.append(
            f"<li style='margin:10px 0;'>"
            f"<a style='font-weight:700;' href='{path.name}'>{path.stem}</a>"
            f" <span style='color:var(--muted); font-size:12px;'>리포트 보기</span>"
            f"</li>"
        )
    links = "\n".join(items) if items else "<li>리포트가 아직 없습니다.</li>"

    html_page = (
        "<!doctype html>\n"
        "<html lang='ko'>\n"
        "<head>\n"
        "  <meta charset='utf-8'/>\n"
        "  <meta name='viewport' content='width=device-width, initial-scale=1'/>\n"
        "  <title>리포트 모아보기</title>\n"
        f"  <style>{_LIGHT_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "  <div class='wrap'>\n"
        "    <div class='header'>\n"
        "      <div class='header-top'>\n"
        "        <div>\n"
        "          <h1>최근 14일 리포트</h1>\n"
        "          <div class='meta'>날짜를 클릭하면 해당 리포트를 열 수 있습니다.</div>\n"
        "        </div>\n"
        "        <div class='pills'>\n"
        "          <div class='pill'><a href='./'>폴더</a></div>\n"
        "        </div>\n"
        "      </div>\n"
        "    </div>\n"
        "    <div class='main'>\n"
        f"      <ul style='list-style: none; padding-left:0; margin:0;'>{links}</ul>\n"
        "    </div>\n"
        "  </div>\n"
        "</body>\n"
        "</html>"
    )

    index_path.write_text(html_page, encoding="utf-8")
    return index_path
