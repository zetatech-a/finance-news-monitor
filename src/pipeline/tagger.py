from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from src.pipeline.normalize import Article


@dataclass
class TaggedArticle:
    article: Article
    sectors: list[str]
    matched_keywords: list[str]


def tag_articles(articles: list[Article], sector_queries: dict[str, list[str]]) -> list[TaggedArticle]:
    tagged: list[TaggedArticle] = []
    for article in articles:
        text = f"{article.title} {article.description}".lower()
        matched_keywords: list[str] = []
        sectors: list[str] = []
        for sector, keywords in sector_queries.items():
            hits = [kw for kw in keywords if kw.lower() in text]
            if hits:
                sectors.append(sector)
                matched_keywords.extend(hits)
        if not sectors:
            sectors = ["기타"]
        tagged.append(
            TaggedArticle(
                article=article,
                sectors=sectors,
                matched_keywords=matched_keywords,
            )
        )
    return tagged


def keyword_trends(tagged: list[TaggedArticle], top_n: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for item in tagged:
        counter.update(item.matched_keywords)
    return counter.most_common(top_n)
