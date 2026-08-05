"""실제 렌더링 검증 — CSS 문자열이 아니라 브라우저가 계산한 결과를 본다.

Playwright와 Chromium이 설치된 환경에서만 실행되고, 없으면 skip한다
(CI의 daily/smoke 워크플로는 pytest를 돌리지 않으므로 개발 환경 전용 검증이다).

    pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.config import KST
from src.pipeline.normalize import Article
from src.pipeline.report import render_html
from src.pipeline.tagger import TaggedArticle

sync_playwright = pytest.importorskip(
    "playwright.sync_api", reason="playwright 미설치 — 렌더링 검증 skip"
).sync_playwright

BASE = datetime(2026, 8, 5, 9, 0, tzinfo=KST)
AI_LINES = [
    "금융위원회가 대부업 감독규정 개정안을 의결해 최고금리 산정 방식을 손질했다.",
    "개정 규정은 2026년 9월 1일부터 등록 대부업체 900곳에 적용된다.",
    "금융위는 시행 6개월 뒤 이행 실태를 점검하고 위반 업체를 제재할 방침이다.",
]
SECTORS = ("대부", "은행", "저축은행", "상호금융", "여전", "보험")


def _chromium_path() -> str | None:
    """번들 chromium 경로. 없으면 Playwright 기본 탐색에 맡긴다."""
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    matches = sorted(root.glob("chromium-*/chrome-linux/chrome"))
    return str(matches[-1]) if matches else None


def _article(idx: int, *, ai: bool, long_title: bool, rejected: bool = False, related: int = 0) -> Article:
    title = (
        "금융위원회·금융감독원 합동 점검에서 드러난 대부업권 최고금리 산정 방식의 구조적 문제와 "
        "저축은행·상호금융권으로 번지는 연체율 상승 흐름 종합 분석"
        if long_title
        else f"기사 {idx} — 연체율과 자금조달 여건 점검"
    )
    article = Article(
        title=title,
        description=(
            "네이버 스니펫 원본 설명입니다. 미리보기 문단은 데스크톱 3줄, 모바일 2줄까지만 "
            "표시되도록 clamp가 걸려 있으며 긴 문장이 카드 밖으로 넘치지 않아야 합니다."
        ),
        link=f"https://news.example.com/{idx}",
        originallink=f"https://press.example.com/{idx}",
        naver_link=f"https://n.news.naver.com/{idx}",
        pub_date=BASE - timedelta(minutes=idx),
        query="대부업",
    )
    article.relevance_score = 9.5 - idx * 0.01
    article.source_description = (
        "네이버 원본 스니펫 — 내용 거부 기사에서 표시 요약으로 되돌아가는 문장입니다."
    )
    if ai:
        article.summary_lines = list(AI_LINES)
    if rejected:
        article.summary_rejection_reason = "title_body_mismatch"
    if related:
        article.cluster_id = f"cluster-{idx}"
        article.cluster_size = related + 1
        article.related_articles = [
            {
                "title": f"같은 이슈를 다룬 관련 보도 {n}",
                "link": f"https://related{n}.example.com/{idx}",
                "press": f"press{n}.co.kr",
            }
            for n in range(related)
        ]
    return article


@pytest.fixture(scope="module")
def report_file(tmp_path_factory) -> Path:
    # 세 요약 상태(ai / content_rejected / preview)와 관련 기사 목록이 모두 렌더되도록
    # 구성한다 — 대비·구조 검증이 조용히 건너뛰지 않게 하기 위한 것이다.
    items = [
        TaggedArticle(
            _article(
                i,
                ai=(i % 3 == 0),
                long_title=(i % 5 == 0),
                rejected=(i % 3 == 1),
                related=(3 if i % 4 == 0 else 0),
            ),
            [SECTORS[i % len(SECTORS)]],
            ["연체·부실", "정책·제도개선"],
            [],
        )
        for i in range(24)
    ]
    path = tmp_path_factory.mktemp("report") / "report.html"
    path.write_text(render_html(BASE, items, [("연체", 4)]), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def browser():
    executable = _chromium_path()
    with sync_playwright() as p:
        try:
            launched = p.chromium.launch(executable_path=executable) if executable else p.chromium.launch()
        except Exception as exc:  # pragma: no cover - 브라우저 미설치 환경
            pytest.skip(f"chromium 실행 불가: {exc}")
        yield launched
        launched.close()


def _measure(browser, report_file: Path, width: int, height: int, theme: str = "light") -> dict:
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(report_file.as_uri())
    page.evaluate("theme => localStorage.setItem('reportTheme', theme)", theme)
    page.reload()
    page.wait_for_timeout(120)
    data = page.evaluate(
        """
        () => {
          const columns = sel => {
            const el = document.querySelector(sel);
            return el ? getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length : 0;
          };
          const topCards = Array.from(document.querySelectorAll('#sec-TOP .grid [data-card]'))
              .filter(c => c.style.display !== 'none');
          const aiList = document.querySelector('.summary-panel__list');
          const preview = document.querySelector('.summary-panel__text');
          const doc = document.documentElement;
          return {
            topColumns: columns('#sec-TOP .grid'),
            sectorColumns: columns("section[data-group]:not(#sec-TOP):not(#sec-KW) .grid"),
            topRows: new Set(topCards.map(c => Math.round(c.getBoundingClientRect().top))).size,
            topCardCount: topCards.length,
            cardWidth: topCards.length ? topCards[0].getBoundingClientRect().width : 0,
            horizontalOverflow: doc.scrollWidth > doc.clientWidth,
            cardOverflow: Array.from(document.querySelectorAll('[data-card]'))
                .filter(c => c.offsetParent !== null)
                .some(c => c.getBoundingClientRect().right > doc.clientWidth + 1),
            aiLineClamp: aiList ? getComputedStyle(aiList).webkitLineClamp : null,
            aiItemsRendered: aiList ? aiList.querySelectorAll('li').length : 0,
            aiFullyVisible: aiList ? aiList.scrollHeight <= aiList.clientHeight + 1 : false,
            previewLineClamp: preview ? getComputedStyle(preview).webkitLineClamp : null,
            controlsOverlap: (() => {
              const els = ['searchInput', 'sortSel', 'topOnly', 'favOnly', 'themeBtn']
                  .map(id => document.getElementById(id)).filter(Boolean);
              for (let i = 0; i < els.length; i++)
                for (let j = i + 1; j < els.length; j++) {
                  const a = els[i].getBoundingClientRect(), b = els[j].getBoundingClientRect();
                  if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) return true;
                }
              return false;
            })(),
          };
        }
        """
    )
    page.close()
    return data


@pytest.mark.parametrize(
    ("width", "height", "expected_columns"),
    [(375, 812, 1), (768, 1024, 2), (1024, 768, 3), (1280, 800, 4), (1920, 1080, 5)],
)
def test_responsive_column_counts(browser, report_file, width, height, expected_columns):
    data = _measure(browser, report_file, width, height)
    assert data["topColumns"] == expected_columns
    assert data["sectorColumns"] == expected_columns


def test_top_ten_fills_five_columns_by_two_rows_on_wide_desktop(browser, report_file):
    data = _measure(browser, report_file, 1920, 1080)
    assert data["topCardCount"] == 10
    assert data["topColumns"] == 5
    assert data["topRows"] == 2


@pytest.mark.parametrize("viewport", [(375, 812), (768, 1024), (1280, 800), (1920, 1080)])
def test_cards_stay_readable_and_inside_the_viewport(browser, report_file, viewport):
    data = _measure(browser, report_file, *viewport)
    assert data["horizontalOverflow"] is False
    assert data["cardOverflow"] is False
    assert data["cardWidth"] >= 270


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("viewport", [(375, 812), (1920, 1080)])
def test_ai_summary_is_never_truncated_and_preview_keeps_its_clamp(browser, report_file, viewport, theme):
    data = _measure(browser, report_file, *viewport, theme=theme)
    assert data["aiItemsRendered"] == 3
    assert data["aiLineClamp"] in (None, "none")
    assert data["aiFullyVisible"] is True
    assert data["previewLineClamp"] in ("2", "3")


@pytest.mark.parametrize("viewport", [(375, 812), (768, 1024), (1024, 768), (1920, 1080)])
def test_top_controls_never_overlap(browser, report_file, viewport):
    assert _measure(browser, report_file, *viewport)["controlsOverlap"] is False


# 본문 텍스트를 담는 selector 전부 — 배경이 흰 카드가 아니라 연회색 chip인 경우
# muted 토큰이 4.5:1 아래로 떨어지므로 실제 렌더 색으로 확인한다.
TEXT_SELECTORS = (
    ".title a",
    ".summary-panel__text",
    ".summary-panel__title",
    ".summary-panel--ai .summary-panel__list li",
    ".meta-row__time",
    # `.meta-row__press`는 제외한다 — 파이프라인이 Article에 press를 채우지 않아
    # 실제 리포트에 렌더되지 않는 방어적 경로다(dict 소비자용).
    ".summary-panel__status",
    ".input__icon",
    ".badge--sector",
    ".badge--soft",
    ".badge--topic",
    ".pill:not(.active)",
    ".pill:not(.active) strong",
    ".pill.active strong",
    ".status-chip",
    ".filter-group__label",
    ".note",
    ".count",
    ".kchip",
    ".kchip .n",
    ".footer",
    ".related a",
)

CONTRAST_SCRIPT = """
(selectors) => {
  // color-mix()/oklab 계산값을 실제 픽셀로 환산하기 위해 canvas에 칠해서 읽는다.
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', {willReadFrequently: true});
  const paintOver = (css, base) => {
    ctx.fillStyle = base; ctx.fillRect(0, 0, 1, 1);
    ctx.fillStyle = css; ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2]];
  };
  // 같은 색을 흰색·검은색 위에 칠해 알파와 premultiplied 색을 역산한다.
  // (반투명 배경을 부모 위에 합성하지 않으면 대비가 실제보다 훨씬 낮게 나온다)
  const decompose = css => {
    const onWhite = paintOver(css, '#fff'), onBlack = paintOver(css, '#000');
    return {alpha: 1 - (onWhite[0] - onBlack[0]) / 255, premultiplied: onBlack};
  };
  const composite = (css, baseRGB) => {
    const {alpha, premultiplied} = decompose(css);
    return premultiplied.map((v, i) => v + baseRGB[i] * (1 - alpha));
  };
  const effectiveBackground = el => {
    const layers = [];
    let node = el;
    while (node) {
      const css = getComputedStyle(node).backgroundColor;
      const {alpha} = decompose(css);
      if (alpha > 0.001) layers.push(css);
      if (alpha > 0.999) break;
      node = node.parentElement;
    }
    let base = [255, 255, 255];
    layers.reverse().forEach(layer => { base = composite(layer, base); });
    return base;
  };
  const luminance = rgb => {
    const [r, g, b] = rgb.map(v => {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const out = {};
  selectors.forEach(sel => {
    const el = document.querySelector(sel);
    if (!el) return;
    const style = getComputedStyle(el);
    const background = effectiveBackground(el);
    const l1 = luminance(composite(style.color, background)), l2 = luminance(background);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    // opacity를 텍스트에 걸면 실제 대비가 이 계산보다 낮아지므로 함께 본다.
    out[sel] = {ratio: Math.round(ratio * 100) / 100, opacity: Number(style.opacity)};
  });
  return out;
}
"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_small_text_meets_the_contrast_minimum(browser, report_file, theme):
    """카드·chip의 11~12px 텍스트가 4.5:1 아래로 내려가지 않는다."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(report_file.as_uri())
    page.evaluate("t => localStorage.setItem('reportTheme', t)", theme)
    page.reload()
    page.wait_for_timeout(150)
    measured = page.evaluate(CONTRAST_SCRIPT, list(TEXT_SELECTORS))
    page.close()

    # 클래스명이 바뀌어 측정 대상에서 조용히 빠지는 것도 회귀다.
    missing = [sel for sel in TEXT_SELECTORS if sel not in measured]
    assert not missing, missing

    failures = {
        sel: data for sel, data in measured.items()
        if data["ratio"] < 4.5 or data["opacity"] < 1
    }
    assert not failures, failures


def test_titles_are_not_clamped_on_touch_devices(browser, report_file):
    """터치 기기에서는 `title` 툴팁을 쓸 수 없으므로 제목을 자르지 않는다."""
    results = {}
    for name, options in (("hover", {}), ("touch", {"has_touch": True, "is_mobile": True})):
        context = browser.new_context(viewport={"width": 375, "height": 812}, **options)
        page = context.new_page()
        page.goto(report_file.as_uri())
        page.wait_for_timeout(120)
        results[name] = page.evaluate(
            """() => {
              const a = document.querySelector('.title a');
              return {
                hoverNone: matchMedia('(hover: none)').matches,
                clamp: getComputedStyle(a).webkitLineClamp,
                fullyVisible: a.scrollHeight <= a.clientHeight + 1,
              };
            }"""
        )
        context.close()

    assert results["touch"]["hoverNone"] is True
    assert results["touch"]["clamp"] in (None, "none")
    assert results["touch"]["fullyVisible"] is True
    # hover가 되는 환경에서는 clamp가 유지된다(툴팁으로 전체 제목 확인 가능).
    assert results["hover"]["hoverNone"] is False
    assert results["hover"]["clamp"] == "3"


def test_saved_count_only_counts_articles_present_in_this_report(browser, report_file):
    """날짜별 리포트가 localStorage를 공유하므로 저장 건수는 현재 리포트와 교집합이다."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(report_file.as_uri())
    page.evaluate(
        "() => localStorage.setItem('reportFavs_v1',"
        " JSON.stringify(['https://other-report.example.com/older']))"
    )
    page.reload()
    page.wait_for_timeout(150)
    # 다른 리포트에서 저장한 기사만 있으면 0건 — '저장 1건'인데 '저장만'은 0건인 모순을 막는다.
    assert page.text_content("#savedCount") == "저장 0건"

    page.click("[data-clip]")
    page.wait_for_timeout(120)
    assert page.text_content("#savedCount") == "저장 1건"
    # 다른 리포트의 즐겨찾기는 저장소에 그대로 남는다.
    assert page.evaluate("() => JSON.parse(localStorage.getItem('reportFavs_v1')).length") == 2

    page.check("#favOnly")
    page.wait_for_timeout(120)
    shown = page.evaluate(
        "() => Array.from(document.querySelectorAll('[data-card]'))"
        ".filter(c => c.style.display !== 'none').length"
    )
    assert shown > 0  # 표시된 저장 건수와 실제 필터 결과가 어긋나지 않는다
    page.close()


def test_filters_search_sort_favorites_and_theme_still_work(browser, report_file):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(report_file.as_uri())
    page.wait_for_timeout(120)

    shown = "() => Array.from(document.querySelectorAll('[data-card]')).filter(c => c.style.display !== 'none').length"
    initial = page.evaluate(shown)
    assert initial > 0

    # 업권 필터
    page.click("[data-sector-pill][data-sector='은행']")
    page.wait_for_timeout(80)
    assert page.evaluate(shown) < initial
    assert page.get_attribute("[data-sector-pill][data-sector='은행']", "aria-pressed") == "true"
    page.click("[data-sector-pill][data-sector='ALL']")
    page.wait_for_timeout(80)

    # 주제 필터
    page.click("[data-topic-pill][data-topic='연체·부실']")
    page.wait_for_timeout(80)
    assert page.evaluate(shown) > 0
    page.click("[data-topic-pill][data-topic='ALL']")
    page.wait_for_timeout(80)

    # 검색 + 초기화 (data-hay 기반)
    page.fill("#searchInput", "저축은행")
    page.wait_for_timeout(320)
    searched = page.evaluate(shown)
    assert 0 < searched < initial
    assert "건" in page.text_content("#resultCount")
    page.fill("#searchInput", "")
    page.wait_for_timeout(320)
    assert page.evaluate(shown) == initial

    # Top만 / 저장만 / 즐겨찾기
    page.check("#topOnly")
    page.wait_for_timeout(80)
    assert page.evaluate(shown) == 10
    page.uncheck("#topOnly")
    page.wait_for_timeout(80)

    page.click("[data-clip]")
    page.wait_for_timeout(80)
    assert page.evaluate("() => localStorage.getItem('reportFavs_v1')")
    # 토글 상태는 aria-pressed만 바뀌고 접근성 이름은 그대로다.
    assert page.get_attribute("[data-clip]", "aria-pressed") == "true"
    assert page.get_attribute("[data-clip]", "aria-label") == "기사 저장"
    page.check("#favOnly")
    page.wait_for_timeout(80)
    assert page.evaluate(shown) > 0
    page.uncheck("#favOnly")
    page.wait_for_timeout(80)

    # 정렬 / 다크 모드
    page.select_option("#sortSel", "rel")
    page.wait_for_timeout(120)
    assert page.evaluate(shown) == initial
    page.click("#themeBtn")
    page.wait_for_timeout(80)
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
    # 이름이 다음 동작을 설명하는 버튼이므로 aria-pressed를 붙이지 않는다.
    assert page.get_attribute("#themeBtn", "aria-pressed") is None
    assert page.get_attribute("#themeBtn", "aria-label") == "라이트 모드로 전환"

    # 네이버·원문·관련 기사 링크는 그대로 남는다.
    assert page.evaluate("() => document.querySelectorAll(\"a.btn.primary\").length") > 0
    assert page.evaluate("() => document.querySelectorAll('.actions a').length") > 0
    page.close()
