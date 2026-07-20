from __future__ import annotations

from pathlib import Path

from src.pipeline import report


def test_template_files_exist():
    templates = Path(report.__file__).resolve().parent / "templates"
    for name in ("report.css", "report.js", "light.css"):
        assert (templates / name).is_file(), f"missing template: {name}"


def test_templates_are_loaded_and_nonempty():
    assert len(report._UI_CSS) > 1000
    assert len(report._UI_JS) > 1000
    assert len(report._LIGHT_CSS) > 500


def test_rendered_html_inlines_templates():
    from datetime import datetime

    from src.config import KST

    html = report.render_html(datetime(2026, 7, 15, tzinfo=KST), [], [])
    # CSS/JS가 여전히 인라인으로 삽입되는지 (외부 파일 링크 없이 단일 HTML 유지)
    assert ":root{" in html
    assert "requestAnimationFrame" in html
    assert "<link" not in html
