import importlib

import pytest

from open_deep_research.evaluation.gates import (
    EvaluationAuthorizationError,
    require_full_eval_authorization,
)


def test_full_eval_requires_both_environment_gates(monkeypatch):
    monkeypatch.delenv("ODR_EVAL_MODE", raising=False)
    monkeypatch.delenv("RUN_FULL_EVAL", raising=False)

    with pytest.raises(EvaluationAuthorizationError) as exc_info:
        require_full_eval_authorization()

    assert "ODR_EVAL_MODE=full" in str(exc_info.value)
    assert "RUN_FULL_EVAL=1" in str(exc_info.value)


def test_pairwise_import_does_not_start_comparison(monkeypatch):
    module = importlib.import_module("tests.pairwise_evaluation")
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("external comparison must not start")

    monkeypatch.setattr(module, "evaluate_comparative", forbidden)
    monkeypatch.delenv("ODR_EVAL_MODE", raising=False)
    monkeypatch.delenv("RUN_FULL_EVAL", raising=False)

    with pytest.raises(EvaluationAuthorizationError):
        module.main()
    assert not called


def test_marker_hook_skips_closed_cost_markers(monkeypatch):
    from tests import conftest

    class FakeConfig:
        def getoption(self, name):
            return False

    class FakeItem:
        def __init__(self, keyword):
            self.keywords = {keyword: True}
            self.markers = []

        def add_marker(self, marker):
            self.markers.append(marker)

    monkeypatch.delenv("ODR_EVAL_MODE", raising=False)
    monkeypatch.delenv("RUN_LIVE_RESEARCH", raising=False)
    monkeypatch.delenv("RUN_FULL_EVAL", raising=False)
    live_item = FakeItem("live")
    full_item = FakeItem("full_eval")

    conftest.pytest_collection_modifyitems(FakeConfig(), [live_item, full_item])

    assert len(live_item.markers) == 1
    assert len(full_item.markers) == 1
