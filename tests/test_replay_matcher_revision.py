"""Revision-specific matcher preparation and state isolation."""
from __future__ import annotations

from copy import deepcopy

import pytest

from scripts import evaluate_cluster_review as evaluation
from src.pipeline import text_matcher


def row(title: str) -> dict:
    return {'title': title, 'summary': '', 'url': 'https://example.test/replay',
            'keep': 1, 'score': 6, 'prob': .8}


def test_base_state_is_historical_and_restored() -> None:
    before = deepcopy((text_matcher._TERM_ALIASES, text_matcher._ALIAS_MATCH_MODES))
    config = evaluation.matcher_configuration(evaluation.BASE)
    assert set(config['_TERM_ALIASES']) == {'cp', '킥스'}
    assert config['_ALIAS_MATCH_MODES'] == {}
    with evaluation.matcher_state(evaluation.BASE):
        assert not text_matcher.contains_term('불법 대부 피해', '불법대부')
    assert (text_matcher._TERM_ALIASES, text_matcher._ALIAS_MATCH_MODES) == before
    assert text_matcher.contains_term('불법 대부를 적발', '불법대부')
    assert not evaluation.prepare([row('불법 대부 피해')], revision=evaluation.BASE, recorded=False)
    assert evaluation.prepare([row('불법 대부 피해')], recorded=False)
    assert (text_matcher._TERM_ALIASES, text_matcher._ALIAS_MATCH_MODES) == before


@pytest.mark.parametrize('modes,expected', [({}, True), ({'불법 대부': 'right_token'}, False),
                                        ({'불법 대부': 'korean_particle'}, True)])
def test_revision_modes_are_literal_not_current_defaults(
    monkeypatch: pytest.MonkeyPatch, modes: dict, expected: bool,
) -> None:
    aliases = {'불법대부': ('불법 대부',)}
    source = f'_TERM_ALIASES: dict = {aliases!r}\n'
    if modes:
        source += f'_ALIAS_MATCH_MODES = {modes!r}\n'
    # Only literals are read: no historical module import/side effect is allowed.
    source += 'raise AssertionError("historical source must not execute")\n'
    monkeypatch.setattr(evaluation.subprocess, 'check_output', lambda *a, **kw: source)
    before = deepcopy((text_matcher._TERM_ALIASES, text_matcher._ALIAS_MATCH_MODES))
    for _ in range(2):
        with evaluation.matcher_state('test-revision'):
            assert text_matcher.contains_term('불법 대부를 적발', '불법대부') is expected
        assert (text_matcher._TERM_ALIASES, text_matcher._ALIAS_MATCH_MODES) == before
        assert text_matcher.contains_term('불법 대부를 적발', '불법대부')


def test_revision_state_restores_after_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    before = deepcopy((text_matcher._TERM_ALIASES, text_matcher._ALIAS_MATCH_MODES))
    monkeypatch.setattr(evaluation.subprocess, 'check_output', lambda *a, **kw: '_TERM_ALIASES = {}')
    with pytest.raises(RuntimeError):
        with evaluation.matcher_state('test-revision'):
            raise RuntimeError('failed replay')
    assert (text_matcher._TERM_ALIASES, text_matcher._ALIAS_MATCH_MODES) == before


def test_base_vs_base_has_identical_tags_clusters_and_pairs() -> None:
    rows = [row('불법 대부 피해'), row('미등록 대부 영업'), row('대부업권 채권 매입')]
    a = evaluation.prepare(rows, revision=evaluation.BASE)
    b = evaluation.prepare(rows, revision=evaluation.BASE)
    assert [(t.article.link, t.sectors) for t in a] == [(t.article.link, t.sectors) for t in b]
    assert evaluation.measure(evaluation.snapshot(evaluation.BASE), a) == evaluation.measure(
        evaluation.snapshot(evaluation.BASE), b)
