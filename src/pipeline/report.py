from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import markdown

from src.pipeline.tagger import TaggedArticle


def _format_article(item: TaggedArticle) -> str:
    title = item.article.title
    link = item.article.link
    summary = item.article.description.strip()
    if len(summary) > 160:
        summary = summary[:157].rstrip() + "..."
    return f"- [{title}]({link})\n  - {summary}"


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
        lines.extend(_format_article(item) for item in top_items)
    else:
        lines.append("- 해당 기간 기사 없음")

    lines.append("")
    lines.append("## 업권별 주요 기사")
    by_sector: dict[str, list[TaggedArticle]] = defaultdict(list)
    for item in tagged:
        for sector in item.sectors:
            by_sector[sector].append(item)

    sector_order = [
        "은행",
        "보험",
        "증권",
        "카드",
        "캐피탈",
        "저축은행",
        "대부",
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


def write_report(report_date: datetime, markdown_text: str, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = report_date.strftime("%Y-%m-%d")
    md_path = output_dir / f"{date_str}.md"
    html_path = output_dir / f"{date_str}.html"

    md_path.write_text(markdown_text, encoding="utf-8")
    html_content = markdown.markdown(markdown_text, extensions=["tables"])
    html_page = (
        "<!doctype html>\n"
        "<html lang='ko'>\n"
        "<head>\n"
        "  <meta charset='utf-8'/>\n"
        f"  <title>금융권 일일 언론동향 {date_str}</title>\n"
        "  <style>body{font-family:Arial, sans-serif; max-width:960px; margin:0 auto; padding:24px;}"
        "  h1,h2,h3{color:#1b1b1b;} a{color:#0b57d0;}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{html_content}\n"
        "</body>\n"
        "</html>"
    )
    html_path.write_text(html_page, encoding="utf-8")

    return {"markdown": md_path, "html": html_path}


def write_index(recent_reports: list[Path], output_dir: Path) -> Path:
    index_path = output_dir / "index.html"
    links = "\n".join(
        f"<li><a href='{path.name}'>{path.stem}</a></li>" for path in recent_reports
    )
    html_page = (
        "<!doctype html>\n"
        "<html lang='ko'>\n"
        "<head><meta charset='utf-8'/><title>최근 리포트</title></head>\n"
        "<body>\n"
        "<h1>최근 14일 리포트</h1>\n"
        f"<ul>{links}</ul>\n"
        "</body>\n"
        "</html>"
    )
    index_path.write_text(html_page, encoding="utf-8")
    return index_path
