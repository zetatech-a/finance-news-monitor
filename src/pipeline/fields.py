"""dict/객체(Article, TaggedArticle) 양쪽을 지원하는 공용 필드 접근 헬퍼.

Article의 파이프라인 필드는 이제 전부 dataclass에 정식 선언되어 있지만,
테스트·스크립트가 dict 형태의 기사도 넘기므로 소비 측에서는 이 헬퍼로
두 형태를 동일하게 다룬다. (과거에는 같은 헬퍼가 5개 모듈에 복사돼 있었다.)
"""
from __future__ import annotations

from typing import Any


def field_value(obj: Any, key: str) -> Any:
    """dict이면 .get(key), 객체면 getattr — 없으면 None."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def unwrap_article(item: Any) -> Any:
    """TaggedArticle처럼 .article을 가진 래퍼면 내부 Article을, 아니면 그대로 반환."""
    article = field_value(item, "article")
    return item if article is None else article
