"""Deterministic acceptance validator for completed development phases."""

from __future__ import annotations

import argparse
import ast
import asyncio
import configparser
import importlib
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from open_deep_research.evaluation.baseline import (  # noqa: E402
    create_replay_record,
    live_authorization_refusal,
    load_cases,
    load_replay_fixture,
    run_replay,
    select_case,
)
from open_deep_research.evaluation.manifest import sha256_file  # noqa: E402
from open_deep_research.evaluation.models import (  # noqa: E402
    BaselineRunRecord,
    RunStatus,
)
from open_deep_research.evaluation.storage import load_jsonl  # noqa: E402
from open_deep_research.evaluation.telemetry import (  # noqa: E402
    ainvoke_with_evaluation_telemetry,
)


EXPECTED_REFERENCE_IDS = {
    "paper-qa",
    "deepeval",
    "langmem",
    "langgraph",
    "mcp-servers",
}
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class CheckResult:
    """One phase acceptance result."""

    acceptance_id: str
    passed: bool
    detail: str


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _check_refs(root: Path, refs_lock_path: Path) -> str:
    lock = _read_json(refs_lock_path)
    if lock.get("schema_version") != "1.0":
        raise ValueError("refs lock schema_version must be 1.0")
    repositories = lock.get("repositories")
    if not isinstance(repositories, list) or len(repositories) != 5:
        raise ValueError("refs lock repositories must be a list")
    if not all(isinstance(item, dict) for item in repositories):
        raise ValueError("every reference entry must be an object")
    reference_ids = [item.get("id") for item in repositories]
    local_paths = [item.get("local_path") for item in repositories]
    if set(reference_ids) != EXPECTED_REFERENCE_IDS or len(set(reference_ids)) != 5:
        raise ValueError(f"reference ids differ or repeat: {reference_ids}")
    if len(set(local_paths)) != 5 or None in local_paths:
        raise ValueError("reference local_path values must be unique and non-null")

    modules = configparser.ConfigParser()
    modules.read(root / ".gitmodules", encoding="utf-8")
    module_entries = {
        values["path"]: values["url"]
        for section in modules.sections()
        for values in [dict(modules.items(section))]
    }

    for item in repositories:
        for field in ("id", "url", "local_path", "commit", "license", "acquisition"):
            if field not in item:
                raise ValueError(f"reference {item.get('id')} missing {field}")
        if not COMMIT_PATTERN.fullmatch(item["commit"]):
            raise ValueError(f"reference {item['id']} has invalid commit")
        license_data = item["license"]
        for field in ("spdx", "status", "file", "sha256"):
            if not license_data.get(field):
                raise ValueError(f"reference {item['id']} license missing {field}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(license_data["sha256"])):
            raise ValueError(f"reference {item['id']} has invalid license digest")
        acquisition = item["acquisition"]
        if acquisition.get("strategy") != "git-submodule" or not isinstance(
            acquisition.get("shallow"), bool
        ):
            raise ValueError(f"reference {item['id']} has invalid acquisition contract")

        local_path = item["local_path"].replace("\\", "/")
        if module_entries.get(local_path) != item["url"]:
            raise ValueError(f".gitmodules mismatch for {item['id']}")
        staged = _git(root, "ls-files", "--stage", "--", local_path)
        fields = staged.split()
        if len(fields) < 2 or fields[0] != "160000" or fields[1] != item["commit"]:
            raise ValueError(f"gitlink mismatch for {item['id']}: {staged}")

        checkout = root / local_path
        if checkout.exists():
            top_level = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            initialized = bool(
                top_level.returncode == 0
                and Path(top_level.stdout.strip()).resolve() == checkout.resolve()
            )
            if initialized:
                local_head = _git(checkout, "rev-parse", "HEAD")
                if local_head != item["commit"]:
                    raise ValueError(
                        f"local HEAD mismatch for {item['id']}: {local_head}"
                    )
                license_path = checkout / license_data["file"]
                if not license_path.is_file():
                    raise ValueError(f"license file missing for {item['id']}")
                if sha256_file(license_path) != license_data["sha256"]:
                    raise ValueError(f"license digest mismatch for {item['id']}")
    return "five reference URLs, gitlinks, local HEADs, and licenses match lock"


def _check_dataset(cases_path: Path) -> str:
    cases = load_cases(cases_path)
    counts = Counter(case.difficulty.value for case in cases)
    if counts != Counter({"simple": 3, "medium": 3, "complex": 3}):
        raise ValueError(f"unexpected difficulty counts: {dict(counts)}")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("duplicate case id")
    return "dataset has 3 simple, 3 medium, and 3 complex unique cases"


def _top_level_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports


def _check_offline_imports(root: Path) -> str:
    prohibited = {"deepeval", "langsmith", "dotenv"}
    for relative in (
        "scripts/run_baseline.py",
        "src/open_deep_research/evaluation/baseline.py",
        "src/open_deep_research/evaluation/__init__.py",
    ):
        imported = _top_level_import_roots(root / relative)
        found = imported & prohibited
        if found:
            raise ValueError(f"{relative} eagerly imports {sorted(found)}")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import open_deep_research.evaluation; "
                "raise SystemExit(1 if 'deepeval' in sys.modules else 0)"
            ),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise ValueError("production evaluation import loaded DeepEval")
    return "smoke/replay imports are inert and do not load external evaluation clients"


def _replay_record(
    root: Path, cases_path: Path, fixture_path: Path, manifest: dict
):
    case = select_case("simple-001", cases_path)
    fixture = load_replay_fixture(case, fixture_path.parent)
    return case, create_replay_record(
        case, fixture, commit=manifest["phase_start_commit"]
    )


def _check_replay(root: Path, cases_path: Path, fixture_path: Path, manifest: dict) -> str:
    case = select_case("simple-001", cases_path)
    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    output = temporary_root / f"replay-{uuid4().hex}.jsonl"
    try:
        record = run_replay(
            case.id,
            output,
            cases_path=cases_path,
            fixtures_dir=fixture_path.parent,
            commit=manifest["phase_start_commit"],
        )
        loaded = load_jsonl(output, BaselineRunRecord)
        if loaded != [record]:
            raise ValueError("persisted replay did not round-trip")
        if record.telemetry.status is not RunStatus.COMPLETED:
            raise ValueError("replay did not complete")
        if not record.output or not all(metric.passed for metric in record.metrics):
            raise ValueError("replay output or deterministic metrics failed")
        if record.case_id != case.id:
            raise ValueError("replay case linkage mismatch")
    finally:
        output.unlink(missing_ok=True)
    return "simple-001 replay produces a complete, reloadable schema record"


def _check_live_refusal(root: Path) -> str:
    refusal = live_authorization_refusal(
        "simple-001", confirm_cost=False, environment={}
    )
    if refusal is None or refusal.status != "not_run_no_authorization":
        raise ValueError("live authorization did not refuse")
    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    output = temporary_root / f"unauthorized-{uuid4().hex}.jsonl"
    environment = os.environ.copy()
    for key in ("ODR_EVAL_MODE", "RUN_LIVE_RESEARCH"):
        environment.pop(key, None)
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "run_baseline.py"),
            "--mode",
            "live",
            "--case",
            "simple-001",
            "--output",
            str(output),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 3 or output.exists():
        raise ValueError(
            f"unauthorized live CLI returned {completed.returncode} or wrote output"
        )
    event = json.loads(completed.stderr.strip())
    if event.get("status") != "not_run_no_authorization":
        raise ValueError("live CLI refusal status missing")
    return "live runner refuses before output with structured status and exit code 3"


def _check_disabled_wrapper() -> str:
    class IdentityRunnable:
        def __init__(self) -> None:
            self.input_value = None
            self.config = None

        async def ainvoke(self, input_value, config):
            self.input_value = input_value
            self.config = config
            return input_value

    runnable = IdentityRunnable()
    input_value = {"messages": ["identity"]}
    config = {"configurable": {"existing": True}}
    result = asyncio.run(
        ainvoke_with_evaluation_telemetry(
            runnable, input_value, config, enabled=False
        )
    )
    if result is not input_value or runnable.config is not config:
        raise ValueError("disabled wrapper changed input/output/config identity")
    return "telemetry defaults off and delegates without changing invocation objects"


def _check_pairwise_import(root: Path) -> str:
    tree = ast.parse(
        (root / "tests" / "pairwise_evaluation.py").read_text(encoding="utf-8")
    )
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            function = node.value.func
            if isinstance(function, ast.Name) and function.id == "evaluate_comparative":
                raise ValueError("evaluate_comparative remains at module scope")
    if not any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and "__main__" in ast.unparse(node.test)
        for node in tree.body
    ):
        raise ValueError("pairwise evaluator has no __main__ gate")
    return "pairwise comparison has explicit full-eval and main gates"


def _check_optional_deepeval(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as source:
        pyproject = tomllib.load(source)
    optional = pyproject["project"]["optional-dependencies"].get("eval", [])
    if optional != ["deepeval==4.1.1"]:
        raise ValueError(f"unexpected eval extra: {optional}")
    module = importlib.import_module("open_deep_research.evaluation.deepeval_adapter")
    if not hasattr(module, "EvaluationAdapter"):
        raise ValueError("EvaluationAdapter missing")
    return "DeepEval 4.1.1 is optional and deterministic metrics remain project-owned"


def _commit_exists(root: Path, commit: str) -> bool:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _commit_is_ancestor(root: Path, commit: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _check_manifest(root: Path, manifest: dict) -> str:
    required = {
        "schema_version",
        "captured_at",
        "phase_start_commit",
        "project_commit_at_capture",
        "phase_start_worktree_clean",
        "worktree_clean_at_capture",
        "runtime",
        "project_contract",
        "resolved_packages",
        "configuration_defaults",
        "configuration_drift",
        "protected_core_sha256",
    }
    missing = required - manifest.keys()
    if missing:
        raise ValueError(f"manifest missing fields: {sorted(missing)}")
    phase_start_commit = str(manifest["phase_start_commit"])
    if not _commit_exists(root, phase_start_commit):
        raise ValueError("manifest phase_start_commit is not an existing commit object")
    if not _commit_is_ancestor(root, phase_start_commit):
        raise ValueError("manifest phase_start_commit is not an ancestor of HEAD")
    drift = {item.get("field"): item for item in manifest["configuration_drift"]}
    for field in ("allow_clarification", "print_process_info", "model_defaults"):
        if field not in drift or "[ASK USER]" not in drift[field].get("decision", ""):
            raise ValueError(f"manifest does not record unresolved drift for {field}")
    project_contract = manifest["project_contract"]
    if project_contract.get("requires_python_at_phase_start") != ">=3.10":
        raise ValueError("manifest does not preserve the Phase 0 starting Python range")
    if project_contract.get("requires_python") != ">=3.11":
        raise ValueError("manifest Python target is not >=3.11")
    expected_packages = [
        "open_deep_research",
        "open_deep_research.evaluation",
        "legacy",
        "tests",
    ]
    if project_contract.get("setuptools_packages") != expected_packages:
        raise ValueError("manifest does not record explicit evaluation package discovery")

    with (root / "pyproject.toml").open("rb") as source:
        pyproject = tomllib.load(source)
    if pyproject["project"]["requires-python"] != project_contract["requires_python"]:
        raise ValueError("manifest Python contract differs from pyproject.toml")
    if (
        pyproject["project"]["optional-dependencies"].get("eval", [])
        != project_contract["optional_eval_dependencies"]
    ):
        raise ValueError("manifest eval extra differs from pyproject.toml")
    current_packages = pyproject["tool"]["setuptools"]["packages"]
    if not set(expected_packages).issubset(current_packages):
        raise ValueError("pyproject.toml lost a Phase 0 package")

    defaults = manifest["configuration_defaults"]
    expected_default_pairs = {
        "allow_clarification": (False, True),
        "print_process_info": (True, False),
    }
    for field, (runtime_default, ui_default) in expected_default_pairs.items():
        values = defaults.get(field, {})
        if values.get("runtime_default") is not runtime_default:
            raise ValueError(f"manifest runtime default mismatch for {field}")
        if values.get("ui_default") is not ui_default:
            raise ValueError(f"manifest UI default mismatch for {field}")
    for field in (
        "summarization_model",
        "research_model",
        "compression_model",
        "final_report_model",
    ):
        values = defaults.get(field, {})
        if values.get("runtime_default_source") != "environment_at_import":
            raise ValueError(f"manifest model default source missing for {field}")
        if "environment_override_present" not in values:
            raise ValueError(f"manifest model environment presence missing for {field}")

    resolved = manifest["resolved_packages"]
    for package in (
        "open_deep_research",
        "langgraph",
        "langchain-core",
        "langchain",
        "langsmith",
        "pydantic",
        "pytest",
        "deepeval",
    ):
        if package not in resolved or not (
            resolved[package] is None or isinstance(resolved[package], str)
        ):
            raise ValueError(f"manifest resolved package missing/invalid: {package}")
    runtime = manifest["runtime"]
    if not str(runtime.get("python_version", "")).startswith("3.11."):
        raise ValueError("manifest runtime is not Python 3.11.x")
    if runtime.get("conda_environment") != "open-deep-research":
        raise ValueError("manifest target conda environment mismatch")
    return "actual defaults, resolved packages, and three unresolved drifts are recorded"


def _check_measurements(
    root: Path, cases_path: Path, fixture_path: Path, manifest: dict
) -> str:
    _, record = _replay_record(root, cases_path, fixture_path, manifest)
    telemetry = record.telemetry
    if (
        telemetry.input_tokens is None
        or telemetry.output_tokens is None
        or telemetry.total_tokens is None
        or telemetry.wall_time_ms <= 0
        or not telemetry.tool_calls_by_name
        or telemetry.estimated_cost is not None
    ):
        raise ValueError("token/cost/time/tool dimensions are not independently represented")
    return "record separates tokens, null cost, wall time, and per-tool counts"


def _check_validator_inputs(root: Path, manifest: dict) -> str:
    if manifest.get("schema_version") != "1.0":
        raise ValueError("manifest schema is not accepted")
    commit = str(manifest.get("phase_start_commit", ""))
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("manifest phase_start_commit is absent or malformed")
    if not _commit_exists(root, commit):
        raise ValueError("manifest phase_start_commit is not present in the repository")
    if not _commit_is_ancestor(root, commit):
        raise ValueError("manifest phase_start_commit is not an ancestor of HEAD")
    return "validator enforces required fields and exact commit integrity"


def _check_protected_core(root: Path, manifest: dict) -> str:
    expected = manifest.get("protected_core_sha256", {})
    required = (
        "src/open_deep_research/deep_researcher.py",
        "src/open_deep_research/prompts.py",
        "src/open_deep_research/utils.py",
    )
    for relative in required:
        if expected.get(relative) != sha256_file(root / relative):
            raise ValueError(f"protected core changed: {relative}")
    return "deep_researcher.py, prompts.py, and utils.py match Phase 0 start hashes"


def _run_check(acceptance_id: str, check: Callable[[], str]) -> CheckResult:
    try:
        return CheckResult(acceptance_id, True, check())
    except BaseException as exc:
        return CheckResult(
            acceptance_id,
            False,
            f"{type(exc).__name__}: {exc}",
        )


def validate_phase0(
    root: Path,
    *,
    refs_lock_path: Path,
    cases_path: Path,
    fixture_path: Path,
    manifest_path: Path,
) -> list[CheckResult]:
    """Evaluate every T0 acceptance criterion without network or paid calls."""
    try:
        manifest = _read_json(manifest_path)
    except BaseException as exc:
        detail = f"{type(exc).__name__}: {exc}"
        return [CheckResult(f"T0-{index}", False, detail) for index in range(1, 13)]

    checks: list[tuple[str, Callable[[], str]]] = [
        ("T0-1", lambda: _check_refs(root, refs_lock_path)),
        ("T0-2", lambda: _check_dataset(cases_path)),
        ("T0-3", lambda: _check_offline_imports(root)),
        ("T0-4", lambda: _check_replay(root, cases_path, fixture_path, manifest)),
        ("T0-5", lambda: _check_live_refusal(root)),
        ("T0-6", _check_disabled_wrapper),
        ("T0-7", lambda: _check_pairwise_import(root)),
        ("T0-8", lambda: _check_optional_deepeval(root)),
        ("T0-9", lambda: _check_manifest(root, manifest)),
        ("T0-10", lambda: _check_measurements(root, cases_path, fixture_path, manifest)),
        ("T0-11", lambda: _check_validator_inputs(root, manifest)),
        ("T0-12", lambda: _check_protected_core(root, manifest)),
    ]
    return [_run_check(acceptance_id, check) for acceptance_id, check in checks]


def _check_phase1_test_suite(root: Path) -> str:
    def load_test_module(name: str, relative: str):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load contract test module: {relative}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    contract = load_test_module(
        "phase1_contract_tests",
        "tests/integration/storage/test_repository_contract.py",
    )
    persistence = load_test_module(
        "phase1_persistence_tests",
        "tests/integration/storage/test_sqlite_and_blob_persistence.py",
    )
    state_reducer = load_test_module(
        "phase1_state_reducer_tests",
        "tests/integration/storage/test_state_reducer_integration.py",
    )
    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary = temporary_root / f"direct-phase1-{uuid4().hex}"
    temporary.mkdir()
    contract.test_same_repository_contract_suite_covers_both_backends(
        temporary / "memory-contract", "memory"
    )
    contract.test_same_repository_contract_suite_covers_both_backends(
        temporary / "sqlite-contract", "sqlite"
    )
    contract.test_backends_have_identical_stable_observable_contract(
        temporary / "backend-equivalence"
    )
    contract.test_scope_isolation_applies_to_metadata_and_blob_dedupe(
        temporary / "scope-isolation"
    )
    contract.test_private_scope_requires_matching_trusted_user(
        temporary / "private-scope"
    )
    for test_name in (
        "test_sqlite_reopen_and_original_blob_survive_source_overwrite",
        "test_two_sqlite_writers_create_one_version_and_one_create_audit",
        "test_missing_foreign_keys_fail_and_schema_version_is_rejected",
    ):
        directory = temporary / test_name
        directory.mkdir(parents=True)
        getattr(persistence, test_name)(directory)
    state_reducer.test_parallel_researcher_reference_updates_merge_deterministically()
    return (
        "shared InMemory/SQLite contract scenarios, persistence, concurrency, "
        "scope, soft-delete, and blob snapshot probes passed"
    )


def _check_phase1_locators() -> str:
    from open_deep_research.knowledge.models import (
        Chunk,
        ChunkInput,
        ChunkLocatorType,
    )

    values = (
        ChunkInput(
            ordinal=0,
            text="page",
            locator_type=ChunkLocatorType.PAGE,
            page_start=2,
            page_end=3,
        ),
        ChunkInput(
            ordinal=1,
            text="heading",
            locator_type=ChunkLocatorType.HEADING,
            heading_path=("Architecture", "Storage"),
        ),
    )
    for value in values:
        chunk = Chunk(
            **value.model_dump(), scope_id="scope_validator", version_id="ver_validator"
        )
        if Chunk.model_validate_json(chunk.model_dump_json()) != chunk:
            raise ValueError("chunk locator failed JSON round-trip")
    return "page and heading locators round-trip as typed fields"


def _check_phase1_reducer() -> str:
    from open_deep_research.evidence.reducers import stable_id_reducer

    left = stable_id_reducer(["src_b", "evd_a"], ["src_a", "src_b"])
    right = stable_id_reducer(["src_a"], ["src_b", "evd_a", "src_b"])
    batched = stable_id_reducer(
        stable_id_reducer([], ["src_b"]), ["evd_a", "src_a", "src_b"]
    )
    expected = ["evd_a", "src_a", "src_b"]
    if left != expected or right != expected or batched != expected:
        raise ValueError("stable ID reducer depends on order or batching")
    return "stable ID reducer is deduplicating, sorted, and batching-invariant"


def _check_phase1_packaging(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as source:
        pyproject = tomllib.load(source)
    if pyproject["project"]["requires-python"] != ">=3.11":
        raise ValueError("Python contract is not >=3.11")
    required_packages = {
        "open_deep_research.knowledge",
        "open_deep_research.evidence",
        "open_deep_research.storage",
        "open_deep_research.storage.migrations",
    }
    packages = set(pyproject["tool"]["setuptools"]["packages"])
    if not required_packages.issubset(packages):
        raise ValueError("new subpackages are absent from setuptools package list")
    prohibited = (
        "sqlalchemy",
        "chromadb",
        "faiss",
        "qdrant",
        "pinecone",
        "pgvector",
    )
    dependencies = [item.lower() for item in pyproject["project"]["dependencies"]]
    if any(item.startswith(prohibited) for item in dependencies):
        raise ValueError("Phase 1 introduced an ORM or vector database dependency")
    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from open_deep_research.knowledge.models import Source; "
                "from open_deep_research.evidence.models import Evidence; "
                "from open_deep_research.storage.sqlite import SCHEMA_VERSION"
            ),
        ],
        cwd=temporary_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise ValueError(f"outside-repository import failed: {probe.stderr.strip()}")
    return "Python >=3.11, package discovery/import smoke, and dependency boundary pass"


def _check_phase1_disabled_compatibility(root: Path) -> str:
    from open_deep_research.configuration import Configuration
    from open_deep_research.state import ResearcherOutputState

    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    directory = temporary_root / f"disabled-phase1-{uuid4().hex}"
    directory.mkdir()
    previous = Path.cwd()
    os.chdir(directory)
    try:
        configuration = Configuration()
        if configuration.enable_structured_evidence is not False:
            raise ValueError("structured evidence is not disabled by default")
        if Path("data").exists():
            raise ValueError("disabled configuration created storage")
    finally:
        os.chdir(previous)
    output = ResearcherOutputState(
        compressed_research="legacy", raw_notes=["legacy-note"]
    )
    if output.compressed_research != "legacy" or output.raw_notes != ["legacy-note"]:
        raise ValueError("legacy ResearcherOutputState fields changed")
    return "default-off configuration creates no DB and legacy free-text state remains valid"


def _check_phase1_schema() -> str:
    from open_deep_research.evidence.models import EvidenceValidationStatus
    from open_deep_research.knowledge.models import VersionLifecycleStatus
    from open_deep_research.storage.migrations import MIGRATION_V1
    from open_deep_research.storage.sqlite import SCHEMA_VERSION

    required_tables = (
        "knowledge_scopes",
        "sources",
        "documents",
        "content_blobs",
        "document_versions",
        "chunks",
        "requirements",
        "evidence",
        "audit_events",
    )
    if SCHEMA_VERSION < 1 or any(name not in MIGRATION_V1 for name in required_tables):
        raise ValueError("migration v1 is incomplete")
    if {item.value for item in VersionLifecycleStatus} != {
        "candidate",
        "active",
        "stale",
        "superseded",
        "quarantined",
        "archived",
    }:
        raise ValueError("DocumentVersion lifecycle vocabulary changed")
    if {item.value for item in EvidenceValidationStatus} != {
        "pending",
        "validated",
        "rejected",
    }:
        raise ValueError("Evidence validation vocabulary changed")
    return (
        "historical migration v1 remains intact under the current schema, and "
        "required tables/status vocabularies pass"
    )


def _check_phase1_citation_eligibility() -> str:
    from datetime import UTC, datetime, timedelta

    from open_deep_research.evidence.models import (
        Evidence,
        EvidenceValidationStatus,
        is_evidence_citable,
    )
    from open_deep_research.knowledge.models import (
        Chunk,
        Document,
        DocumentVersion,
        Source,
        SourceKind,
        VersionLifecycleStatus,
    )

    now = datetime.now(UTC)
    source = Source(
        scope_id="scope_validator",
        kind=SourceKind.WEB,
        canonical_uri="https://example.com/validator",
        display_name="Validator",
    )
    document = Document(
        scope_id="scope_validator",
        source_id=source.source_id,
        logical_key="validator",
        title="Validator",
        media_type="text/plain",
    )
    version = DocumentVersion(
        scope_id="scope_validator",
        document_id=document.document_id,
        blob_id="blob_validator",
        content_sha256="a" * 64,
        version_number=1,
        retrieved_at=now,
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
        lifecycle_status=VersionLifecycleStatus.ACTIVE,
    )
    chunk = Chunk(
        scope_id="scope_validator",
        version_id=version.version_id,
        ordinal=0,
        text="support",
    )
    evidence = Evidence(
        scope_id="scope_validator",
        chunk_id=chunk.chunk_id,
        excerpt="support",
        confidence=1,
        retrieval_method="validator",
        validation_status=EvidenceValidationStatus.VALIDATED,
    )
    if not is_evidence_citable(
        evidence, chunk, version, document, source, at=now
    ):
        raise ValueError("active+validated chain is not citable")
    pending = evidence.model_copy(
        update={"validation_status": EvidenceValidationStatus.PENDING}
    )
    candidate = version.model_copy(
        update={"lifecycle_status": VersionLifecycleStatus.CANDIDATE}
    )
    if is_evidence_citable(
        pending, chunk, version, document, source, at=now
    ) or (
        is_evidence_citable(
            evidence, chunk, candidate, document, source, at=now
        )
    ):
        raise ValueError("citation eligibility ignored version/evidence status")
    return "citation eligibility requires active Version and validated Evidence"


def validate_phase1(root: Path) -> list[CheckResult]:
    """Evaluate every T1 criterion using deterministic local evidence only."""
    try:
        suite_detail = _check_phase1_test_suite(root)
        suite_error: BaseException | None = None
    except BaseException as exc:
        suite_detail = ""
        suite_error = exc

    def contract_evidence(focus: str) -> str:
        if suite_error is not None:
            raise suite_error
        return f"{focus}; {suite_detail}"

    checks: list[tuple[str, Callable[[], str]]] = [
        ("T1-1", lambda: contract_evidence("duplicate source/content returns one version ID")),
        ("T1-2", lambda: contract_evidence("changed bytes create monotonic immutable versions")),
        ("T1-3", lambda: contract_evidence("Evidence foreign keys trace to Source; missing refs fail")),
        ("T1-4", _check_phase1_locators),
        ("T1-5", _check_phase1_reducer),
        ("T1-6", lambda: contract_evidence("SQLite reopen preserves rows, ordering, delete, and audit")),
        ("T1-7", lambda: contract_evidence("two SQLite writers converge through transaction/UNIQUE")),
        ("T1-8", lambda: contract_evidence("Source/Version/Chunk/Evidence deletion is soft and audited")),
        ("T1-9", lambda: _check_phase1_packaging(root)),
        ("T1-10", lambda: _check_phase1_disabled_compatibility(root)),
        ("T1-11", lambda: contract_evidence("one parameterized contract suite covers both backends")),
        ("T1-12", _check_phase1_schema),
        ("T1-13", lambda: contract_evidence("blob dedupe is scope-local while source chains stay distinct")),
        ("T1-14", lambda: contract_evidence("snapshot remains readable after source overwrite")),
        ("T1-15", _check_phase1_citation_eligibility),
        ("T1-16", lambda: contract_evidence("tenant/project/private and cross-scope access fail closed")),
    ]
    return [_run_check(acceptance_id, check) for acceptance_id, check in checks]


PHASE2_TEST_TARGETS = (
    "tests/unit/knowledge",
    "tests/unit/tools/test_knowledge_tools.py",
    "tests/integration/knowledge",
    "tests/integration/storage/test_phase2_repository_contract.py",
)


def _check_phase2_test_suite(root: Path) -> str:
    """Run the complete deterministic Phase 2 suite in the current interpreter."""
    missing = [
        relative
        for relative in PHASE2_TEST_TARGETS
        if not (root / relative).exists()
    ]
    if missing:
        raise ValueError(f"Phase 2 test targets are missing: {missing}")

    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    basetemp = temporary_root / f"phase2-pytest-{uuid4().hex}"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *PHASE2_TEST_TARGETS,
            "-m",
            "not live",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(basetemp),
            "-q",
            "-rs",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    combined_output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode != 0:
        tail = "\n".join(combined_output.splitlines()[-30:])
        raise ValueError(
            f"Phase 2 pytest suite exited {completed.returncode}:\n{tail}"
        )
    if re.search(r"\b\d+\s+skipped\b", combined_output):
        raise ValueError(
            "Phase 2 pytest suite skipped coverage despite the compatible "
            "dependency gate"
        )
    passed_match = re.search(r"\b(\d+) passed\b", completed.stdout)
    if passed_match is None:
        raise ValueError("Phase 2 pytest suite did not report a passing test count")
    return (
        f"{passed_match.group(1)} tests passed with zero skips using a unique "
        ".phase-validation-tmp basetemp"
    )


def _phase2_test_inventory(root: Path) -> dict[str, set[str]]:
    inventory: dict[str, set[str]] = {}
    paths: list[Path] = []
    for relative in PHASE2_TEST_TARGETS:
        target = root / relative
        paths.extend(sorted(target.rglob("test_*.py")) if target.is_dir() else [target])
    for path in paths:
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        inventory[relative] = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        }
    return inventory


def _require_phase2_tests(
    inventory: dict[str, set[str]], requirements: dict[str, tuple[str, ...]]
) -> str:
    missing: list[str] = []
    for relative, names in requirements.items():
        available = inventory.get(relative, set())
        missing.extend(f"{relative}::{name}" for name in names if name not in available)
    if missing:
        raise ValueError(f"Phase 2 acceptance evidence tests are missing: {missing}")
    count = sum(len(names) for names in requirements.values())
    return f"{count} mapped acceptance test(s) are present"


def _run_phase2_dependency_gate(root: Path) -> tuple[dict, str]:
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_phase2_dependencies.py"),
            "--json",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        output = completed.stdout.strip() or completed.stderr.strip()
        raise ValueError(
            "Phase 2 dependency gate exited "
            f"{completed.returncode}: {output}"
        )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Phase 2 dependency gate did not emit valid JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("Phase 2 dependency report must be a JSON object")
    expected = report.get("expected_versions")
    installed = report.get("installed")
    environment = report.get("environment", {})
    offline = report.get("offline_settings", {})
    adapter = report.get("adapter_static_check", {})
    if (
        report.get("status") != "compatible"
        or report.get("exit_code") != 0
        or report.get("errors") != []
        or report.get("missing_distributions") != []
        or report.get("network_used") is not False
        or report.get("installation_attempted") is not False
        or environment.get("windows_ok") is not True
        or environment.get("python_ok") is not True
        or offline.get("ok") is not True
        or adapter.get("ok") is not True
        or adapter.get("findings") != []
        or not isinstance(expected, dict)
        or not isinstance(installed, dict)
        or any(
            installed.get(name, {}).get("actual") != version
            or installed.get(name, {}).get("ok") is not True
            for name, version in expected.items()
        )
    ):
        raise ValueError(f"Phase 2 dependency report is not compatible: {report}")
    versions = ", ".join(
        f"{name}={version}" for name, version in sorted(expected.items())
    )
    return report, (
        f"compatible on {environment.get('system')} Python {environment.get('python')}; "
        f"offline/no-install/no-network; {versions}"
    )


def _check_phase2_adapter_boundary(report: dict) -> str:
    adapter = report.get("adapter_static_check", {})
    if adapter.get("ok") is not True or adapter.get("findings") != []:
        raise ValueError("PaperQA adapter references forbidden answer/Agent APIs")
    offline = report.get("offline_settings", {})
    required_offline = {
        "use_doc_details": False,
        "defer_embedding": True,
        "parse_media": False,
        "enrich_media": False,
        "evidence_skip_summary": True,
    }
    if any(offline.get(key) is not value for key, value in required_offline.items()):
        raise ValueError(f"PaperQA offline settings drifted: {offline}")
    return "adapter AST excludes ask/aquery/agents and offline enrichment is disabled"


def _check_phase2_packaging(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as source:
        pyproject = tomllib.load(source)
    required_packages = {
        "open_deep_research.knowledge",
        "open_deep_research.knowledge.ingestion",
        "open_deep_research.knowledge.ingestion.parsers",
        "open_deep_research.knowledge.retrieval",
        "open_deep_research.evidence",
        "open_deep_research.storage",
        "open_deep_research.storage.migrations",
        "open_deep_research.tools",
    }
    configured_packages = set(pyproject["tool"]["setuptools"]["packages"])
    missing = required_packages - configured_packages
    if missing:
        raise ValueError(f"Phase 2 packages are absent from setuptools: {sorted(missing)}")

    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    directory = temporary_root / f"phase2-package-import-{uuid4().hex}"
    directory.mkdir()
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, sys; "
                "import open_deep_research.knowledge.ingestion.service; "
                "import open_deep_research.knowledge.ingestion.parsers; "
                "import open_deep_research.knowledge.retrieval; "
                "import open_deep_research.knowledge.paperqa_adapter; "
                "import open_deep_research.tools.knowledge; "
                "raise SystemExit(1 if 'paperqa' in sys.modules "
                "or pathlib.Path('data').exists() else 0)"
            ),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if probe.returncode != 0:
        raise ValueError(
            "Phase 2 package import failed outside the repository cwd: "
            f"{probe.stderr.strip() or probe.stdout.strip()}"
        )
    return "Phase 2 package set is complete and imports inertly outside repository cwd"


def _check_phase2_default_off(root: Path) -> str:
    from open_deep_research.configuration import Configuration

    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    directory = temporary_root / f"phase2-default-off-{uuid4().hex}"
    directory.mkdir()
    paperqa_before = {
        name
        for name in sys.modules
        if name == "paperqa" or name.startswith("paperqa.")
    }
    previous = Path.cwd()
    os.chdir(directory)
    try:
        configuration = Configuration()
        expected_false = (
            "enable_knowledge_base",
            "enable_paperqa_retrieval",
            "paperqa_contextual_summarization",
        )
        enabled = [
            name
            for name in expected_false
            if getattr(configuration, name) is not False
        ]
        if enabled:
            raise ValueError(f"Phase 2 configuration defaults are enabled: {enabled}")
        if Path("data").exists():
            raise ValueError("default-off Configuration created a data directory")
    finally:
        os.chdir(previous)
    paperqa_after = {
        name
        for name in sys.modules
        if name == "paperqa" or name.startswith("paperqa.")
    }
    if paperqa_after - paperqa_before:
        raise ValueError("default-off Configuration imported PaperQA")
    return "knowledge/PaperQA/contextual flags default off and create no data or import"


def _check_phase2_production_isolation(root: Path) -> str:
    protected = (
        "src/open_deep_research/deep_researcher.py",
        "src/open_deep_research/utils.py",
    )
    manifest = _read_json(root / "tests" / "baseline" / "baseline_manifest.json")
    expected_hashes = manifest.get("protected_core_sha256", {})
    forbidden_imports: list[str] = []
    for relative in protected:
        path = root / relative
        if expected_hashes.get(relative) != sha256_file(path):
            raise ValueError(
                f"production core differs from the Phase 0 baseline: {relative}"
            )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for name in imported:
                if name == "paperqa" or name.startswith("paperqa.") or name == (
                    "open_deep_research.knowledge"
                ) or name.startswith("open_deep_research.knowledge."):
                    forbidden_imports.append(f"{relative}:{name}")
    if forbidden_imports:
        raise ValueError(
            f"production core imports Phase 2 internals: {forbidden_imports}"
        )

    utils_tree = ast.parse(
        (root / "src/open_deep_research/utils.py").read_text(encoding="utf-8")
    )
    get_all_tools = next(
        (
            node
            for node in utils_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "get_all_tools"
        ),
        None,
    )
    if get_all_tools is None:
        raise ValueError("get_all_tools is missing")
    identifiers = {
        node.id
        for node in ast.walk(get_all_tools)
        if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(get_all_tools)
        if isinstance(node, ast.Attribute)
    }
    bound = identifiers & {"knowledge_search", "knowledge_read"}
    if bound:
        raise ValueError(
            f"production get_all_tools binds Phase 2 tools: {sorted(bound)}"
        )
    return "deep_researcher/utils match baseline and bind no knowledge/PaperQA API"


def validate_phase2(root: Path) -> list[CheckResult]:
    """Evaluate every T2 criterion using deterministic, offline local evidence."""
    try:
        suite_detail = _check_phase2_test_suite(root)
        suite_error: BaseException | None = None
    except BaseException as exc:
        suite_detail = ""
        suite_error = exc
    try:
        inventory = _phase2_test_inventory(root)
        inventory_error: BaseException | None = None
    except BaseException as exc:
        inventory = {}
        inventory_error = exc
    try:
        dependency_report, dependency_detail = _run_phase2_dependency_gate(root)
        dependency_error: BaseException | None = None
    except BaseException as exc:
        dependency_report = {}
        dependency_detail = ""
        dependency_error = exc

    def evidence(
        focus: str,
        requirements: dict[str, tuple[str, ...]],
        extra: Callable[[], str] | None = None,
    ) -> str:
        if suite_error is not None:
            raise suite_error
        if inventory_error is not None:
            raise inventory_error
        mapped = _require_phase2_tests(inventory, requirements)
        details = [focus, mapped, suite_detail]
        if extra is not None:
            details.append(extra())
        return "; ".join(details)

    def dependency_evidence() -> str:
        if dependency_error is not None:
            raise dependency_error
        return dependency_detail

    ingestion = "tests/integration/knowledge/test_ingestion_service.py"
    parsers = "tests/unit/knowledge/test_ingestion_parsers.py"
    retrieval = "tests/unit/knowledge/test_retrieval_and_paperqa_adapter.py"
    tools = "tests/unit/tools/test_knowledge_tools.py"
    cli = "tests/integration/knowledge/test_cli_workflow.py"
    native = "tests/integration/knowledge/test_native_paperqa_offline.py"
    dependency = "tests/unit/knowledge/test_phase2_dependency_smoke.py"
    configuration = "tests/unit/knowledge/test_configuration_and_state.py"
    checks: list[tuple[str, Callable[[], str]]] = [
        (
            "T2-1",
            lambda: evidence(
                "four byte-backed formats import and persist as candidates",
                {
                    ingestion: (
                        "test_four_formats_persist_candidate_chunks_and_pending_context_evidence",
                        "test_reopened_repository_retrieves_pdf_markdown_and_html_locators",
                    ),
                    cli: ("test_cli_import_reopen_and_candidate_inspection",),
                },
            ),
        ),
        (
            "T2-2",
            lambda: evidence(
                "PDF page ranges retain the Version/Source chain",
                {
                    parsers: (
                        "test_pdf_parser_preserves_one_indexed_and_cross_page_locators",
                    ),
                    ingestion: (
                        "test_four_formats_persist_candidate_chunks_and_pending_context_evidence",
                        "test_reopened_repository_retrieves_pdf_markdown_and_html_locators",
                    ),
                },
            ),
        ),
        (
            "T2-3",
            lambda: evidence(
                "Markdown chunks retain deterministic heading paths",
                {
                    parsers: (
                        "test_markdown_parser_preserves_hierarchy_repeats_fences_and_crlf",
                        "test_markdown_chunking_is_deterministic_and_phase1_compatible",
                    ),
                    ingestion: (
                        "test_reopened_repository_retrieves_pdf_markdown_and_html_locators",
                    ),
                },
            ),
        ),
        (
            "T2-4",
            lambda: evidence(
                "HTML chunks bind canonical snapshot identity and anchors",
                {
                    parsers: (
                        "test_html_parser_removes_executable_content_and_preserves_snapshot_identity",
                        "test_html_input_canonical_uri_overrides_snapshot_hint_without_fetching",
                    ),
                    ingestion: (
                        "test_source_rewrite_and_deletion_keep_original_blob_and_locator",
                        "test_reopened_repository_retrieves_pdf_markdown_and_html_locators",
                    ),
                },
            ),
        ),
        (
            "T2-5",
            lambda: evidence(
                "empty repository, no-match, and zero-similarity paths return explicit empty hits",
                {
                    retrieval: (
                        "test_repository_retriever_empty_and_deterministic_tie_break",
                        "test_native_paperqa_zero_similarity_is_an_explicit_empty_result",
                    ),
                    tools: (
                        "test_knowledge_search_returns_typed_artifact_and_explicit_empty",
                    ),
                },
            ),
        ),
        (
            "T2-6",
            lambda: evidence(
                "raw retrieval trace and AST exclude a second answer/Agent loop",
                {
                    native: (
                        "test_native_paperqa_rehydrates_repository_with_local_embedding_only",
                    ),
                    retrieval: (
                        "test_native_paperqa_seam_only_manual_loads_and_retrieves",
                        "test_contextual_backend_is_opt_in_bounded_and_order_preserving",
                    ),
                    dependency: ("test_adapter_static_check_rejects_answer_and_agent_apis",),
                },
                lambda: _check_phase2_adapter_boundary(dependency_report)
                if dependency_error is None
                else dependency_evidence(),
            ),
        ),
        (
            "T2-7",
            lambda: evidence(
                "same bytes are idempotent; changed bytes version; derived indexing retries",
                {
                    ingestion: (
                        "test_duplicate_content_is_idempotent_and_changed_content_creates_new_version",
                        "test_index_failure_retries_without_promoting_or_duplicating_version",
                    )
                },
            ),
        ),
        (
            "T2-8",
            lambda: evidence(
                "PaperQA results preserve project IDs and deterministic score/ID order",
                {
                    retrieval: (
                        "test_repository_retriever_empty_and_deterministic_tie_break",
                        "test_paperqa_adapter_prefilters_postfilters_and_uses_project_ids",
                        "test_paperqa_adapter_deduplicates_and_sorts_by_score_then_chunk_id",
                        "test_native_paperqa_seam_only_manual_loads_and_retrieves",
                    )
                },
            ),
        ),
        (
            "T2-9",
            lambda: evidence(
                "Phase 2 remains default-off and production tools/Web flow remain baseline",
                {
                    configuration: (
                        "test_structured_evidence_defaults_off_without_creating_storage",
                    ),
                    retrieval: (
                        "test_disabled_paperqa_uses_repository_without_loading_module",
                    ),
                    tools: ("test_phase2_contract_is_not_registered_with_production_tools",),
                },
                lambda: (
                    f"{_check_phase2_default_off(root)}; "
                    f"{_check_phase2_production_isolation(root)}"
                ),
            ),
        ),
        (
            "T2-10",
            lambda: evidence(
                "parse/index failures are structured, retryable, and never activate a Version",
                {
                    ingestion: (
                        "test_structured_failed_job_retries_under_same_job_id",
                        "test_index_failure_retries_without_promoting_or_duplicating_version",
                    ),
                    parsers: (
                        "test_pdf_parser_rejects_invalid_and_image_only_documents",
                        "test_empty_text_snapshots_fail_instead_of_generating_content",
                    ),
                    retrieval: (
                        "test_paperqa_failure_is_structured_or_explicitly_falls_back",
                        "test_contextual_backend_reports_timeout_and_exception",
                    ),
                },
            ),
        ),
        (
            "T2-11",
            lambda: evidence(
                "knowledge_read accepts stable Repository IDs and rejects local paths",
                {
                    retrieval: (
                        "test_read_uses_stable_chunk_or_evidence_id_and_citation_chain",
                        "test_search_and_read_requests_are_strict_and_read_rejects_paths",
                    ),
                    tools: ("test_tool_requests_forbid_extra_fields_and_paths",),
                },
            ),
        ),
        (
            "T2-12",
            lambda: evidence(
                "Windows/Python 3.11 dependency matrix and native imports are compatible",
                {
                    dependency: (
                        "test_fully_compatible_report_is_deterministic_without_network",
                        "test_project_knowledge_extra_has_the_complete_exact_matrix",
                        "test_missing_dependencies_are_structured_not_skipped",
                    )
                },
                lambda: f"{dependency_evidence()}; {_check_phase2_packaging(root)}",
            ),
        ),
        (
            "T2-13",
            lambda: evidence(
                "candidate/pending evidence stays non-citable and inspection-only",
                {
                    ingestion: (
                        "test_four_formats_persist_candidate_chunks_and_pending_context_evidence",
                        "test_index_failure_retries_without_promoting_or_duplicating_version",
                    ),
                    cli: ("test_cli_import_reopen_and_candidate_inspection",),
                    tools: (
                        "test_candidate_search_and_read_require_service_capability",
                        "test_phase2_contract_is_not_registered_with_production_tools",
                    ),
                },
            ),
        ),
        (
            "T2-14",
            lambda: evidence(
                "deleted source remains recoverable from hashed ContentBlob with locator",
                {
                    ingestion: (
                        "test_four_formats_persist_candidate_chunks_and_pending_context_evidence",
                        "test_source_rewrite_and_deletion_keep_original_blob_and_locator",
                    )
                },
            ),
        ),
        (
            "T2-15",
            lambda: evidence(
                "fresh native PaperQA state rehydrates authoritative scope-filtered records",
                {
                    native: (
                        "test_native_paperqa_rehydrates_repository_with_local_embedding_only",
                    ),
                    retrieval: (
                        "test_repository_retriever_scope_filters_as_of_and_candidate",
                        "test_paperqa_adapter_prefilters_postfilters_and_uses_project_ids",
                        "test_paperqa_adapter_deduplicates_and_sorts_by_score_then_chunk_id",
                        "test_retrievers_fail_closed_when_catalog_leaks_another_scope",
                    ),
                    cli: (
                        "test_cli_paperqa_opt_in_rehydrates_without_an_answer_agent",
                    ),
                },
            ),
        ),
    ]
    return [_run_check(acceptance_id, check) for acceptance_id, check in checks]


def build_parser() -> argparse.ArgumentParser:
    """Build the phase validator CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, required=True, choices=(0, 1, 2))
    parser.add_argument("--refs-lock", type=Path)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a phase validator and print an auditable result per acceptance ID."""
    args = build_parser().parse_args(argv)
    if args.phase == 0:
        results = validate_phase0(
            PROJECT_ROOT,
            refs_lock_path=(
                args.refs_lock or PROJECT_ROOT / "doc/reference/refs.lock.json"
            ),
            cases_path=(args.cases or PROJECT_ROOT / "tests/baseline/cases.jsonl"),
            fixture_path=(
                args.fixture
                or PROJECT_ROOT / "tests/baseline/fixtures/simple-001.replay.json"
            ),
            manifest_path=(
                args.manifest or PROJECT_ROOT / "tests/baseline/baseline_manifest.json"
            ),
        )
    elif args.phase == 1:
        results = validate_phase1(PROJECT_ROOT)
    else:
        results = validate_phase2(PROJECT_ROOT)
    if args.as_json:
        sys.stdout.write(
            json.dumps(
                [result.__dict__ for result in results], ensure_ascii=False, indent=2
            )
            + "\n"
        )
    else:
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            sys.stdout.write(f"{status} {result.acceptance_id}: {result.detail}\n")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
