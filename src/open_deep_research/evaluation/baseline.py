"""Replay-first baseline execution and explicit live authorization gates."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from open_deep_research.evaluation.metrics import evaluate_smoke
from open_deep_research.evaluation.models import (
    AuthorizationRefusal,
    BaselineCase,
    BaselineRunRecord,
    NetworkPolicy,
    ReplayFixture,
    RunMode,
    RunStatus,
    RunTelemetry,
)
from open_deep_research.evaluation.storage import append_jsonl_atomic, load_jsonl
from open_deep_research.evaluation.telemetry import (
    EvaluationTelemetryCollector,
    ainvoke_with_evaluation_telemetry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = PROJECT_ROOT / "tests" / "baseline" / "cases.jsonl"
DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "tests" / "baseline" / "fixtures"
_KNOWN_EXTERNAL_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "LANGSMITH_API_KEY",
)
_LIVE_CONFIG_FIELDS = (
    "max_structured_output_retries",
    "allow_clarification",
    "print_process_info",
    "max_concurrent_research_units",
    "max_concurrent_researcher_tool_calls",
    "search_api",
    "max_queries_per_search_call",
    "max_results_per_tavily",
    "max_researcher_iterations",
    "max_react_tool_calls",
    "summarization_model",
    "summarization_model_max_tokens",
    "max_content_length",
    "research_model",
    "research_model_max_tokens",
    "compression_model",
    "compression_model_max_tokens",
    "final_report_model",
    "final_report_model_max_tokens",
)


class LiveAuthorizationError(PermissionError):
    """Refuse a live execution before importing or constructing external clients."""

    def __init__(self, refusal: AuthorizationRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


def load_cases(path: str | Path = DEFAULT_CASES_PATH) -> list[BaselineCase]:
    """Load the complete baseline dataset."""
    cases = load_jsonl(path, BaselineCase)
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("baseline case ids must be globally unique")
    return cases


def select_case(case_id: str, path: str | Path = DEFAULT_CASES_PATH) -> BaselineCase:
    """Resolve exactly one stable case ID."""
    for case in load_cases(path):
        if case.id == case_id:
            return case
    raise KeyError(f"unknown baseline case: {case_id}")


def load_replay_fixture(
    case: BaselineCase,
    fixtures_dir: str | Path = DEFAULT_FIXTURES_DIR,
) -> ReplayFixture:
    """Load the fixture whose case and version match the dataset contract."""
    path = Path(fixtures_dir) / f"{case.id}.replay.json"
    fixture = ReplayFixture.model_validate_json(path.read_text(encoding="utf-8"))
    if fixture.case_id != case.id:
        raise ValueError(f"fixture case mismatch: {fixture.case_id} != {case.id}")
    if fixture.fixture_version != case.fixture_version:
        raise ValueError(
            f"fixture version mismatch: {fixture.fixture_version} != {case.fixture_version}"
        )
    return fixture


def project_commit(project_root: str | Path = PROJECT_ROOT) -> str:
    """Return the commit containing the baseline starting point."""
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def create_replay_record(
    case: BaselineCase,
    fixture: ReplayFixture,
    *,
    commit: str,
) -> BaselineRunRecord:
    """Convert one committed observation into the common run schema."""
    record = BaselineRunRecord(
        run_id=f"replay-{case.id}-{uuid4().hex}",
        case_id=case.id,
        mode=RunMode.REPLAY,
        project_commit=commit,
        config_snapshot={
            "evaluation_telemetry_enabled": False,
            "network_access": False,
            "source": "committed_replay",
        },
        output=fixture.output,
        telemetry=fixture.telemetry,
        artifact_refs=[f"tests/baseline/fixtures/{case.id}.replay.json"],
        created_at=datetime.now(timezone.utc),
        fixture_version=fixture.fixture_version,
        telemetry_source="fixture",
    )
    return record.model_copy(update={"metrics": evaluate_smoke(case, record)})


def run_replay(
    case_id: str,
    output_path: str | Path,
    *,
    cases_path: str | Path = DEFAULT_CASES_PATH,
    fixtures_dir: str | Path = DEFAULT_FIXTURES_DIR,
    commit: str | None = None,
) -> BaselineRunRecord:
    """Run a deterministic, fully offline replay and atomically persist it."""
    case = select_case(case_id, cases_path)
    fixture = load_replay_fixture(case, fixtures_dir)
    record = create_replay_record(case, fixture, commit=commit or project_commit())
    append_jsonl_atomic(output_path, record, BaselineRunRecord)
    return record


def live_authorization_refusal(
    case_id: str,
    *,
    confirm_cost: bool,
    environment: dict[str, str] | None = None,
) -> AuthorizationRefusal | None:
    """Return a refusal unless all independent live-cost gates are open."""
    active_environment = environment if environment is not None else os.environ
    missing: list[str] = []
    if active_environment.get("ODR_EVAL_MODE", "smoke").lower() != "live":
        missing.append("ODR_EVAL_MODE=live")
    if active_environment.get("RUN_LIVE_RESEARCH") != "1":
        missing.append("RUN_LIVE_RESEARCH=1")
    if not confirm_cost:
        missing.append("--confirm-cost")
    if not missing:
        return None
    return AuthorizationRefusal(
        case_id=case_id,
        missing_gates=missing,
        message=(
            "Live baseline was not started. Explicit mode, environment, and CLI "
            "cost confirmation are all required."
        ),
    )


def _safe_live_config_snapshot(case: BaselineCase) -> dict[str, Any]:
    from open_deep_research.configuration import Configuration

    configuration = Configuration.from_runnable_config()
    values: dict[str, Any] = {}
    for field_name in _LIVE_CONFIG_FIELDS:
        value = getattr(configuration, field_name)
        values[field_name] = getattr(value, "value", value)
    return {
        "evaluation_telemetry_enabled": True,
        "network_policy": case.network_policy.value,
        "configuration": values,
        "external_credentials_present": {
            name: bool(os.environ.get(name)) for name in _KNOWN_EXTERNAL_KEYS
        },
    }


def _extract_graph_output(state: Any) -> str | None:
    if not isinstance(state, dict):
        return str(state) if state is not None else None
    final_report = state.get("final_report")
    if final_report:
        return str(final_report)
    messages = state.get("messages") or []
    if not messages:
        return None
    last_message = messages[-1]
    if isinstance(last_message, dict):
        return str(last_message.get("content") or "") or None
    return str(getattr(last_message, "content", "")) or None


async def run_live_authorized(
    case: BaselineCase,
    output_path: str | Path,
    *,
    commit: str | None = None,
    confirm_cost: bool = False,
    environment: dict[str, str] | None = None,
    _runnable: Any | None = None,
    _message_factory: Any | None = None,
) -> BaselineRunRecord:
    """Run one already-authorized live case and persist success or failure.

    This function deliberately performs the costly imports. Callers must run
    ``live_authorization_refusal`` before invoking it.
    """
    refusal = live_authorization_refusal(
        case.id,
        confirm_cost=confirm_cost,
        environment=environment,
    )
    if refusal is not None:
        raise LiveAuthorizationError(refusal)
    if case.network_policy is not NetworkPolicy.LIVE_ALLOWED:
        raise ValueError(f"case {case.id} is offline_only")

    if _runnable is None:
        from dotenv import load_dotenv
        from langchain_core.messages import HumanMessage

        load_dotenv(PROJECT_ROOT / ".env")
        from open_deep_research.deep_researcher import deep_researcher

        runnable = deep_researcher
        message_factory = HumanMessage
    else:
        runnable = _runnable
        message_factory = _message_factory or (lambda content: content)

    collector = EvaluationTelemetryCollector()
    result: Any = None
    caught: BaseException | None = None
    try:
        result = await ainvoke_with_evaluation_telemetry(
            runnable,
            {"messages": [message_factory(content=case.prompt)]},
            enabled=True,
            collector=collector,
        )
    except BaseException as exc:
        caught = exc

    telemetry = collector.telemetry
    if telemetry is None:
        raise RuntimeError("authorized live run ended without telemetry") from caught
    output = _extract_graph_output(result)
    if telemetry.status is RunStatus.COMPLETED and not output:
        telemetry = RunTelemetry.model_validate(
            {
                **telemetry.model_dump(),
                "status": RunStatus.FAILED,
                "error_type": "MissingOutputError",
            }
        )
        caught = RuntimeError("live graph completed without a report or message output")
    record = BaselineRunRecord(
        run_id=f"live-{case.id}-{uuid4().hex}",
        case_id=case.id,
        mode=RunMode.LIVE,
        project_commit=commit or project_commit(),
        config_snapshot=_safe_live_config_snapshot(case),
        output=output,
        telemetry=telemetry,
        artifact_refs=[],
        created_at=datetime.now(timezone.utc),
        fixture_version=None,
        telemetry_source="callback",
    )
    if telemetry.status is RunStatus.COMPLETED:
        record = record.model_copy(update={"metrics": evaluate_smoke(case, record)})
    append_jsonl_atomic(output_path, record, BaselineRunRecord)
    if caught is not None:
        raise caught.with_traceback(caught.__traceback__)
    return record
