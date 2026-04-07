from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown

from src.pipeline.tagger import TaggedArticle

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


def _top_rank_score(item: TaggedArticle) -> float:
    sector = item.sectors[0] if item.sectors else ""
    topics = set(item.topics or [])
    title = (getattr(item.article, "title", None) or "").lower()
    summary = (getattr(item.article, "description", None) or "").lower()
    text = f"{title} {summary}"

    score = 0.0

    # 업권 중요도
    sector_weights = {
        "대부": 3.0,
        "감독·제재": 1.6,
        "입법·정책": 1.5,
        "저축은행": 1.2,
        "은행": 1.1,
        "거시·시장": 0.9,
    }
    score += sector_weights.get(sector, 1.0)
    if "대부" in text or "사금융" in text:
        score += 1.2

    # 시장/정책/감독 영향
    if sector in {"입법·정책", "감독·제재"}:
        score += 1.8
    impact_keywords = (
        "금리",
        "기준금리",
        "최고금리",
        "불법사금융",
        "불법추심",
        "추심",
        "pf",
        "부동산pf",
        "가계부채",
        "연체",
        "연체율",
        "제재",
        "감독",
        "정책",
    )
    score += sum(0.45 for kw in impact_keywords if kw in text)

    # topic 가중치
    topic_weights = {
        "최고금리": 2.0,
        "불법사금융": 2.0,
        "채권추심": 1.8,
        "금리": 1.5,
        "PF": 1.4,
        "연체": 1.3,
        "가계부채": 1.3,
        "정책": 1.2,
        "감독": 1.2,
    }
    score += sum(w for topic, w in topic_weights.items() if topic in topics)

    def _topic_matches_any(topic_values: set[str], needles: tuple[str, ...]) -> bool:
        return any(any(needle in topic for needle in needles) for topic in topic_values)

    macro_relevance_needles = ("금리", "PF", "연체", "가계부채")

    if "해외·글로벌" in topics and len(topics) == 1:
        score -= 1.2
    if sector == "거시·시장" and not _topic_matches_any(topics, macro_relevance_needles):
        score -= 0.8

    cluster_size = _field(item.article, "cluster_size")
    if isinstance(cluster_size, int) and cluster_size > 1:
        score += min(cluster_size - 1, 4) * 0.35

    relevance = _relevance_value(item.article)
    if isinstance(relevance, float):
        rel_norm = min(relevance / 10.0, 1.0) if relevance > 1.0 else relevance
        score += rel_norm * 2.5

    # 최신성 보조
    score += _ts_dt(getattr(item.article, "pub_date", None)) / 1_000_000_000_000
    return score


def _select_top_items(tagged: list[TaggedArticle], limit: int = 10) -> list[TaggedArticle]:
    excluded_sectors = {"기타"}
    pool = [it for it in tagged if (it.sectors[0] if it.sectors else "기타") not in excluded_sectors]
    ranked = sorted(
        pool,
        key=lambda it: (_top_rank_score(it), _ts_dt(getattr(it.article, "pub_date", None))),
        reverse=True,
    )

    selected: list[TaggedArticle] = []
    seen_clusters: set[str] = set()
    seen_titles: set[str] = set()
    sector_counts: dict[str, int] = defaultdict(int)
    topic_counts: dict[str, int] = defaultdict(int)
    sector_limit = max(3, (limit // 2) + 1)
    topic_limit = max(2, (limit // 3) + 1)

    def norm_title(x: str) -> str:
        return re.sub(r"\s+", " ", (x or "").strip().lower())

    for item in ranked:
        if len(selected) >= limit:
            break
        sector = item.sectors[0] if item.sectors else "기타"
        cluster_id = str(_field(item.article, "cluster_id") or "").strip()
        title_key = norm_title(getattr(item.article, "title", None) or "")
        if not title_key or title_key in seen_titles:
            continue
        if cluster_id and cluster_id in seen_clusters:
            continue
        if sector_counts[sector] >= sector_limit:
            continue
        top_topic = (item.topics or [""])[0]
        if top_topic and topic_counts[top_topic] >= topic_limit:
            continue
        selected.append(item)
        seen_titles.add(title_key)
        if cluster_id:
            seen_clusters.add(cluster_id)
        sector_counts[sector] += 1
        if top_topic:
            topic_counts[top_topic] += 1

    if len(selected) < limit:
        for item in ranked:
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

    by_sector: dict[str, list[TaggedArticle]] = defaultdict(list)
    for item in tagged:
        for sector in item.sectors:
            by_sector[sector].append(item)

    sector_order = [
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
        "기타",
    ]

    ordered_sectors = sector_order + [s for s in by_sector.keys() if s not in sector_order]
    for sector in ordered_sectors:
        if sector not in by_sector:
            continue
        lines.append(f"### {sector}")
        for item in sorted(
            by_sector[sector], key=lambda x: x.article.pub_date, reverse=True
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
# ✅ 제품형 UI용 CSS / JS
# =========================

_UI_CSS = r"""
:root{
  --bg:#f6f8fb; --paper:#ffffff; --text:#0f172a; --muted:#64748b; --border:#e5e7eb;
  --link:#2563eb; --link_hover:#1d4ed8;
  --chip:#eef2ff; --chip_text:#3730a3;
  --shadow:0 10px 30px rgba(17,24,39,0.08);
  --shadow2:0 6px 18px rgba(17,24,39,0.08);
  --ring: 0 0 0 3px rgba(37,99,235,0.15);
}

html[data-theme="dark"]{
  --bg:#0b1220; --paper:#0f172a; --text:#e5e7eb; --muted:#94a3b8; --border:#22304a;
  --link:#60a5fa; --link_hover:#93c5fd;
  --chip:#111c33; --chip_text:#c7d2fe;
  --shadow:0 12px 30px rgba(0,0,0,0.45);
  --shadow2:0 10px 22px rgba(0,0,0,0.40);
  --ring: 0 0 0 3px rgba(96,165,250,0.20);
}

*{ box-sizing:border-box; }
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,"Noto Sans KR",Arial,sans-serif;
  line-height:1.5;
}
body.no-scroll{ overflow:hidden; }

.wrap{ max-width:1600px; margin:0 auto; padding:12px 16px 42px; }
.layout{ display:block; }
.mobile-mini{ display:none; }

.sidebar{ margin-bottom:14px; }
.sheet-backdrop{ display:none; }
.filter-shell{
  background:var(--paper); border:1px solid var(--border);
  border-radius:16px; padding:14px; box-shadow:var(--shadow);
}

.header-top{ display:flex; gap:10px; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; }
h1{ margin:0; font-size:19px; letter-spacing:-0.2px; }
.meta{ color:var(--muted); font-size:13px; margin-top:4px; }

.controls{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.input{
  display:flex; align-items:center; gap:8px;
  background: color-mix(in srgb, var(--paper) 92%, var(--bg));
  border:1px solid var(--border); border-radius:12px;
  padding:8px 10px; min-width:250px;
}
.input input{ border:none; outline:none; background:transparent; color:var(--text); font-size:13px; width:100%; min-width:150px; }
.input:focus-within{ box-shadow:var(--ring); border-color: color-mix(in srgb, var(--link) 55%, var(--border)); }

.btn, .select{
  display:inline-flex; align-items:center; justify-content:center; gap:8px;
  border:1px solid var(--border); background:var(--paper); color:var(--text);
  padding:8px 10px; border-radius:12px; font-size:13px; cursor:pointer;
}
.btn:hover, .select:hover{ border-color: color-mix(in srgb, var(--link) 45%, var(--border)); }
.btn.primary{ background: color-mix(in srgb, var(--link) 12%, var(--paper)); border-color: color-mix(in srgb, var(--link) 30%, var(--border)); }
.btn.active{ background: color-mix(in srgb, var(--link) 14%, var(--paper)); border-color: color-mix(in srgb, var(--link) 35%, var(--border)); color:var(--text); }
.btn.small{ padding:6px 8px; border-radius:10px; font-size:12px; }
.select{ cursor:default; }
.select select{ border:none; outline:none; background:transparent; color:var(--text); font-size:13px; }

.toggle{
  display:inline-flex; align-items:center; gap:8px;
  border:1px solid var(--border); background:var(--paper);
  padding:8px 10px; border-radius:12px; font-size:13px;
}
.toggle input{ transform: translateY(1px); }

.nav{
  margin-top:10px; display:flex; gap:8px;
  flex-wrap:nowrap; overflow-x:auto; overflow-y:hidden;
  padding-bottom:2px;
  -webkit-overflow-scrolling:touch;
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--muted) 45%, transparent) transparent;
}
.nav::-webkit-scrollbar{ height:6px; }
.nav::-webkit-scrollbar-thumb{
  border-radius:999px;
  background: color-mix(in srgb, var(--muted) 45%, transparent);
}
.nav-wrap{ position:relative; }
.nav-wrap .nav-hint{
  position:absolute; top:50%; transform:translateY(-50%);
  width:20px; height:20px; border-radius:999px;
  display:none; align-items:center; justify-content:center;
  color:var(--muted); font-size:12px; font-weight:700;
  border:1px solid var(--border);
  background: color-mix(in srgb, var(--paper) 92%, transparent);
  pointer-events:none;
}
.nav-wrap .nav-hint.left{ left:0; }
.nav-wrap .nav-hint.right{ right:0; }
.nav-wrap.can-scroll-left .nav-hint.left,
.nav-wrap.can-scroll-right .nav-hint.right{ display:inline-flex; }
.pill, .kchip{ white-space:nowrap; }

.pill{
  display:inline-flex; align-items:center; gap:6px;
  padding:7px 10px; border-radius:999px;
  border:1px solid var(--border); background: color-mix(in srgb, var(--paper) 92%, var(--bg));
  color:var(--muted); font-size:12px; cursor:pointer;
}
.pill strong{ color:var(--text); font-weight:700; }
.pill.active{
  background: color-mix(in srgb, var(--link) 14%, var(--paper));
  border-color: color-mix(in srgb, var(--link) 35%, var(--border));
  color:var(--text);
}

.main{
  background:var(--paper); border:1px solid var(--border);
  border-radius:16px; padding:14px; box-shadow:var(--shadow);
}

.section-head{
  display:flex; align-items:flex-end; justify-content:space-between; gap:12px; flex-wrap:wrap;
  margin: 6px 0 10px; padding-top: 12px; border-top: 1px solid var(--border);
}
.section-head:first-child{ border-top:none; padding-top:0; }
h2{ margin:0; font-size:15px; }
.count{ color:var(--muted); font-size:12px; margin-left:6px; }
.note{ color:var(--muted); font-size:12px; }

.grid{ display:grid; grid-template-columns:1fr; gap:8px; }
.card{
  border:1px solid var(--border); border-radius:12px;
  padding:10px; background: color-mix(in srgb, var(--paper) 96%, var(--bg));
  box-shadow: var(--shadow2); transition: transform .08s ease, border-color .08s ease;
}
.card:hover{ transform: translateY(-1px); border-color: color-mix(in srgb, var(--link) 35%, var(--border)); }
.card-head{ display:flex; align-items:flex-start; justify-content:space-between; gap:8px; }
.clip{ border:1px solid var(--border); background:var(--paper); color:var(--muted); border-radius:12px; padding:6px 8px; cursor:pointer; line-height:1; font-size:14px; }
.clip:hover{ border-color: color-mix(in srgb, var(--link) 45%, var(--border)); }
.clip.on{ color: var(--link_hover); background: color-mix(in srgb, var(--link) 10%, var(--paper)); border-color: color-mix(in srgb, var(--link) 35%, var(--border)); }

.title{ margin:0 0 4px; font-size:14px; font-weight:800; line-height:1.3; }
.title a{ color:var(--text); text-decoration:none; }
.title a:hover{ color:var(--link_hover); text-decoration:underline; }
.meta-row{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; color:var(--muted); font-size:12px; margin-bottom:7px; }
.badge{ display:inline-flex; align-items:center; padding:3px 8px; border-radius:999px; border:1px solid var(--border); background: var(--chip); color: var(--chip_text); font-size:11px; }
.badge.r-high{ font-weight:700; }
.badge.r-med{ opacity:0.95; }
.badge.r-low{ opacity:0.85; }

.summary{
  margin:0; color: color-mix(in srgb, var(--text) 82%, var(--muted)); font-size:12.5px;
  display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden;
}
.actions{ display:flex; gap:8px; flex-wrap:wrap; margin-top:9px; }
.load-more-wrap{ margin:8px 0 2px; }
mark{ background: color-mix(in srgb, var(--link) 18%, transparent); color: inherit; border-radius: 6px; padding: 0 3px; }
.footer{ margin-top:12px; color:var(--muted); font-size:12px; text-align:center; }
.kchips{ display:flex; gap:8px; flex-wrap:wrap; margin-top:6px; }
.kchip{ display:inline-flex; align-items:center; gap:6px; padding:6px 10px; border-radius:999px; border:1px solid var(--border); background: color-mix(in srgb, var(--paper) 92%, var(--bg)); color:var(--text); font-size:12px; }
.kchip .n{ color:var(--muted); font-size:12px; }

@media (min-width:768px){
  .grid{ grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }
}

@media (min-width:1200px){
  .wrap{ max-width:1720px; padding:14px 20px 52px; }
  .layout{ display:grid; grid-template-columns:minmax(320px, 360px) minmax(0, 1fr); gap:16px; align-items:start; }
  .sidebar{ margin-bottom:0; }
  .filter-shell{ position:sticky; top:12px; max-height:calc(100vh - 24px); overflow:auto; }
  .main{ padding:16px; }
  .grid{ grid-template-columns:repeat(auto-fit, minmax(250px, 1fr)); }
}

@media (max-width:1199px){
  .header-top{ gap:8px; }
  .input{ min-width:200px; }
}

@media (max-width:767px){
  .wrap{ padding:74px 10px 24px; }
  .mobile-mini{
    display:block; position:fixed; top:0; left:0; right:0; z-index:95;
    background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(10px);
    border-bottom:1px solid var(--border); padding:8px 10px;
  }
  .mobile-mini-head{ display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .mobile-mini h1{ font-size:14px; }
  .mobile-mini .meta{ margin-top:1px; font-size:11px; }
  .mobile-mini-actions{ display:flex; gap:6px; }
  .mobile-mini .btn, .mobile-mini .toggle{ padding:6px 8px; font-size:12px; border-radius:10px; }

  .sidebar{ position:fixed; inset:0; z-index:100; pointer-events:none; }
  .sheet-backdrop{ display:block; position:absolute; inset:0; background:rgba(0,0,0,0.35); opacity:0; transition:opacity .18s ease; }
  .filter-shell{
    position:absolute; left:0; right:0; bottom:0; margin:0;
    border-radius:16px 16px 0 0; max-height:78vh; overflow:auto;
    transform:translateY(105%); transition:transform .2s ease;
  }
  .sidebar.open{ pointer-events:auto; }
  .sidebar.open .sheet-backdrop{ opacity:1; }
  .sidebar.open .filter-shell{ transform:translateY(0); }

  .header-top .meta{ display:none; }
  .controls{ gap:6px; }
  .controls .input{ min-width:100%; }
  .controls .toggle, .controls .btn, .controls .select{ font-size:12px; padding:7px 8px; }

  .main{ padding:10px; }
  .section-head{ margin:4px 0 8px; padding-top:10px; }
  .card{ padding:8px; border-radius:10px; }
  .summary{ -webkit-line-clamp:2; font-size:12px; }
  .meta-row{ margin-bottom:5px; gap:5px; }
  .actions{ margin-top:7px; gap:6px; }
}
"""

_UI_JS = r"""
(function(){
  const root = document.documentElement;
  const body = document.body;
  const themeBtn = document.getElementById("themeBtn");
  const search = document.getElementById("searchInput");
  const topOnly = document.getElementById("topOnly");
  const favOnly = document.getElementById("favOnly");
  const sortSel = document.getElementById("sortSel");
  const emptyState = document.getElementById("emptyState");
  const sidebar = document.getElementById("filterSidebar");
  const mobileFilterBtn = document.getElementById("mobileFilterBtn");
  const mobileSearchBtn = document.getElementById("mobileSearchBtn");
  const mobileTopBtn = document.getElementById("mobileTopBtn");
  const navElements = Array.from(document.querySelectorAll(".nav-wrap[data-scroll-hint] .nav"));

  const pills = Array.from(document.querySelectorAll("[data-sector-pill]"));
  const topicPills = Array.from(document.querySelectorAll("[data-topic-pill]"));
  const cards = Array.from(document.querySelectorAll("[data-card]"));
  const groups = Array.from(document.querySelectorAll("[data-group]"));
  const PAGE_SIZE = 20;
  const SEARCH_DEBOUNCE_MS = 150;

  const LS_THEME = "reportTheme";
  const LS_FAVS = "reportFavs_v1";
  let activeSector = "ALL";
  let activeTopic = "ALL";

  const groupMetaMap = new Map();
  const groupMetas = groups.map(groupEl => {
    const meta = {
      el: groupEl,
      grid: groupEl.querySelector(".grid"),
      loadMoreBtn: groupEl.querySelector("[data-load-more]"),
      cards: [],
      orderedCards: [],
      visibleLimit: PAGE_SIZE,
      lastOrder: []
    };
    groupMetaMap.set(groupEl, meta);
    return meta;
  });

  const cardMetas = cards.map((cardEl, idx) => {
    const groupMeta = groupMetaMap.get(cardEl.closest("[data-group]"));
    const meta = {
      id: idx,
      el: cardEl,
      group: groupMeta,
      hay: (cardEl.dataset.hay || "").toLowerCase(),
      sector: cardEl.dataset.sector || "",
      topics: (cardEl.dataset.topics || "").split("|").filter(Boolean),
      ts: parseFloat(cardEl.dataset.ts || "0"),
      rel: parseFloat(cardEl.dataset.rel || "0"),
      isTop: cardEl.dataset.top === "1",
      url: cardEl.dataset.url || "",
      isMatch: cardEl.dataset.match === "1",
      isShown: cardEl.style.display !== "none"
    };
    if(groupMeta) groupMeta.cards.push(meta);
    return meta;
  });
  groupMetas.forEach(g => { g.orderedCards = g.cards.slice(); g.lastOrder = g.cards.slice(); });

  let renderRafId = 0;
  let pendingRender = { recomputeMatch: true, recomputeSort: true, resetPagination: true };

  function loadTheme(){ const saved = localStorage.getItem(LS_THEME); root.dataset.theme = (saved === "dark" || saved === "light") ? saved : "light"; if(themeBtn) themeBtn.textContent = (root.dataset.theme === "dark") ? "라이트" : "다크"; }
  function toggleTheme(){ const next = (root.dataset.theme === "dark") ? "light" : "dark"; root.dataset.theme = next; localStorage.setItem(LS_THEME, next); if(themeBtn) themeBtn.textContent = (next === "dark") ? "라이트" : "다크"; }
  function getFavs(){ try{ const raw = localStorage.getItem(LS_FAVS); const arr = raw ? JSON.parse(raw) : []; return new Set(Array.isArray(arr) ? arr : []);}catch(e){ return new Set(); }}
  function saveFavs(set){ localStorage.setItem(LS_FAVS, JSON.stringify(Array.from(set))); }
  function setActivePill(sector){
    const resolvedSector = pills.some(p => p.dataset.sector === sector) ? sector : "ALL";
    activeSector = resolvedSector;
    pills.forEach(p => p.classList.toggle("active", p.dataset.sector === resolvedSector));
  }
  function setActiveTopicPill(topic){
    const resolvedTopic = topicPills.some(p => p.dataset.topic === topic) ? topic : "ALL";
    activeTopic = resolvedTopic;
    topicPills.forEach(p => p.classList.toggle("active", p.dataset.topic === resolvedTopic));
  }
  function debounce(fn, waitMs){ let timer = 0; return (...args) => { clearTimeout(timer); timer = window.setTimeout(() => fn(...args), waitMs); }; }
  function setFilterSheetOpen(nextOpen, options){
    const focusSearch = !!options?.focusSearch;
    const isMobile = window.innerWidth < 768;
    const open = !!nextOpen && isMobile;
    if(sidebar){
      sidebar.classList.toggle("open", open);
      sidebar.setAttribute("aria-hidden", open ? "false" : "true");
    }
    body.classList.toggle("no-scroll", open);
    if(mobileFilterBtn){
      mobileFilterBtn.setAttribute("aria-expanded", open ? "true" : "false");
      mobileFilterBtn.setAttribute("aria-label", open ? "필터 닫기" : "필터 열기");
      mobileFilterBtn.textContent = "필터";
    }
    if(open && focusSearch) setTimeout(() => search?.focus(), 120);
  }

  function updateNavScrollHints(){
    navElements.forEach(nav => {
      const wrap = nav.closest(".nav-wrap");
      if(!wrap) return;
      const maxLeft = Math.max(0, nav.scrollWidth - nav.clientWidth);
      const canScroll = maxLeft > 2;
      wrap.classList.toggle("can-scroll-left", canScroll && nav.scrollLeft > 2);
      wrap.classList.toggle("can-scroll-right", canScroll && nav.scrollLeft < maxLeft - 2);
    });
  }

  function sortCards(mode, cardsToSort){
    return cardsToSort.slice().sort((a,b) => {
      if(mode === "rel"){
        if(b.rel !== a.rel) return b.rel - a.rel;
        return b.ts - a.ts;
      }
      return b.ts - a.ts;
    });
  }

  function computeFilterState(){
    const q = (search?.value || "").trim().toLowerCase();
    const qTokens = q ? q.split(/\s+/).filter(Boolean) : [];
    const onlyTop = !!(topOnly && topOnly.checked);
    const onlyFav = !!(favOnly && favOnly.checked);
    const favs = getFavs();
    let totalMatched = 0;

    cardMetas.forEach(meta => {
      const isFav = favs.has(meta.url);
      let ok = true;
      if(activeSector !== "ALL" && meta.sector !== activeSector) ok = false;
      if(activeTopic !== "ALL" && !meta.topics.includes(activeTopic)) ok = false;
      if(onlyTop && !meta.isTop) ok = false;
      if(onlyFav && !isFav) ok = false;
      if(qTokens.length && !qTokens.some(tok => meta.hay.includes(tok))) ok = false;
      meta.isMatch = ok;
      if(ok) totalMatched += 1;
    });
    return totalMatched;
  }

  function applyDomState(totalMatched){
    groupMetas.forEach(g => {
      const matchedOrdered = g.orderedCards.filter(meta => meta.isMatch);
      const matchedCount = matchedOrdered.length;
      if(g.visibleLimit < PAGE_SIZE) g.visibleLimit = PAGE_SIZE;
      const visibleLimit = Math.min(g.visibleLimit, matchedCount);

      let shownMatched = 0;
      g.orderedCards.forEach(meta => {
        const shouldShow = meta.isMatch && shownMatched < visibleLimit;
        if(meta.isMatch) shownMatched += 1;
        if((meta.el.dataset.match === "1") !== meta.isMatch) meta.el.dataset.match = meta.isMatch ? "1" : "0";
        if(meta.isShown !== shouldShow){
          meta.el.style.display = shouldShow ? "" : "none";
          meta.isShown = shouldShow;
        }
      });

      if(g.loadMoreBtn){
        const nextOffset = Math.min(visibleLimit, matchedCount);
        if(g.loadMoreBtn.dataset.offset !== String(nextOffset)) g.loadMoreBtn.dataset.offset = String(nextOffset);
        const showLoadMore = visibleLimit < matchedCount;
        if((g.loadMoreBtn.style.display !== "none") !== showLoadMore) g.loadMoreBtn.style.display = showLoadMore ? "" : "none";
      }
      const showGroup = matchedCount > 0;
      if((g.el.style.display !== "none") !== showGroup) g.el.style.display = showGroup ? "" : "none";
    });
    if (emptyState) emptyState.style.display = totalMatched > 0 ? "none" : "";
  }

  function runRender(){
    renderRafId = 0;
    const task = pendingRender;
    pendingRender = { recomputeMatch: false, recomputeSort: false, resetPagination: false };
    const mode = (sortSel?.value || "new");

    groupMetas.forEach(g => {
      if(task.recomputeSort){
        const nextOrder = sortCards(mode, g.cards);
        const sameOrder = nextOrder.length === g.lastOrder.length && nextOrder.every((meta, idx) => meta === g.lastOrder[idx]);
        g.orderedCards = nextOrder;
        if(!sameOrder && g.grid){
          nextOrder.forEach(meta => g.grid.appendChild(meta.el));
          g.lastOrder = nextOrder.slice();
        }
      }
      if(task.resetPagination) g.visibleLimit = PAGE_SIZE;
    });

    let totalMatched = cardMetas.reduce((acc, meta) => acc + (meta.isMatch ? 1 : 0), 0);
    if(task.recomputeMatch) totalMatched = computeFilterState();
    applyDomState(totalMatched);
  }

  function scheduleRender(nextTask){
    pendingRender = {
      recomputeMatch: pendingRender.recomputeMatch || !!nextTask?.recomputeMatch,
      recomputeSort: pendingRender.recomputeSort || !!nextTask?.recomputeSort,
      resetPagination: pendingRender.resetPagination || !!nextTask?.resetPagination,
    };
    if(renderRafId) return;
    renderRafId = window.requestAnimationFrame(runRender);
  }

  function applySort(){
    scheduleRender({ recomputeSort: true, resetPagination: true });
  }

  function applyFilter(){
    scheduleRender({ recomputeMatch: true, resetPagination: true });
  }

  function initFavButtons(){
    const favs = getFavs();
    cards.forEach(card => {
      const btn = card.querySelector("[data-clip]"); const url = card.dataset.url || ""; if(!btn || !url) return;
      const on = favs.has(url); btn.classList.toggle("on", on); btn.textContent = on ? "★" : "☆";
      btn.addEventListener("click", () => { const set = getFavs(); const nowOn = set.has(url) ? (set.delete(url), false) : (set.add(url), true); saveFavs(set); btn.classList.toggle("on", nowOn); btn.textContent = nowOn ? "★" : "☆"; if(favOnly && favOnly.checked) applyFilter(); });
    });
  }

  function bindEvents(){
    pills.forEach(p => p.addEventListener("click", () => { setActivePill(p.dataset.sector); applyFilter(); if(window.innerWidth < 768) setFilterSheetOpen(false); }));
    topicPills.forEach(p => p.addEventListener("click", () => { setActiveTopicPill(p.dataset.topic); applyFilter(); if(window.innerWidth < 768) setFilterSheetOpen(false); }));
    groupMetas.forEach(g => { const btn = g.loadMoreBtn; if(!btn) return; btn.addEventListener("click", ()=>{ g.visibleLimit += PAGE_SIZE; scheduleRender({ resetPagination: false }); }); });
    const debouncedSearch = debounce(()=>{ applyFilter(); }, SEARCH_DEBOUNCE_MS);
    search?.addEventListener("input", debouncedSearch);
    if(topOnly) topOnly.addEventListener("change", applyFilter);
    if(favOnly) favOnly.addEventListener("change", applyFilter);
    sortSel?.addEventListener("change", ()=>{ applySort(); applyFilter(); });
    themeBtn?.addEventListener("click", toggleTheme);

    mobileFilterBtn?.addEventListener("click", () => setFilterSheetOpen(!sidebar?.classList.contains("open")));
    mobileSearchBtn?.addEventListener("click", () => setFilterSheetOpen(true, { focusSearch: true }));
    sidebar?.addEventListener("click", (e)=>{ if(e.target.closest("[data-sheet-close]")) setFilterSheetOpen(false); });
    document.addEventListener("keydown", (e)=>{ if(e.key === "Escape") setFilterSheetOpen(false); });
    mobileTopBtn?.addEventListener("click", ()=>{ if(!topOnly) return; topOnly.checked = !topOnly.checked; mobileTopBtn.classList.toggle("active", topOnly.checked); applyFilter(); });
    topOnly?.addEventListener("change", ()=> mobileTopBtn?.classList.toggle("active", topOnly.checked));
    navElements.forEach(nav => nav.addEventListener("scroll", updateNavScrollHints, { passive: true }));
    window.addEventListener("resize", ()=>{ setFilterSheetOpen(false); updateNavScrollHints(); });
  }

  loadTheme();
  setActivePill("ALL");
  setActiveTopicPill("ALL");
  applySort();
  initFavButtons();
  bindEvents();
  applyFilter();
  setFilterSheetOpen(false);
  updateNavScrollHints();
  mobileTopBtn?.classList.toggle("active", !!topOnly?.checked);
})();
"""


def render_html(
    report_date: datetime,
    tagged: list[TaggedArticle],
    keyword_trends: list[tuple[str, int]],
) -> str:
    date_str = report_date.strftime("%Y-%m-%d")

    top_items = _select_top_items(tagged, limit=10)

    by_sector: dict[str, list[TaggedArticle]] = defaultdict(list)
    for item in tagged:
        sector = item.sectors[0] if item.sectors else "기타"
        by_sector[sector].append(item)

    sector_order = [
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
        "기타",
    ]
    ordered_sectors = sector_order + [s for s in by_sector.keys() if s not in sector_order]
    sector_counts = {s: len(by_sector.get(s, [])) for s in ordered_sectors}
    if sum(sector_counts.values()) != len(tagged):
        raise ValueError(
            "Sector counts sanity check failed: sum(sector_counts) != total tagged"
        )

    def pill_html(sector: str, count: int) -> str:
        return f"<button class='pill' data-sector-pill data-sector='{_h(sector)}'><strong>{_h(sector)}</strong><span class='count'>{count}</span></button>"

    pills = [
        "<button class='pill active' data-sector-pill data-sector='ALL'><strong>전체</strong><span class='count'>{}</span></button>".format(
            len(tagged)
        )
    ]
    for s in ordered_sectors:
        count = sector_counts[s]
        if count <= 0:
            continue
        pills.append(pill_html(s, count))

    topic_counts: dict[str, int] = defaultdict(int)
    for item in tagged:
        for topic in item.topics or []:
            topic_counts[topic] += 1
    topic_pills = [
        "<button class='pill active' data-topic-pill data-topic='ALL'><strong>전체 주제</strong></button>"
    ]
    for topic, count in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        topic_pills.append(
            f"<button class='pill' data-topic-pill data-topic='{_h(topic)}'><strong>{_h(topic)}</strong><span class='count'>{count}</span></button>"
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
        topic_badges = (
            "".join(f"<span class='badge'>{_h(t)}</span>" for t in topics)
            or "<span class='badge'>주제 없음</span>"
        )

        return (
            f"<article class='card' data-card "
            f"data-sector='{_h(sector)}' data-top={'1' if is_top else '0'} "
            f"data-hay='{_h(hay)}' data-topics='{_h(topic_joined)}' data-ts='{ts}' data-rel='{rel_val}' "
            f"data-cluster='{_h(str(cluster_id or ''))}' data-cluster-size='{int(cluster_size or 1)}' "
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
            by_sector.get(s, []), key=lambda x: x.article.pub_date, reverse=True
        )
        if not items:
            continue
        cards = "\n".join(card_html(it, False) for it in items)
        sector_sections.append(
            f"<section data-group id='sec-{_h(s)}'>"
            f"  <div class='section-head'>"
            f"    <h2>{_h(s)}<span class='count'>{len(by_sector.get(s, []))}</span></h2>"
            f"    <div class='note'>섹터 클릭/검색/정렬 가능</div>"
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

_LIGHT_CSS = """
:root{
  --bg: #f6f8fb;
  --paper: #ffffff;
  --text: #111827;
  --muted: #6b7280;
  --border: #e5e7eb;
  --link: #2563eb;
  --link_hover: #1d4ed8;
  --chip: #eef2ff;
  --chip_text: #3730a3;
  --shadow: 0 10px 30px rgba(17,24,39,0.08);
}
*{ box-sizing: border-box; }
html, body { height: 100%; }
body{
  margin:0;
  background: var(--bg);
  color: var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
  line-height: 1.6;
}
.wrap{
  max-width: 1040px;
  margin: 0 auto;
  padding: 26px 16px 60px;
}
.header{
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 18px;
  box-shadow: var(--shadow);
}
.header-top{
  display:flex;
  gap:12px;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
}
h1{
  margin:0;
  font-size: 22px;
  letter-spacing: -0.2px;
}
.meta{
  color: var(--muted);
  font-size: 13px;
  margin-top: 6px;
}
.pills{
  display:flex;
  gap:8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.pill{
  display:inline-flex;
  align-items:center;
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: #fafafa;
  color: var(--muted);
  font-size: 12px;
}
.main{
  margin-top: 14px;
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 18px;
  box-shadow: var(--shadow);
}
a{
  color: var(--link);
  text-decoration: none;
}
a:hover{ color: var(--link_hover); text-decoration: underline; }
h2{
  margin: 18px 0 10px;
  padding-top: 6px;
  font-size: 16px;
  border-top: 1px solid var(--border);
}
h3{
  margin: 14px 0 8px;
  font-size: 14px;
  color: #0f172a;
}
ul{
  margin: 8px 0 14px 0;
  padding-left: 18px;
}
li{
  margin: 8px 0;
}
hr{
  border: none;
  border-top: 1px solid var(--border);
  margin: 18px 0;
}
.footer{
  margin-top: 14px;
  color: var(--muted);
  font-size: 12px;
}
.notice{
  background: var(--chip);
  color: var(--chip_text);
  border: 1px solid #dbeafe;
  border-radius: 12px;
  padding: 10px 12px;
  font-size: 12px;
  margin-top: 10px;
}
"""


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
