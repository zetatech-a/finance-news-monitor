"""테스트 전역 픽스처.

Gemini 테스트들은 처리량 상한을 바꿔가며 검증하느라 `GEMINI_*` 환경변수를 직접
설정한다. 그 값이 다음 테스트로 새면 실행 순서에 따라 결과가 달라지므로
(예: 코드 기본값을 확인하는 테스트가 앞 테스트의 상한을 읽어버림),
매 테스트 전에 지우고 끝나면 원래 값을 되돌린다.
"""
from __future__ import annotations

import os

import pytest

GEMINI_ENV_PREFIX = "GEMINI_"


@pytest.fixture(autouse=True)
def isolate_gemini_env():
    saved = {k: v for k, v in os.environ.items() if k.startswith(GEMINI_ENV_PREFIX)}
    for key in saved:
        del os.environ[key]
    try:
        yield
    finally:
        for key in [k for k in os.environ if k.startswith(GEMINI_ENV_PREFIX)]:
            del os.environ[key]
        os.environ.update(saved)
