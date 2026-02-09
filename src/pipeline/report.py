from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import markdown

from src.pipeline.tagger import TaggedArticle

import re
import html

_MD_SPECIAL = r"\\`*_{}[]()#+-.!"

def md_escape(text: str) -> str:
    t = (text or "").strip()
    # 네이버 API가 주는 <b> 태그 제거(있을 수 있음)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)

    # 마크다운 링크 텍스트 깨짐 방지: [ ] ( ) 등 이스케이프
    # 특히 [ ] 가 핵심
    t = t.replace("\\", "\\\\")
    t = t.replace("[", r"\[").replace("]", r"\]")
    t = t.replace("(", r"\(").replace(")", r"\)")
    return t

def _truncate(text: str, n: int = 170) -> str:
    t = (text or "").strip().replace("\n", " ").replace("\r", " ")
    if len(t) > n:
        return t[: n - 3].rstrip() + "..."
    return t


def _format_article(item: TaggedArticle) -> str:
    # ✅ 기사 1개 = 1줄(제목 — 요약)로 정리: HTML에서 훨씬 깔끔
    title = md_escape(item.article.title)
    link = item.article.link
    summary = md_escape(_truncate(item.article.description, 170))
    return f"- [{title}]({link}) — {summary}"


def _format_top_issue(item: TaggedArticle) -> str:
    title = (item.article.title or "").strip()
    link = item.article.link
    summary = _truncate(item.article.description, 180)
    return f"- [{title}]({link}) — {summary}"


def render_markdown(
    report_date: datetime,
    tagged: list[TaggedArticle],
    keyword_trends: list[tuple[str, int]],
) -> str:
    header = f"# 금융권 일일 언론동향 ({report_date.strftime('%Y-%m-%d')})"
    lines = [header, ""]

    top_items = sorted(
        [
            item
            for item in tagged
            if "감독입법" not in item.sectors and "기타" not in item.sectors
        ],
        key=lambda x: x.article.pub_date,
        reverse=True,
    )[:10]

    lines.append("## 오늘의 Top 이슈 10")
    if top_items:
        lines.extend(_format_top_issue(item) for item in top_items)
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
        "보험",
        "증권",
        "카드",
        "캐피탈",
        "저축은행",
        "핀테크",
        "감독입법",
        "기타",
    ]

    for sector in sector_order:
        if sector not in by_sector:
            continue
        lines.append(f"### {sector}")
        for item in sorted(by_sector[sector], key=lambda x: x.article.pub_date, reverse=True)[:10]:
            lines.append(_format_article(item))
        lines.append("")

    lines.append("## 대부업권 집중 섹션")
    dabu_items = by_sector.get("대부", [])
    if dabu_items:
        for item in sorted(dabu_items, key=lambda x: x.article.pub_date, reverse=True)[:15]:
            lines.append(_format_article(item))
    else:
        lines.append("- 해당 기간 기사 없음")

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

def write_report(report_date: datetime, markdown_text: str, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = report_date.strftime("%Y-%m-%d")
    md_path = output_dir / f"{date_str}.md"
    html_path = output_dir / f"{date_str}.html"

    md_path.write_text(markdown_text, encoding="utf-8")

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

