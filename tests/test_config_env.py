from __future__ import annotations

import os

from src.config import load_dotenv_if_present
from src.pipeline import fulltext_fetch


def test_dotenv_loads_values_without_overriding_existing(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 주석\n"
        "\n"
        "NEW_VAR=hello\n"
        'QUOTED_VAR="world"\n'
        "EXISTING_VAR=from_file\n"
        "broken line without equals... 아 이건 = 있네\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING_VAR", "from_shell")
    monkeypatch.delenv("NEW_VAR", raising=False)
    monkeypatch.delenv("QUOTED_VAR", raising=False)

    loaded = load_dotenv_if_present(env_file)

    assert loaded >= 2
    assert os.environ["NEW_VAR"] == "hello"
    assert os.environ["QUOTED_VAR"] == "world"
    # 이미 export된 값은 절대 덮어쓰지 않음 (CI secrets 우선)
    assert os.environ["EXISTING_VAR"] == "from_shell"

    monkeypatch.delenv("NEW_VAR", raising=False)
    monkeypatch.delenv("QUOTED_VAR", raising=False)


def test_dotenv_missing_file_is_noop(tmp_path):
    assert load_dotenv_if_present(tmp_path / "nope.env") == 0


class _FakeStreamingResponse:
    encoding = "utf-8"

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_html_caps_response_size(monkeypatch):
    # 64KiB 청크 100개(≈6.4MB)를 주지만 max_bytes에서 잘려야 한다
    chunks = [b"a" * 65536] * 100
    monkeypatch.setattr(
        fulltext_fetch.requests,
        "get",
        lambda *args, **kwargs: _FakeStreamingResponse(chunks),
    )

    html = fulltext_fetch.fetch_html("https://example.com/huge", max_bytes=200_000)

    # 상한 도달 직후 중단 — 상한 + 청크 1개를 넘지 않음
    assert len(html.encode("utf-8", errors="ignore")) <= 200_000 + 65536
