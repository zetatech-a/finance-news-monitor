from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from src.pipeline.normalize import Article


@dataclass
class TaggedArticle:
    article: Article
    sectors: list[str]
    matched_keywords: list[str]
    topics: list[str] = field(default_factory=list)


def tag_articles(
    articles: list[Article],
    sector_queries: dict[str, list[str]],
    topic_queries: dict[str, list[str]] | None = None,
) -> list[TaggedArticle]:
    tagged: list[TaggedArticle] = []
    for article in articles:
        title_text = (article.title or "").lower()
        desc_text = (article.description or "").lower()
        full_text = f"{title_text} {desc_text}"

        best_sector = "기타"
        best_score = 0
        best_hits: list[str] = []
        for sector, keywords in sector_queries.items():
            title_hits = [kw for kw in keywords if kw.lower() in title_text]
            desc_hits = [kw for kw in keywords if kw.lower() in desc_text]
            score = (len(title_hits) * 2) + len(desc_hits)
            if score > best_score:
                best_score = score
                best_sector = sector
                best_hits = [*title_hits, *[kw for kw in desc_hits if kw not in title_hits]]

        topics: list[str] = []
        if topic_queries:
            for topic, keywords in topic_queries.items():
                if any(kw.lower() in full_text for kw in keywords):
                    topics.append(topic)

        tagged.append(
            TaggedArticle(
                article=article,
                sectors=[best_sector if best_score > 0 else "기타"],
                matched_keywords=best_hits if best_score > 0 else [],
                topics=topics,
            )
        )
    return tagged


def keyword_trends(tagged: list[TaggedArticle], top_n: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for item in tagged:
        counter.update(item.matched_keywords)
    return counter.most_common(top_n)
