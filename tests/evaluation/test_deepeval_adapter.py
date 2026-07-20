import importlib
import sys
import types

import pytest

from open_deep_research.evaluation.baseline import (
    create_replay_record,
    load_replay_fixture,
    select_case,
)


def _case_and_run():
    case = select_case("simple-001")
    fixture = load_replay_fixture(case)
    return case, create_replay_record(case, fixture, commit="a" * 40)


def test_module_import_does_not_import_deepeval(monkeypatch):
    sys.modules.pop("deepeval", None)
    sys.modules.pop("deepeval.test_case", None)

    module = importlib.import_module("open_deep_research.evaluation.deepeval_adapter")
    importlib.reload(module)

    assert "deepeval" not in sys.modules
    assert isinstance(module.is_deepeval_available(), bool)
    assert "deepeval" not in sys.modules


def test_missing_optional_dependency_has_clear_error(monkeypatch):
    import open_deep_research.evaluation.deepeval_adapter as adapter

    def missing(_name):
        raise adapter.metadata.PackageNotFoundError

    monkeypatch.setattr(adapter.metadata, "version", missing)
    case, run = _case_and_run()

    with pytest.raises(adapter.DeepEvalUnavailableError, match="optional"):
        adapter.to_deepeval_case(case, run)


def test_lazy_conversion_uses_public_test_case_contract(monkeypatch):
    import open_deep_research.evaluation.deepeval_adapter as adapter

    class FakeLLMTestCase:
        def __init__(self, *, input, actual_output, **kwargs):
            self.import_environment = {
                "api_key": adapter.os.environ.get("CONFIDENT_API_KEY"),
                "telemetry_opt_out": adapter.os.environ.get(
                    "DEEPEVAL_TELEMETRY_OPT_OUT"
                ),
                "legacy_keyfile_disabled": adapter.os.environ.get(
                    "DEEPEVAL_DISABLE_LEGACY_KEYFILE"
                ),
            }
            adapter.os.environ["GRPC_VERBOSITY"] = "simulated-deepeval-change"
            adapter.os.environ["GRPC_TRACE"] = "simulated-deepeval-change"
            self.input = input
            self.actual_output = actual_output
            for name, value in kwargs.items():
                setattr(self, name, value)

    package = types.ModuleType("deepeval")
    test_case_module = types.ModuleType("deepeval.test_case")
    test_case_module.LLMTestCase = FakeLLMTestCase
    monkeypatch.setitem(sys.modules, "deepeval", package)
    monkeypatch.setitem(sys.modules, "deepeval.test_case", test_case_module)
    monkeypatch.setattr(
        adapter.metadata, "version", lambda _name: adapter.EXPECTED_DEEPEVAL_VERSION
    )
    monkeypatch.setenv("CONFIDENT_API_KEY", "must-not-be-visible-during-import")
    monkeypatch.delenv("GRPC_VERBOSITY", raising=False)
    monkeypatch.delenv("GRPC_TRACE", raising=False)
    case, run = _case_and_run()

    converted = adapter.to_deepeval_case(case, run)

    assert converted.input == case.prompt
    assert converted.actual_output == run.output
    assert converted.name == case.id
    assert converted.token_cost is None
    assert converted.completion_time == run.telemetry.wall_time_ms / 1000
    assert converted.metadata["tokens"]["total"] == run.telemetry.total_tokens
    assert converted.metadata["artifact_ref_count"] == 1
    assert getattr(converted, "tools_called", None) is None
    assert converted.import_environment == {
        "api_key": None,
        "telemetry_opt_out": "1",
        "legacy_keyfile_disabled": "1",
    }
    assert adapter.os.environ["CONFIDENT_API_KEY"] == "must-not-be-visible-during-import"
    assert "GRPC_VERBOSITY" not in adapter.os.environ
    assert "GRPC_TRACE" not in adapter.os.environ


def test_version_mismatch_is_rejected_before_import(monkeypatch):
    import open_deep_research.evaluation.deepeval_adapter as adapter

    monkeypatch.setattr(adapter.metadata, "version", lambda _name: "9.9.9")
    case, run = _case_and_run()

    with pytest.raises(adapter.DeepEvalUnavailableError, match="expects 4.1.1"):
        adapter.to_deepeval_case(case, run)


@pytest.mark.skipif(
    importlib.util.find_spec("deepeval") is None,
    reason="optional eval extra is not installed in this environment",
)
@pytest.mark.full_eval
def test_installed_pinned_deepeval_conversion():
    from open_deep_research.evaluation.deepeval_adapter import to_deepeval_case

    case, run = _case_and_run()
    converted = to_deepeval_case(case, run)
    assert converted.input == case.prompt
