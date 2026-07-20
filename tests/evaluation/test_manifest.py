import json
from pathlib import Path

from open_deep_research.evaluation import manifest


def test_environment_derived_model_default_is_redacted(monkeypatch):
    private_value = "private-deployment-name-must-not-leak"
    monkeypatch.setenv("RESEARCH_MODEL", private_value)

    defaults = manifest._config_defaults()
    serialized = json.dumps(defaults)

    assert private_value not in serialized
    assert defaults["research_model"]["runtime_default"] == (
        "[ENV_OVERRIDE_REDACTED]"
    )
    assert defaults["research_model"]["environment_override_present"] is True
    assert defaults["research_model"]["runtime_default_source"] == (
        "environment_at_import"
    )


def test_committed_manifest_contains_no_absolute_python_path():
    path = Path(__file__).parents[1] / "baseline" / "baseline_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["capture_process"]["python_executable_recorded"] is False
    assert "CONDA_PREFIX" not in json.dumps(payload)
