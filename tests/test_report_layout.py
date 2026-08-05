"""상단 툴바 레이아웃 / 전체 폭 shell / 반응형 카드 그리드 회귀 테스트.

문자열 전체 snapshot이 아니라 **구조 계약**(하나의 컨트롤, 안정적 selector, 열 수
breakpoint, 카드 요소 순서)을 검증한다.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from src.config import KST
from src.pipeline import report as report_module
from src.pipeline.normalize import Article
from src.pipeline.report import render_html, render_markdown
from src.pipeline.tagger import TaggedArticle

BASE = datetime(2026, 8, 5, 9, 0, tzinfo=KST)


def _tagged(idx: int, *, sector: str = "대부", topics: list[str] | None = None) -> TaggedArticle:
    article = Article(
        title=f"기사 {idx} <b>제목</b> & 특수문자",
        description="네이버 스니펫 원본 설명입니다. 미리보기 문단으로 표시됩니다.",
        link=f"https://news.example.com/{idx}",
        originallink=f"https://press.example.com/{idx}",
        naver_link=f"https://n.news.naver.com/{idx}",
        pub_date=BASE,
        query="대부업",
    )
    article.relevance_score = 9.0
    return TaggedArticle(article, [sector], topics if topics is not None else ["연체·부실"], [])


@pytest.fixture(scope="module")
def html() -> str:
    items = [
        _tagged(1, sector="대부"),
        _tagged(2, sector="은행", topics=["정책·제도개선"]),
        _tagged(3, sector="저축은행", topics=[]),
    ]
    return render_html(BASE, items, [("연체", 5)])


@pytest.fixture(scope="module")
def css() -> str:
    return (Path(report_module.__file__).resolve().parent / "templates" / "report.css").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="module")
def js() -> str:
    return (Path(report_module.__file__).resolve().parent / "templates" / "report.js").read_text(
        encoding="utf-8"
    )


# --- DOM 구조 ------------------------------------------------------------------


def test_left_sidebar_and_mobile_sheet_are_gone(html: str, css: str, js: str):
    """사이드바는 CSS로 숨기는 게 아니라 마크업에서 제거한다."""
    for token in ("filterSidebar", "class=\"sidebar\"", "sheet-backdrop", "mobile-mini", "filter-shell"):
        assert token not in html, token
    # 남은 CSS/JS도 사이드바 구조를 참조하지 않는다.
    for token in (".sidebar", ".filter-shell", ".sheet-backdrop", ".mobile-mini"):
        assert token not in css, token
    for token in ("filterSidebar", "mobileFilterBtn", "mobileSearchBtn", "mobileTopBtn"):
        assert token not in js, token


def test_top_header_and_control_rows_exist(html: str):
    assert "<header class=\"report-header\">" in html
    assert "report-header__identity" in html
    assert "report-header__actions" in html
    assert "report-controls__primary" in html
    assert "report-controls__filters" in html
    assert "<main class=\"report-content\">" in html


def test_each_control_is_rendered_exactly_once(html: str):
    for control_id in ("searchInput", "sortSel", "topOnly", "favOnly", "themeBtn"):
        assert html.count(f'id="{control_id}"') == 1, control_id


def test_document_has_no_duplicate_ids(html: str):
    ids = re.findall(r'\sid="([^"]+)"', html) + re.findall(r"\sid='([^']+)'", html)
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]


def test_sector_and_topic_filters_live_in_labelled_filter_groups(html: str):
    # 인라인 CSS에도 같은 클래스명이 나오므로 마크업 쪽 속성 형태로 자른다.
    filters_block = html.split('<div class="report-controls__filters">')[1].split("</section>")[0]
    assert "data-sector-pill" in filters_block
    assert "data-topic-pill" in filters_block
    assert filters_block.count('class="filter-group"') == 2
    assert 'aria-labelledby="sectorFilterLabel"' in filters_block
    assert 'aria-labelledby="topicFilterLabel"' in filters_block


def test_status_readouts_are_present_for_results_and_saved(html: str):
    assert 'id="resultCount"' in html and 'aria-live="polite"' in html
    assert 'id="savedCount"' in html


# --- JavaScript 계약 ------------------------------------------------------------


def test_js_uses_stable_selectors_not_dom_position(js: str):
    """레이아웃이 바뀌어도 깨지지 않도록 id/data attribute만 쓴다."""
    assert "nth-child" not in js
    assert "parentElement.parentElement" not in js
    for hook in (
        "getElementById(\"searchInput\")",
        "getElementById(\"sortSel\")",
        "getElementById(\"topOnly\")",
        "getElementById(\"favOnly\")",
        "getElementById(\"themeBtn\")",
        "[data-sector-pill]",
        "[data-topic-pill]",
        "[data-card]",
        "[data-group]",
        "[data-clip]",
        "[data-load-more]",
    ):
        assert hook in js, hook


def test_pill_selection_state_is_not_colour_only(html: str, js: str, css: str):
    assert "aria-pressed='false'" in html and "aria-pressed='true'" in html
    assert 'setAttribute("aria-pressed"' in js
    # 선택된 chip에는 체크 표시가 함께 붙는다.
    assert ".pill.active::before" in css


# --- 그리드 --------------------------------------------------------------------


def test_top_and_sector_sections_share_the_grid_component(html: str):
    top_section = html.split("<section data-group id=\"sec-TOP\">")[1].split("</section>")[0]
    assert "<div class=\"grid\">" in top_section
    sector_section = html.split("<section data-group id='sec-대부'>")[1].split("</section>")[0]
    assert "<div class='grid'>" in sector_section
    # Top 섹션만 위계를 한 단계 높인다(카드 컴포넌트는 동일).
    assert "section-head section-head--top" in top_section


def _grid_columns_at(css: str, min_width: int | None) -> str:
    """지정한 breakpoint 블록 안의 .grid 열 정의."""
    if min_width is None:
        block = css.split("@media")[0]
    else:
        block = css.split(f"@media (min-width:{min_width}px){{")[1].split("\n}")[0]
    match = re.search(r"\.grid\{[^}]*grid-template-columns:([^;]+);", block)
    assert match, f"no .grid columns at {min_width}"
    return match.group(1).strip()


def test_grid_defines_five_four_three_two_one_columns(css: str):
    assert "minmax(0, 1fr)" in _grid_columns_at(css, None)  # 기본 1열
    assert _grid_columns_at(css, 640) == "repeat(2, minmax(0, 1fr))"
    assert _grid_columns_at(css, 960) == "repeat(3, minmax(0, 1fr))"
    assert _grid_columns_at(css, 1280) == "repeat(4, minmax(0, 1fr))"
    assert _grid_columns_at(css, 1600) == "repeat(5, minmax(0, 1fr))"


def test_shell_uses_full_width_with_a_readable_cap(css: str):
    shell = css.split(".report-shell{")[1].split("}")[0]
    match = re.search(r"width:min\(calc\(100% - (\d+)px\), (\d+)px\)", shell)
    assert match, shell
    gutter, cap = int(match.group(1)), int(match.group(2))
    assert gutter >= 24  # 좌우 최소 여백
    assert 1600 <= cap <= 2000  # 넓은 화면에서도 한 줄이 과도하게 길어지지 않는다


def test_no_fixed_pixel_widths_that_could_overflow_the_viewport(css: str):
    """그리드·카드·컨트롤 폭은 고정 px를 쓰지 않는다(모바일 가로 스크롤 방지)."""
    for selector in (".grid{", ".card{", ".report-content{", ".report-controls{"):
        rule = css.split(selector)[1].split("}")[0]
        assert not re.search(r"(?<!min-)(?<!max-)width:\s*\d+px", rule), selector
    assert "overflow-x:hidden" in css.split("body{")[1].split("}")[0]


# --- 카드 ----------------------------------------------------------------------


def _first_card(html: str) -> str:
    return "<article class='card'" + html.split("<article class='card'")[1].split("</article>")[0]


def test_card_keeps_the_information_hierarchy_order(html: str):
    card = _first_card(html)
    order = [
        card.index("class='title'"),
        card.index("meta-row--primary"),
        card.index("meta-row--topics"),
        card.index("summary-panel"),
        card.index("card__actions"),
    ]
    assert order == sorted(order), card


def test_card_actions_use_the_bottom_alignment_class(html: str, css: str):
    assert "class='actions card__actions'" in html
    assert "margin-top:auto" in css.split(".card__actions{")[1].split("}")[0]
    assert "flex-direction:column" in css.split(".card{")[1].split("}")[0]


def test_card_title_is_clamped_only_where_a_tooltip_can_reveal_it(html: str, css: str):
    """터치 기기(hover 없음)에서는 제목을 자르지 않는다.

    잘린 제목을 되살리는 수단이 링크의 `title` 툴팁뿐인데, 터치 브라우저는 hover
    툴팁을 띄우지 않는다. clamp를 hover 환경으로 제한하지 않으면 모바일 사용자는
    기사를 열지 않고는 나머지 제목을 볼 수 없다.
    """
    base_rule = css.split(".title a{")[1].split("}")[0]
    assert "line-clamp" not in base_rule, base_rule
    assert "overflow-wrap:anywhere" in base_rule

    hover_block = css.split("@media (hover: hover){")[1].split("\n}")[0]
    assert "-webkit-line-clamp:3" in hover_block.split(".title a{")[1].split("}")[0]

    # clamp가 걸리는 환경에서는 전체 제목을 title 속성으로 확인할 수 있어야 한다.
    card = _first_card(html)
    assert re.search(r"<a href='[^']*'[^>]*title='[^']*기사 1[^']*'[^>]*data-title>", card), card


def test_badges_have_distinct_visual_weights(html: str, css: str):
    card = _first_card(html)
    assert "badge badge--sector" in card
    assert "badge badge--topic" in card
    for modifier in ("--sector", "--top", "--soft", "--topic"):
        assert f".badge{modifier}{{" in css.replace(" ", ""), modifier


def test_soft_badges_keep_readable_contrast(css: str):
    """11px 배지 텍스트는 muted(연회색 배경에서 4.5:1 미만)를 쓰지 않는다."""
    soft_rule = css.split(".badge--soft{")[1].split("}")[0]
    assert "var(--text-secondary)" in soft_rule
    assert "var(--text-muted)" not in soft_rule
    # 관련도 단계는 opacity로 흐리게 만들지 않는다 — 텍스트·테두리 대비가 함께 떨어진다.
    for level in ("r-high", "r-med", "r-low"):
        rule = css.split(f".badge.{level}{{")[1].split("}")[0]
        assert "opacity" not in rule, level
        assert "font-weight" in rule, level


def test_article_text_stays_html_escaped(html: str):
    assert "기사 1 &lt;b&gt;제목&lt;/b&gt; &amp; 특수문자" in html
    assert "기사 1 <b>제목</b>" not in html


def test_favorite_button_keeps_accessible_semantics(html: str, js: str):
    card = _first_card(html)
    assert "<button class='clip' type='button'" in card
    assert "aria-label='이 기사 저장'" in card
    assert 'setAttribute("aria-label", on ? "저장 해제" : "이 기사 저장")' in js


# --- 색상 토큰 / 다크 모드 -------------------------------------------------------


SEMANTIC_TOKENS = (
    "--page-bg",
    "--surface",
    "--surface-subtle",
    "--text-primary",
    "--text-secondary",
    "--text-muted",
    "--border",
    "--border-strong",
    "--accent",
    "--accent-hover",
    "--accent-soft",
)


def test_semantic_tokens_are_defined_for_light_and_dark(css: str):
    light = css.split(":root{")[1].split("\n}")[0]
    dark = css.split('html[data-theme="dark"]{')[1].split("\n}")[0]
    for token in SEMANTIC_TOKENS:
        assert f"{token}:" in light, token
        assert f"{token}:" in dark, token


def test_dark_mode_separates_page_surface_and_border(css: str):
    dark = css.split('html[data-theme="dark"]{')[1].split("\n}")[0]
    values = dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-f]{6});", dark))
    assert values["--page-bg"] != values["--surface"]
    assert values["--surface"] != values["--surface-subtle"]
    assert values["--border"] != values["--surface"]


def test_focus_visible_style_exists_and_is_theme_independent(css: str):
    focus_rule = css.split(":focus-visible{")[1].split("}")[0]
    assert "outline:" in focus_rule
    # outline 색은 테마별 토큰이라 라이트/다크 모두에서 보인다.
    assert "var(--focus-ring)" in focus_rule
    assert "--focus-ring:" in css.split(":root{")[1].split("\n}")[0]


def test_reduced_motion_is_respected(css: str):
    assert "@media (prefers-reduced-motion: reduce)" in css


# --- 마크다운 / 이메일 -----------------------------------------------------------


def test_markdown_never_leaks_web_layout_classes():
    md = render_markdown(BASE, [_tagged(1)], [("연체", 3)])
    for token in ("report-shell", "report-header", "grid", "card__actions", "summary-panel", "<div"):
        assert token not in md, token
    assert md.startswith("# 금융권 일일 언론동향")


def test_markdown_fallback_html_page_is_untouched_by_the_web_layout(tmp_path: Path):
    """이메일/마크다운 fallback 페이지는 light.css의 단순 레이아웃을 그대로 쓴다."""
    from src.pipeline.report import write_report

    paths = write_report(BASE, "# 제목\n\n- 항목", tmp_path)
    page = paths["html"].read_text(encoding="utf-8")
    for token in ("report-shell", "report-controls", ".grid{", "data-card"):
        assert token not in page, token
    assert "<div class='wrap'>" in page
