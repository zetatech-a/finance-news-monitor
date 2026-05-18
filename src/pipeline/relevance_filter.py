from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from src.ml.relevance_model import load_model, predict_proba
from src.pipeline.relevance_score import matched_terms as score_matched_terms, relevance_score
from src.pipeline.text_matcher import find_terms, has_any_term, normalize_text

logger = logging.getLogger(__name__)

ModelPolicy = Literal["authoritative", "candidate_hybrid", "rule_only"]


DOMAIN_SPECIFIC_ANCHORS: tuple[str, ...] = (
    "대부업",
    "불법사금융",
    "미등록대부",
    "채권추심",
    "최고금리",
    "은행권",
    "시중은행",
    "인터넷은행",
    "저축은행",
    "상호저축은행",
    "상호금융",
    "신협",
    "신협중앙회",
    "새마을금고",
    "농협 상호금융",
    "수협 상호금융",
    "산림조합",
    "카드론",
    "카드사",
    "여신금융협회",
    "여신협회",
    "여신전문금융협회",
    "여신전문금융업",
    "캐피탈사",
    "캐피탈",
    "보험사",
    "보험업계",
    "손해보험",
    "생명보험",
    "손보사",
    "생보사",
    "킥스",
    "k-ics",
    "금감원",
    "금융감독원",
    "금융위",
    "금융위원회",
    "금융당국",
    "연체율",
    "부실채권",
    "가계대출",
    "예대금리차",
    "pf",
    "검사",
    "제재",
    "과징금",
    "행정처분",
    "제도개선",
    "불완전판매",
    "보이스피싱",
)

GENERIC_RELEVANCE_ANCHORS: tuple[str, ...] = (
    "금융",
    "금리",
    "환율",
    "유가",
    "달러",
    "원자재",
    "연준",
    "fomc",
    "cpi",
    "pce",
    "국채",
    "증시",
    "코스피",
    "코스닥",
    "ipo",
    "상장",
    "공매도",
    "거래소",
    "채권",
    "회사채",
    "cp",
    "시장",
    "글로벌",
    "해외",
)

_CORPORATE_MACRO_TERMS: tuple[str, ...] = (
    "환율",
    "고환율",
    "유가",
    "원자재",
    "달러",
    "금리",
    "보험료",
)
_CORPORATE_CONTEXT_TERMS: tuple[str, ...] = (
    "항공사",
    "제조업체",
    "자동차",
    "업체",
    "기업",
    "운송",
    "영업이익",
    "순이익",
    "실적",
    "감소",
    "증가",
    "악화",
    "부담",
)
_MARKET_OR_IPO_CONTEXT_TERMS: tuple[str, ...] = (
    "ipo",
    "상장",
    "증시",
    "뉴욕증시",
    "코스피",
    "코스닥",
    "거래소",
    "연준",
    "fomc",
    "cpi",
    "pce",
    "국채",
    "공매도",
    "시장",
)
_REGULATOR_POLICY_ENFORCEMENT_TERMS: tuple[str, ...] = (
    "금감원",
    "금융감독원",
    "금융위",
    "금융위원회",
    "금융당국",
    "검사",
    "제재",
    "과징금",
    "행정처분",
    "제도개선",
    "불완전판매",
    "특별단속",
)


def _split_matched_terms(value: str | None) -> set[str]:
    return {normalize_text(term) for term in str(value or "").split(";") if term.strip()}


def has_domain_anchor(article_or_text: Any, matched_hard: str | None = None) -> bool:
    text = article_or_text if isinstance(article_or_text, str) else _text(article_or_text)
    matched = _split_matched_terms(matched_hard)
    domain = {normalize_text(term) for term in DOMAIN_SPECIFIC_ANCHORS}
    if matched & domain:
        return True
    return has_any_term(text, DOMAIN_SPECIFIC_ANCHORS)


def has_only_generic_anchors(article_or_text: Any, matched_hard: str | None = None) -> bool:
    text = article_or_text if isinstance(article_or_text, str) else _text(article_or_text)
    if has_domain_anchor(text, matched_hard):
        return False
    matched = _split_matched_terms(matched_hard)
    generic = {normalize_text(term) for term in GENERIC_RELEVANCE_ANCHORS}
    return bool((matched & generic) or find_terms(text, GENERIC_RELEVANCE_ANCHORS))


def has_regulator_policy_enforcement_context(article_or_text: Any) -> bool:
    text = article_or_text if isinstance(article_or_text, str) else _text(article_or_text)
    return has_any_term(text, _REGULATOR_POLICY_ENFORCEMENT_TERMS)


def is_corporate_macro_noise(article_or_text: Any, matched_hard: str | None = None) -> bool:
    text = article_or_text if isinstance(article_or_text, str) else _text(article_or_text)
    if has_domain_anchor(text, matched_hard):
        return False
    return has_any_term(text, _CORPORATE_MACRO_TERMS) and has_any_term(text, _CORPORATE_CONTEXT_TERMS)


def is_generic_market_or_ipo_noise(article_or_text: Any, matched_hard: str | None = None) -> bool:
    text = article_or_text if isinstance(article_or_text, str) else _text(article_or_text)
    if has_domain_anchor(text, matched_hard) or has_regulator_policy_enforcement_context(text):
        return False
    return has_only_generic_anchors(text, matched_hard) and has_any_term(text, _MARKET_OR_IPO_CONTEXT_TERMS)


def _get(article: Any, key: str) -> str:
    if isinstance(article, dict):
        return (article.get(key) or "").strip()
    return (getattr(article, key, "") or "").strip()


def _text(article: Any) -> str:
    title = _get(article, "title")
    summary = _get(article, "summary") or _get(article, "description")
    return f"{title}\n{summary}".strip()


def _matched_terms(article: Any) -> dict[str, str]:
    matched = score_matched_terms(article)
    return {
        "matched_hard": ";".join(matched["hard"]),
        "matched_soft": ";".join(matched["soft"]),
        "matched_negative": ";".join(matched["negative"]),
    }


def _rule_decide_relevance(
    *,
    score: int,
    min_score: int,
    matched_hard: str = "",
    matched_negative: str = "",
) -> tuple[bool, str]:
    if score >= min_score:
        return True, "rule_keep_score_ge_threshold"
    if matched_negative:
        return False, "rule_drop_negative_signal"
    if not matched_hard:
        return False, "rule_drop_no_financial_anchor"
    return False, "rule_drop_score_lt_threshold"


def _decide_relevance(
    *,
    score: int,
    prob: float | None,
    min_prob: float,
    min_score: int,
    matched_hard: str = "",
    matched_negative: str = "",
    article_text: str = "",
    model_policy: ModelPolicy = "authoritative",
    candidate_keep_prob: float = 0.65,
    candidate_drop_prob: float = 0.35,
    strong_rule_keep_score: int = 8,
    gray_keep_min_score: int = 6,
    no_model_keep_min_score: int = 5,
    model_keep_min_score: int = 5,
) -> tuple[bool, str]:
    if model_policy == "rule_only":
        return _rule_decide_relevance(
            score=score,
            min_score=min_score,
            matched_hard=matched_hard,
            matched_negative=matched_negative,
        )

    if model_policy == "candidate_hybrid":
        domain_anchor = has_domain_anchor(article_text, matched_hard)
        generic_only = has_only_generic_anchors(article_text, matched_hard)
        corporate_noise = is_corporate_macro_noise(article_text, matched_hard)
        market_noise = is_generic_market_or_ipo_noise(article_text, matched_hard)

        if matched_negative:
            return False, "candidate_hybrid_drop_negative_signal"
        if corporate_noise:
            return False, "candidate_hybrid_drop_corporate_macro_noise"
        if market_noise:
            return False, "candidate_hybrid_drop_generic_market_or_ipo_noise"
        if score >= strong_rule_keep_score and matched_hard and domain_anchor:
            return True, "candidate_hybrid_keep_strong_domain_rule_anchor"

        if prob is not None and prob >= candidate_keep_prob:
            if domain_anchor or (
                score >= model_keep_min_score and not generic_only
            ) or has_regulator_policy_enforcement_context(article_text):
                return True, "candidate_hybrid_model_keep_prob_ge_threshold_with_domain_anchor"
            return False, "candidate_hybrid_drop_model_keep_without_domain_anchor"
        if prob is not None and prob <= candidate_drop_prob:
            return False, "candidate_hybrid_model_drop_prob_le_threshold"
        if prob is not None:
            if score < gray_keep_min_score:
                return False, "candidate_hybrid_gray_drop_score_lt_threshold"
            if not domain_anchor:
                if generic_only:
                    return False, "candidate_hybrid_gray_drop_generic_anchor_only"
                return False, "candidate_hybrid_gray_drop_no_domain_anchor"
            return True, "candidate_hybrid_gray_keep_domain_score_ge_threshold"

        if score < no_model_keep_min_score:
            return False, "candidate_hybrid_no_model_drop_score_lt_threshold"
        if not domain_anchor:
            return False, "candidate_hybrid_no_model_drop_no_domain_anchor"
        return True, "candidate_hybrid_no_model_keep_domain_score_ge_threshold"

    if prob is not None:
        if prob >= min_prob:
            return True, "model_keep_prob_ge_threshold"
        return False, "model_drop_prob_lt_threshold"

    return _rule_decide_relevance(
        score=score,
        min_score=min_score,
        matched_hard=matched_hard,
        matched_negative=matched_negative,
    )


def _set_article_meta(
    article: Any,
    *,
    score: int,
    prob: float | None,
    keep: bool,
    decision_reason: str,
    matched: dict[str, str],
    model_policy: ModelPolicy,
    model_used: bool,
    candidate_keep_prob: float,
    candidate_drop_prob: float,
) -> None:
    label = "high" if score >= 8 else "med" if score >= 4 else "low"
    values = {
        "relevance_score": score,
        "score": score,
        "relevance_prob": prob,
        "prob": prob,
        "relevance_label": label,
        "relevance_bucket": label,
        "decision": "keep" if keep else "drop",
        "decision_reason": decision_reason,
        "keep": keep,
        "relevance_model_policy": model_policy,
        "model_used": model_used,
        "candidate_keep_prob": candidate_keep_prob,
        "candidate_drop_prob": candidate_drop_prob,
        **matched,
    }
    if isinstance(article, dict):
        article.update(values)
        return

    for key, value in values.items():
        setattr(article, key, value)


def _write_metrics(
    *,
    metrics_path: Path,
    date: str | None,
    model_policy: ModelPolicy,
    model_path: Path,
    model_used: bool,
    min_score: int,
    min_prob: float,
    candidate_keep_prob: float,
    candidate_drop_prob: float,
    strong_rule_keep_score: int,
    gray_keep_min_score: int,
    no_model_keep_min_score: int,
    input_count: int,
    kept_count: int,
    rows: list[dict[str, Any]],
) -> None:
    decision_reason_counts = Counter(str(row["decision_reason"]) for row in rows)
    payload = {
        "date": date,
        "model_policy": model_policy,
        "model_path": str(model_path),
        "model_used": model_used,
        "min_score": min_score,
        "min_prob": min_prob,
        "candidate_keep_prob": candidate_keep_prob,
        "candidate_drop_prob": candidate_drop_prob,
        "candidate_strong_rule_keep_score": strong_rule_keep_score,
        "candidate_gray_keep_min_score": gray_keep_min_score,
        "candidate_no_model_keep_min_score": no_model_keep_min_score,
        "input_count": input_count,
        "kept_count": kept_count,
        "dropped_count": input_count - kept_count,
        "decision_reason_counts": dict(sorted(decision_reason_counts.items())),
        "model_prob_available_count": sum(1 for row in rows if row["prob"] != ""),
        "model_prob_missing_count": sum(1 for row in rows if row["prob"] == ""),
        "candidate_hybrid_model_keep": decision_reason_counts[
            "candidate_hybrid_model_keep_prob_ge_threshold"
        ] + decision_reason_counts[
            "candidate_hybrid_model_keep_prob_ge_threshold_with_domain_anchor"
        ],
        "candidate_hybrid_model_keep_with_domain_anchor": decision_reason_counts[
            "candidate_hybrid_model_keep_prob_ge_threshold_with_domain_anchor"
        ],
        "candidate_hybrid_model_drop": decision_reason_counts[
            "candidate_hybrid_model_drop_prob_le_threshold"
        ],
        "candidate_hybrid_gray_rule_keep": decision_reason_counts[
            "candidate_hybrid_gray_keep_rule_score_ge_threshold"
        ] + decision_reason_counts[
            "candidate_hybrid_gray_keep_domain_score_ge_threshold"
        ],
        "candidate_hybrid_gray_keep_domain": decision_reason_counts[
            "candidate_hybrid_gray_keep_domain_score_ge_threshold"
        ],
        "candidate_hybrid_gray_rule_drop": decision_reason_counts[
            "candidate_hybrid_gray_drop_rule_score_lt_threshold"
        ] + decision_reason_counts[
            "candidate_hybrid_gray_drop_score_lt_threshold"
        ],
        "candidate_hybrid_gray_drop_no_domain_anchor": decision_reason_counts[
            "candidate_hybrid_gray_drop_no_domain_anchor"
        ],
        "candidate_hybrid_gray_drop_generic_anchor_only": decision_reason_counts[
            "candidate_hybrid_gray_drop_generic_anchor_only"
        ],
        "candidate_hybrid_strong_rule_keep": decision_reason_counts[
            "candidate_hybrid_keep_strong_rule_anchor"
        ] + decision_reason_counts[
            "candidate_hybrid_keep_strong_domain_rule_anchor"
        ],
        "candidate_hybrid_keep_strong_domain_rule_anchor": decision_reason_counts[
            "candidate_hybrid_keep_strong_domain_rule_anchor"
        ],
        "candidate_hybrid_drop_corporate_macro_noise": decision_reason_counts[
            "candidate_hybrid_drop_corporate_macro_noise"
        ],
        "candidate_hybrid_drop_generic_market_or_ipo_noise": decision_reason_counts[
            "candidate_hybrid_drop_generic_market_or_ipo_noise"
        ],
        "candidate_hybrid_no_model_keep_domain": decision_reason_counts[
            "candidate_hybrid_no_model_keep_domain_score_ge_threshold"
        ],
        "candidate_hybrid_no_model_drop": decision_reason_counts[
            "candidate_hybrid_no_model_drop_no_domain_anchor"
        ] + decision_reason_counts[
            "candidate_hybrid_no_model_drop_score_lt_threshold"
        ],
        "candidate_hybrid_negative_drop": decision_reason_counts[
            "candidate_hybrid_drop_negative_signal"
        ],
    }
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def filter_relevance(
    articles: list[Any],
    model_path: Path,
    out_candidates_csv: Path | None = None,
    min_prob: float = 0.60,
    min_score: int = 4,
    model_policy: ModelPolicy = "authoritative",
    candidate_keep_prob: float = 0.65,
    candidate_drop_prob: float = 0.35,
    strong_rule_keep_score: int = 8,
    gray_keep_min_score: int = 6,
    no_model_keep_min_score: int = 5,
    model_keep_min_score: int = 5,
    metrics_path: Path | None = None,
    metrics_date: str | None = None,
) -> list[Any]:
    if model_policy not in {"authoritative", "candidate_hybrid", "rule_only"}:
        raise ValueError(f"unsupported relevance model_policy: {model_policy}")
    if candidate_drop_prob >= candidate_keep_prob:
        raise ValueError("candidate_drop_prob must be less than candidate_keep_prob")

    model = None
    if model_policy != "rule_only":
        try:
            model = load_model(model_path)
        except Exception as exc:
            logger.warning(
                "Failed to load relevance model at %s; falling back to rules: %s",
                model_path,
                exc,
            )

    texts = [_text(a) for a in articles]
    scores = [relevance_score(a) for a in articles]

    probs = None
    if model is not None and model_policy != "rule_only":
        try:
            probs = predict_proba(model, texts)
        except Exception as exc:
            logger.warning(
                "Failed to score relevance model at %s; falling back to rules: %s",
                model_path,
                exc,
            )
            probs = None

    model_used = probs is not None
    kept: list[Any] = []
    rows = []

    for i, a in enumerate(articles):
        p = probs[i] if probs is not None else None
        s = scores[i]
        matched = _matched_terms(a)
        decision_policy: ModelPolicy = model_policy if (p is not None or model_policy == "candidate_hybrid") else "rule_only"
        keep, decision_reason = _decide_relevance(
            score=s,
            prob=p,
            min_prob=min_prob,
            min_score=min_score,
            matched_hard=matched["matched_hard"],
            matched_negative=matched["matched_negative"],
            article_text=texts[i],
            model_policy=decision_policy,
            candidate_keep_prob=candidate_keep_prob,
            candidate_drop_prob=candidate_drop_prob,
            strong_rule_keep_score=strong_rule_keep_score,
            gray_keep_min_score=gray_keep_min_score,
            no_model_keep_min_score=no_model_keep_min_score,
            model_keep_min_score=model_keep_min_score,
        )

        _set_article_meta(
            a,
            score=s,
            prob=p,
            keep=keep,
            decision_reason=decision_reason,
            matched=matched,
            model_policy=model_policy,
            model_used=model_used,
            candidate_keep_prob=candidate_keep_prob,
            candidate_drop_prob=candidate_drop_prob,
        )

        if keep:
            kept.append(a)

        prob_value = "" if p is None else round(p, 4)
        rows.append({
            "title": _get(a, "title"),
            "summary": _get(a, "summary") or _get(a, "description"),
            "url": _get(a, "url") or _get(a, "link"),
            "score": s,
            "prob": prob_value,
            "keep": int(keep),
            "decision": "keep" if keep else "drop",
            "decision_reason": decision_reason,
            "relevance_score": s,
            "relevance_prob": prob_value,
            "matched_hard": matched["matched_hard"],
            "matched_soft": matched["matched_soft"],
            "matched_negative": matched["matched_negative"],
            "relevance_model_policy": model_policy,
            "model_used": int(model_used),
            "candidate_keep_prob": candidate_keep_prob,
            "candidate_drop_prob": candidate_drop_prob,
        })

    if out_candidates_csv:
        out_candidates_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "title",
            "summary",
            "url",
            "score",
            "prob",
            "keep",
            "decision",
            "decision_reason",
            "relevance_score",
            "relevance_prob",
            "matched_hard",
            "matched_soft",
            "matched_negative",
            "relevance_model_policy",
            "model_used",
            "candidate_keep_prob",
            "candidate_drop_prob",
        ]
        with out_candidates_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    if metrics_path:
        try:
            _write_metrics(
                metrics_path=metrics_path,
                date=metrics_date,
                model_policy=model_policy,
                model_path=model_path,
                model_used=model_used,
                min_score=min_score,
                min_prob=min_prob,
                candidate_keep_prob=candidate_keep_prob,
                candidate_drop_prob=candidate_drop_prob,
                strong_rule_keep_score=strong_rule_keep_score,
                gray_keep_min_score=gray_keep_min_score,
                no_model_keep_min_score=no_model_keep_min_score,
                input_count=len(articles),
                kept_count=len(kept),
                rows=rows,
            )
        except Exception as exc:
            logger.warning("Failed to write relevance filter metrics to %s: %s", metrics_path, exc)

    return kept
