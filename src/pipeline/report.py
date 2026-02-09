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


def _relevance_value(article: Any) -> float | None:
    """
    프로젝트마다 필드명이 다를 수 있어, 존재하면 최대한 끌어오도록.
    - relevance_prob / prob / relevance
    - relevance_score / score
    """
    for k in ("relevance_prob", "prob", "relevance"):
        v = getattr(article, k, None)
        if isinstance(v, (int, float)):
            return float(v)
    for k in ("relevance_score", "score"):
        v = getattr(article, k, None)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _relevance_label(v: float) -> tuple[str, str]:
    # (label, css_class)
    if v >= 0.75:
        return ("High", "r-high")
    if v >= 0.60:
        return ("Med", "r-med")
    return ("Low", "r-low")


def render_markdown(
    report_date: datetime,
    tagged: list[TaggedArticle],
    keyword_trends: list[tuple[str, int]],
) -> str:
    header = f"# 금융권 일일 언론동향 ({report_date.strftime('%Y-%m-%d')})"
    lines = [header, ""]

    top_items = sorted(
        [it for it in tagged if "감독입법" not in it.sectors and "기타" not in it.sectors],
        key=lambda x: x.article.pub_date,
        reverse=True,
    )[:10]

    lines.append("## 오늘의 Top 이슈 10")
    if top_items:
        for item in top_items:
            a = item.article
            lines.append(f"- {md_link(a.title or '', _primary_link(a))} — {md_escape(_truncate(a.description, 180))}")
    else:
        lines.append("- 해당 기간 기사 없음")

    lines.append("")
    lines.append("## 업권별 주요 기사")

    by_sector: dict[str, list[TaggedArticle]] = defaultdict(list)
    for item in tagged:
        for sector in item.sectors:
            by_sector[sector].append(item)

    sector_order = [
        "대부", "은행", "보험", "증권", "카드", "캐피탈",
        "저축은행", "핀테크", "감독입법", "기타",
    ]

    for sector in sector_order:
        if sector not in by_sector:
            continue
        lines.append(f"### {sector}")
        for item in sorted(by_sector[sector], key=lambda x: x.article.pub_date, reverse=True)[:10]:
            a = item.article
            lines.append(f"- {md_link(a.title or '', _primary_link(a))} — {md_escape(_truncate(a.description, 170))}")
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

_UI_CSS = """
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
  line-height:1.55;
}

.wrap{ max-width:1120px; margin:0 auto; padding:18px 16px 60px; }

.topbar{
  position:sticky; top:0; z-index:50;
  background: color-mix(in srgb, var(--bg) 70%, transparent);
  backdrop-filter: blur(10px);
  padding: 10px 0 12px;
}

.header{
  background:var(--paper); border:1px solid var(--border);
  border-radius:16px; padding:16px 16px; box-shadow:var(--shadow);
}

.header-top{
  display:flex; gap:14px; justify-content:space-between; align-items:flex-start; flex-wrap:wrap;
}

h1{ margin:0; font-size:20px; letter-spacing:-0.2px; }
.meta{ color:var(--muted); font-size:13px; margin-top:6px; }

.controls{
  display:flex; gap:10px; flex-wrap:wrap; align-items:center; justify-content:flex-end;
}

.input{
  display:flex; align-items:center; gap:8px;
  background: color-mix(in srgb, var(--paper) 92%, var(--bg));
  border:1px solid var(--border); border-radius:12px;
  padding:8px 10px; min-width:260px;
}
.input input{
  border:none; outline:none; background:transparent; color:var(--text);
  font-size:13px; width:260px;
}
.input:focus-within{ box-shadow:var(--ring); border-color: color-mix(in srgb, var(--link) 55%, var(--border)); }

.btn, .select{
  display:inline-flex; align-items:center; justify-content:center; gap:8px;
  border:1px solid var(--border); background:var(--paper); color:var(--text);
  padding:8px 10px; border-radius:12px; font-size:13px; cursor:pointer;
}
.btn:hover, .select:hover{ border-color: color-mix(in srgb, var(--link) 45%, var(--border)); }
.btn.primary{ background: color-mix(in srgb, var(--link) 12%, var(--paper)); border-color: color-mix(in srgb, var(--link) 30%, var(--border)); }
.btn.small{ padding:6px 8px; border-radius:10px; font-size:12px; }
.select{ cursor:default; }
.select select{
  border:none; outline:none; background:transparent; color:var(--text);
  font-size:13px;
}

.toggle{
  display:inline-flex; align-items:center; gap:8px;
  border:1px solid var(--border); background:var(--paper);
  padding:8px 10px; border-radius:12px; font-size:13px;
}
.toggle input{ transform: translateY(1px); }

.nav{
  margin-top:10px;
  display:flex; gap:8px; flex-wrap:wrap;
}
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
  margin-top:14px;
  background:var(--paper); border:1px solid var(--border);
  border-radius:16px; padding:16px 16px; box-shadow:var(--shadow);
}

.section-head{
  display:flex; align-items:flex-end; justify-content:space-between; gap:12px; flex-wrap:wrap;
  margin: 6px 0 10px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.section-head:first-child{ border-top:none; padding-top:0; }
h2{ margin:0; font-size:15px; }
.count{ color:var(--muted); font-size:12px; margin-left:6px; }
.note{ color:var(--muted); font-size:12px; }

.grid{
  display:grid;
  grid-template-columns: repeat(12, 1fr);
  gap:10px;
}

.card{
  grid-column: span 12;
  border:1px solid var(--border);
  border-radius:14px;
  padding:12px 12px;
  background: color-mix(in srgb, var(--paper) 96%, var(--bg));
  box-shadow: var(--shadow2);
  transition: transform .08s ease, border-color .08s ease;
}
@media (min-width: 860px){
  .card{ grid-column: span 6; }
}
.card:hover{
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--link) 35%, var(--border));
}

.title{
  margin:0 0 6px; font-size:14px; font-weight:800; line-height:1.35;
}
.title a{ color:var(--text); text-decoration:none; }
.title a:hover{ color:var(--link_hover); text-decoration:underline; }

.meta-row{
  display:flex; flex-wrap:wrap; gap:8px; align-items:center;
  color:var(--muted); font-size:12px; margin-bottom:8px;
}
.badge{
  display:inline-flex; align-items:center;
  padding:4px 8px; border-radius:999px;
  border:1px solid var(--border);
  background: var(--chip);
  color: var(--chip_text);
  font-size:11px;
}
.badge.r-high{ font-weight:700; }
.badge.r-med{ opacity:0.95; }
.badge.r-low{ opacity:0.85; }

.summary{
  margin:0;
  color: color-mix(in srgb, var(--text) 82%, var(--muted));
  font-size:13px;
  display:-webkit-box;
  -webkit-line-clamp:3;
  -webkit-box-orient:vertical;
  overflow:hidden;
}

.actions{
  display:flex; gap:8px; flex-wrap:wrap;
  margin-top:10px;
}

mark{
  background: color-mix(in srgb, var(--link) 18%, transparent);
  color: inherit;
  border-radius: 6px;
  padding: 0 3px;
}

.footer{
  margin-top:12px; color:var(--muted); font-size:12px;
  text-align:center;
}

.kchips{ display:flex; gap:8px; flex-wrap:wrap; margin-top:6px; }
.kchip{
  display:inline-flex; align-items:center; gap:6px;
  padding:6px 10px; border-radius:999px;
  border:1px solid var(--border);
  background: color-mix(in srgb, var(--paper) 92%, var(--bg));
  color:var(--text);
  font-size:12px;
}
.kchip .n{ color:var(--muted); font-size:12px; }
"""

.card-head{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:10px;
}

.clip{
  border:1px solid var(--border);
  background:var(--paper);
  color:var(--muted);
  border-radius:12px;
  padding:6px 8px;
  cursor:pointer;
  line-height:1;
  font-size:14px;
}
.clip:hover{ border-color: color-mix(in srgb, var(--link) 45%, var(--border)); }
.clip.on{
  color: var(--link_hover);
  background: color-mix(in srgb, var(--link) 10%, var(--paper));
  border-color: color-mix(in srgb, var(--link) 35%, var(--border));
}

.presetbar{
  margin-top:10px;
  display:flex;
  gap:8px;
  flex-wrap:wrap;
}
.preset{
  display:inline-flex; align-items:center;
  padding:7px 10px; border-radius:999px;
  border:1px solid var(--border);
  background: color-mix(in srgb, var(--paper) 92%, var(--bg));
  color:var(--text);
  font-size:12px;
  cursor:pointer;
}
.preset.active{
  background: color-mix(in srgb, var(--link) 14%, var(--paper));
  border-color: color-mix(in srgb, var(--link) 35%, var(--border));
}


(function(){
  const root = document.documentElement;
  const themeBtn = document.getElementById("themeBtn");
  const search = document.getElementById("searchInput");
  const topOnly = document.getElementById("topOnly");
  const favOnly = document.getElementById("favOnly");
  const sortSel = document.getElementById("sortSel");
  const presetBar = document.getElementById("presetBar");

  const pills = Array.from(document.querySelectorAll("[data-sector-pill]"));
  const cards = Array.from(document.querySelectorAll("[data-card]"));
  const groups = Array.from(document.querySelectorAll("[data-group]"));

  const LS_THEME = "reportTheme";
  const LS_FAVS = "reportFavs_v1";
  const LS_PRESETS = "reportPreset_v1";

  const PRESETS = [
    {k:"PF", q:"PF"},
    {k:"연체", q:"연체 연체율"},
    {k:"금리", q:"금리 기준금리"},
    {k:"가계대출", q:"가계대출 주담대"},
    {k:"부동산", q:"부동산 분양 미분양"},
    {k:"IPO", q:"IPO 상장"},
    {k:"국민연금", q:"국민연금"},
  ];

  function loadTheme(){
    const saved = localStorage.getItem(LS_THEME);
    root.dataset.theme = (saved === "dark" || saved === "light") ? saved : "light";
    themeBtn.textContent = (root.dataset.theme === "dark") ? "라이트" : "다크";
  }
  function toggleTheme(){
    const next = (root.dataset.theme === "dark") ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem(LS_THEME, next);
    themeBtn.textContent = (next === "dark") ? "라이트" : "다크";
  }

  function getFavs(){
    try{
      const raw = localStorage.getItem(LS_FAVS);
      const arr = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(arr) ? arr : []);
    }catch(e){
      return new Set();
    }
  }
  function saveFavs(set){
    localStorage.setItem(LS_FAVS, JSON.stringify(Array.from(set)));
  }

  function setActivePill(sector){
    pills.forEach(p => p.classList.toggle("active", p.dataset.sector === sector));
  }

  function escapeRegExp(s){ return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function escapeHtml(s){
    return (s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function buildHighlight(text, tokens){
    if(!tokens.length) return escapeHtml(text);
    const re = new RegExp(tokens.map(escapeRegExp).join("|"), "ig");
    let out = "";
    let last = 0;
    for (const m of text.matchAll(re)) {
      const idx = m.index ?? 0;
      out += escapeHtml(text.slice(last, idx));
      out += "<mark>" + escapeHtml(m[0]) + "</mark>";
      last = idx + m[0].length;
    }
    out += escapeHtml(text.slice(last));
    return out;
  }

  function cacheOriginalText(){
    cards.forEach(card => {
      const titleEl = card.querySelector("[data-title]");
      const sumEl = card.querySelector("[data-summary]");
      if(titleEl && !card.dataset.ot) card.dataset.ot = titleEl.textContent || "";
      if(sumEl && !card.dataset.os) card.dataset.os = sumEl.textContent || "";
    });
  }

  function applyHighlight(){
    const q = (search.value || "").trim();
    const tokens = q.split(/\s+/).map(t => t.trim()).filter(Boolean);
    cards.forEach(card => {
      const titleEl = card.querySelector("[data-title]");
      const sumEl = card.querySelector("[data-summary]");
      if(!titleEl || !sumEl) return;

      const ot = card.dataset.ot || titleEl.textContent || "";
      const os = card.dataset.os || sumEl.textContent || "";

      if(card.style.display === "none"){
        titleEl.textContent = ot;
        sumEl.textContent = os;
        return;
      }

      if(!tokens.length){
        titleEl.textContent = ot;
        sumEl.textContent = os;
      }else{
        titleEl.innerHTML = buildHighlight(ot, tokens);
        sumEl.innerHTML = buildHighlight(os, tokens);
      }
    });
  }

  function applySort(){
    const mode = (sortSel.value || "new");
    groups.forEach(g => {
      const grid = g.querySelector(".grid");
      if(!grid) return;
      const items = Array.from(grid.querySelectorAll("[data-card]"));
      items.sort((a,b) => {
        const ta = parseFloat(a.dataset.ts || "0");
        const tb = parseFloat(b.dataset.ts || "0");
        const ra = parseFloat(a.dataset.rel || "0");
        const rb = parseFloat(b.dataset.rel || "0");
        if(mode === "rel"){
          if(rb !== ra) return rb - ra;
          return tb - ta;
        }
        return tb - ta;
      });
      items.forEach(it => grid.appendChild(it));
    });
  }

  function applyFilter(){
    const q = (search.value || "").trim().toLowerCase();
    const active = (document.querySelector(".pill.active") || {}).dataset?.sector || "ALL";
    const onlyTop = topOnly.checked;
    const onlyFav = favOnly.checked;
    const favs = getFavs();

    cards.forEach(card => {
      const sector = (card.dataset.sector || "");
      const isTop = (card.dataset.top === "1");
      const hay = (card.dataset.hay || "").toLowerCase();
      const url = (card.dataset.url || "");
      const isFav = favs.has(url);

      let ok = true;
      if(active !== "ALL" && sector !== active) ok = false;
      if(onlyTop && !isTop) ok = false;
      if(onlyFav && !isFav) ok = false;
      if(q && hay.indexOf(q) === -1) ok = false;

      card.style.display = ok ? "" : "none";
    });

    groups.forEach(g => {
      const anyVisible = Array.from(g.querySelectorAll("[data-card]")).some(c => c.style.display !== "none");
      g.style.display = anyVisible ? "" : "none";
    });

    applyHighlight();
  }

  function renderPresets(){
    if(!presetBar) return;
    presetBar.innerHTML = PRESETS.map(p => `<button class="preset" data-preset="${escapeHtml(p.q)}">${escapeHtml(p.k)}</button>`).join("");
    const saved = localStorage.getItem(LS_PRESETS) || "";
    if(saved) search.value = saved;
  }

  function bindPresets(){
    if(!presetBar) return;
    presetBar.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-preset]");
      if(!btn) return;
      const q = btn.getAttribute("data-preset") || "";
      // 단일 선택 느낌: 클릭하면 그 키워드로 교체
      search.value = q;
      localStorage.setItem(LS_PRESETS, q);
      // active 표시
      Array.from(presetBar.querySelectorAll(".preset")).forEach(x => x.classList.toggle("active", x === btn));
      applyFilter();
    });
  }

  function initFavButtons(){
    const favs = getFavs();
    cards.forEach(card => {
      const btn = card.querySelector("[data-clip]");
      const url = card.dataset.url || "";
      if(!btn || !url) return;
      const on = favs.has(url);
      btn.classList.toggle("on", on);
      btn.textContent = on ? "★" : "☆";
      btn.addEventListener("click", () => {
        const set = getFavs();
        const nowOn = set.has(url) ? (set.delete(url), false) : (set.add(url), true);
        saveFavs(set);
        btn.classList.toggle("on", nowOn);
        btn.textContent = nowOn ? "★" : "☆";
        if(favOnly.checked) applyFilter();
      });
    });
  }

  pills.forEach(p => {
    p.addEventListener("click", () => {
      const sector = p.dataset.sector;
      setActivePill(sector);
      applyFilter();
      if(sector === "ALL"){
        window.scrollTo({top:0, behavior:"smooth"});
      }else{
        const sec = document.getElementById("sec-" + sector);
        if(sec) sec.scrollIntoView({behavior:"smooth", block:"start"});
      }
    });
  });

  search.addEventListener("input", () => {
    localStorage.setItem(LS_PRESETS, search.value || "");
    applyFilter();
  });
  topOnly.addEventListener("change", applyFilter);
  favOnly.addEventListener("change", applyFilter);
  sortSel.addEventListener("change", () => { applySort(); applyFilter(); });
  themeBtn.addEventListener("click", toggleTheme);

  loadTheme();
  setActivePill("ALL");
  cacheOriginalText();
  renderPresets();
  bindPresets();
  applySort();
  initFavButtons();
  applyFilter();
})();



def render_html(
    report_date: datetime,
    tagged: list[TaggedArticle],
    keyword_trends: list[tuple[str, int]],
) -> str:
    date_str = report_date.strftime("%Y-%m-%d")

    top_items = sorted(
        [it for it in tagged if "감독입법" not in it.sectors and "기타" not in it.sectors],
        key=lambda x: x.article.pub_date,
        reverse=True,
    )[:10]

    by_sector: dict[str, list[TaggedArticle]] = defaultdict(list)
    for item in tagged:
        for sector in item.sectors:
            by_sector[sector].append(item)

    sector_order = [
        "대부", "은행", "보험", "증권", "카드", "캐피탈",
        "저축은행", "핀테크", "감독입법", "기타",
    ]
    sector_counts = {s: len(by_sector.get(s, [])) for s in sector_order if s in by_sector}

    def pill_html(sector: str, count: int) -> str:
        return f"<button class='pill' data-sector-pill data-sector='{_h(sector)}'><strong>{_h(sector)}</strong><span class='count'>{count}</span></button>"

    pills = ["<button class='pill active' data-sector-pill data-sector='ALL'><strong>전체</strong><span class='count'>{}</span></button>".format(len(tagged))]
    for s in sector_order:
        if s in sector_counts:
            pills.append(pill_html(s, sector_counts[s]))

    def card_html(item: TaggedArticle, is_top: bool) -> str:
        a = item.article
        sector = item.sectors[0] if item.sectors else "기타"

        title = a.title or ""
        summary = a.description or ""
        pub = _fmt_dt(getattr(a, "pub_date", None))
        ts = _ts_dt(getattr(a, "pub_date", None))
        press = _get_press(a)

        naver = _link_naver(a)
        orig = _link_original(a)
        primary = _primary_link(a)

        rel = _relevance_value(a)
        rel_label = None
        rel_class = ""
        rel_val = 0.0
        if isinstance(rel, float):
            rel_val = rel
            rel_label, rel_class = _relevance_label(rel)

        cached = bool(getattr(a, "summary_cached", False))

        # 검색용 hay
        hay = " ".join([title, summary, sector, press]).strip()

        btns = []
        if naver:
            btns.append(f"<a class='btn small primary' href='{_h(naver)}' target='_blank' rel='noopener noreferrer'>네이버</a>")
        if orig and orig != naver:
            btns.append(f"<a class='btn small' href='{_h(orig)}' target='_blank' rel='noopener noreferrer'>원문</a>")
        if not btns and primary:
            btns.append(f"<a class='btn small primary' href='{_h(primary)}' target='_blank' rel='noopener noreferrer'>열기</a>")

        badges = [f"<span class='badge'>{_h(sector)}</span>"]
        if is_top:
            badges.append("<span class='badge'>TOP</span>")
        if rel_label is not None:
            # 숫자까지 보여주고 싶으면 (예: 0.78) 추가
            badges.append(f"<span class='badge {rel_class}'>Rel {rel_label}</span>")
        if cached:
            badges.append("<span class='badge'>⚡ 캐시</span>")

        return (
            f"<article class='card' data-card "
            f"data-sector='{_h(sector)}' data-top={'1' if is_top else '0'} "
            f"data-hay='{_h(hay)}' data-ts='{ts}' data-rel='{rel_val}'>"
            f"  <h3 class='title'><a href='{_h(primary)}' target='_blank' rel='noopener noreferrer' data-title>{_h(title)}</a></h3>"
            f"  <div class='meta-row'>"
            f"    <span>{_h(pub)}</span>"
            f"    {f'<span>·</span><span>{_h(press)}</span>' if press else ''}"
            f"    <span>·</span>{''.join(badges)}"
            f"  </div>"
            f"  <p class='summary' data-summary>{_h(summary)}</p>"
            f"  <div class='actions'>{''.join(btns)}</div>"
            f"</article>"
        )

    top_cards = "\n".join(card_html(it, True) for it in top_items) if top_items else "<div class='note'>해당 기간 Top 이슈가 없습니다.</div>"

    sector_sections: list[str] = []
    for s in sector_order:
        items = sorted(by_sector.get(s, []), key=lambda x: x.article.pub_date, reverse=True)[:10]
        if not items:
            continue
        cards = "\n".join(card_html(it, False) for it in items)
        sector_sections.append(
            f"<section data-group id='sec-{_h(s)}'>"
            f"  <div class='section-head'>"
            f"    <h2>{_h(s)}<span class='count'>{len(by_sector.get(s, []))}</span></h2>"
            f"    <div class='note'>상위 10개 · 섹터 클릭/검색/정렬 가능</div>"
            f"  </div>"
            f"  <div class='grid'>{cards}</div>"
            f"</section>"
        )

    if keyword_trends:
        chips = []
        for kw, n in keyword_trends[:20]:
            chips.append(f"<span class='kchip'>{_h(kw)} <span class='n'>{n}</span></span>")
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
    <div class="topbar">
      <div class="header">
        <div class="header-top">
          <div>
            <h1>금융권 일일 언론동향 <span style="color:var(--muted);">({date_str})</span></h1>
            <div class="meta">대부업권 중심 · 전 금융업권 주요 기사 요약</div>
          </div>
          <div class="controls">
            <div class="input" title="제목/요약/섹터/언론사에서 검색">
              <span style="color:var(--muted); font-size:12px;">🔎</span>
              <input id="searchInput" type="text" placeholder="키워드로 검색 (예: 연체, PF, 국민연금)"/>
            </div>

            <span class="select" title="정렬">
              <span style="color:var(--muted); font-size:12px;">정렬</span>
              <select id="sortSel">
                <option value="new" selected>최신순</option>
                <option value="rel">관련도순</option>
              </select>
            </span>

            <label class="toggle"><input id="topOnly" type="checkbox"/> Top만</label> 저장만</label>
            <button id="themeBtn" class="btn">다크</button>
            <a class="btn" href="index.html">최근 리포트</a>
          </div>
        </div>
        <div class="nav">
          {''.join(pills)}
        </div>
        <div class="presetbar" id="presetBar"></div>
      </div>
    </div>

    <div class="main">
      <section data-group id="sec-TOP">
        <div class="section-head">
          <h2>오늘의 Top 이슈 10<span class="count">{len(top_items) if top_items else 0}</span></h2>
          <div class="note">정책/시장 영향도가 큰 기사 우선</div>
        </div>
        <div class="grid">
          {top_cards}
        </div>
      </section>

      {''.join(sector_sections)}

      <section data-group id="sec-KW">
        <div class="section-head">
          <h2>키워드 트렌드</h2>
          <div class="note">상위 20개</div>
        </div>
        {chips_html}
      </section>

      <div class="footer">
        본 리포트는 Naver News Search API 기반으로 자동 생성되었습니다.
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
        "          <div class='meta'>대부업권 중심 · 전 금융업권 주요 기사 요약</div>\n"
        "        </div>\n"
        "        <div class='pills'>\n"
        "          <div class='pill'>생성: 자동</div>\n"
        "          <div class='pill'>출처: Naver News Search API</div>\n"
        "          <div class='pill'><a href='index.html'>최근 리포트</a></div>\n"
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
        "  <title>최근 리포트</title>\n"
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
