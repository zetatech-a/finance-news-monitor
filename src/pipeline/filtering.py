from __future__ import annotations

from typing import Iterable

from src.pipeline.normalize import Article


SPORTS_KEYWORDS = ("프로배구", "득점", "승부", "경기", "선수")
POLITICS_KEYWORDS = ("총선", "자민당", "개헌", "내각")
FINANCE_KEYWORDS = ("금리", "대출", "연체", "부실", "금감원", "금융위", "은행", "증권", "보험", "카드")


def _get_text_fields(article: Article | dict) -> str:
    if isinstance(article, Article):
        values = (
            article.title,
            article.description,
            article.query,
            article.link,
            article.originallink or "",
        )
    else:
        values = (
            article.get("title", ""),
            article.get("description", ""),
            article.get("summary", ""),
            article.get("content", ""),
            article.get("query", ""),
            article.get("link", ""),
            article.get("url", ""),
            article.get("originallink", ""),
        )
    return " ".join(str(value) for value in values if value).lower()


def _get_urls(article: Article | dict) -> tuple[str, ...]:
    if isinstance(article, Article):
        urls = (article.link, article.originallink or "")
    else:
        urls = (article.get("link", ""), article.get("url", ""), article.get("originallink", ""))
    return tuple(str(url) for url in urls if url)


def filter_articles(articles: Iterable[Article]) -> list[Article]:
    filtered: list[Article] = []
    for article in articles:
        urls = _get_urls(article)
        if any("sports.naver.com" in url for url in urls):
            continue

        text = _get_text_fields(article)
        if any(keyword in text for keyword in SPORTS_KEYWORDS):
            continue

        has_politics = any(keyword in text for keyword in POLITICS_KEYWORDS)
        has_finance = any(keyword in text for keyword in FINANCE_KEYWORDS)
        if has_politics and not has_finance:
            continue

        filtered.append(article)
    return filtered
