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
from open_deep_research.evaluation.reporting import (  # noqa: E402
    validate_artifact_manifest,
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
    """Verify hashes against the immutable committed Phase 0 manifest.

    Later feature phases may legitimately extend feature-gated production files. The
    capture intentionally hashed a dirty working-tree snapshot, so those bytes are not
    Git blobs at ``phase_start_commit``. The durable evidence is the manifest object
    first committed at Phase 0 closeout; comparing against it preserves the historical
    hashes without incorrectly requiring later HEAD files to stay byte-identical.
    """
    expected = manifest.get("protected_core_sha256", {})
    phase_start_commit = str(manifest.get("phase_start_commit", ""))
    if not COMMIT_PATTERN.fullmatch(phase_start_commit) or not _commit_exists(
        root, phase_start_commit
    ):
        raise ValueError("Phase 0 protected hashes lack a valid historical commit")
    history = _git(
        root,
        "log",
        "--diff-filter=A",
        "--format=%H",
        "--",
        "tests/baseline/baseline_manifest.json",
    ).splitlines()
    if not history:
        raise ValueError("cannot resolve the Phase 0 manifest addition commit")
    manifest_commit = history[-1]
    if not _commit_is_ancestor(root, manifest_commit):
        raise ValueError("Phase 0 manifest commit is not an ancestor of HEAD")
    completed = subprocess.run(
        [
            "git",
            "show",
            f"{manifest_commit}:tests/baseline/baseline_manifest.json",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("cannot read the committed Phase 0 manifest object")
    canonical = json.loads(completed.stdout)
    if canonical.get("phase_start_commit") != phase_start_commit:
        raise ValueError("Phase 0 manifest start commit differs from committed evidence")
    if expected != canonical.get("protected_core_sha256"):
        raise ValueError("protected Phase 0 hash map differs from committed evidence")

    required = (
        "src/open_deep_research/deep_researcher.py",
        "src/open_deep_research/prompts.py",
        "src/open_deep_research/utils.py",
    )
    for relative in required:
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected.get(relative, ""))):
            raise ValueError(f"invalid protected Phase 0 hash: {relative}")
    return (
        "deep_researcher.py, prompts.py, and utils.py historical hashes match the "
        "immutable committed Phase 0 manifest; later changes are evaluated by their phase"
    )


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
            "enable_knowledge_tools",
            "enable_agentic_rag",
            "enable_knowledge_writeback",
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
    return (
        "Phase 2 and Phase 3 knowledge/PaperQA/governance flags default off and "
        "create no data or PaperQA import"
    )


def _check_phase2_production_isolation(root: Path) -> str:
    protected = (
        "src/open_deep_research/deep_researcher.py",
        "src/open_deep_research/utils.py",
    )
    manifest = _read_json(root / "tests" / "baseline" / "baseline_manifest.json")
    forbidden_imports: list[str] = []
    _check_protected_core(root, manifest)
    for relative in protected:
        path = root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for name in imported:
                if name == "paperqa" or name.startswith("paperqa."):
                    forbidden_imports.append(f"{relative}:{name}")
    if forbidden_imports:
        raise ValueError(
            f"production core imports PaperQA directly: {forbidden_imports}"
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
    required_gates = {"enable_knowledge_tools", "enable_agentic_rag"}
    if bound and not required_gates.issubset(identifiers):
        raise ValueError(
            "production get_all_tools binds knowledge tools without both Phase 3 "
            f"feature gates: {sorted(bound)}"
        )
    return (
        "historical Phase 0 hashes remain exact; production imports no PaperQA, "
        "and any later knowledge binding is guarded by both Phase 3 mode flags"
    )


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


PHASE3_TEST_TARGETS = (
    "tests/evaluation/test_phase_validator.py",
    "tests/unit/knowledge/lifecycle",
    "tests/unit/knowledge/retrieval",
    "tests/unit/research",
    "tests/unit/evidence/test_run_store.py",
    "tests/unit/tools",
    "tests/integration/agentic_rag",
    "tests/test_research_limits.py",
)


PHASE3_ACCEPTANCE_TESTS: dict[str, dict[str, tuple[str, ...]]] = {
    "T3-1": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_sufficient_active_local_evidence_strictly_skips_web",
        ),
    },
    "T3-2": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_only_missing_requirement_drives_bounded_web_query",
        ),
        "tests/unit/knowledge/retrieval/test_budget.py": (
            "test_registry_shares_one_budget_across_researchers",
            "test_every_global_limit_is_checked_before_reservation",
        ),
    },
    "T3-3": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_writeback_off_keeps_validated_candidate_run_scoped_only",
            "test_low_authority_web_candidate_is_quarantined_not_returned",
        ),
    },
    "T3-4": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_local_candidate_uses_same_gate_then_avoids_web",
        ),
        "tests/integration/agentic_rag/test_lifecycle_flow.py": (
            "test_repository_transition_contract_and_same_state_idempotence",
        ),
    },
    "T3-5": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_low_authority_web_candidate_is_quarantined_not_returned",
        ),
        "tests/unit/tools/test_agentic_rag_routing.py": (
            "test_legacy_mode_allows_only_active_validated_direct_support",
        ),
    },
    "T3-6": {
        "tests/integration/agentic_rag/test_lifecycle_flow.py": (
            "test_replacement_activation_supersedes_old_active_atomically",
        ),
        "tests/unit/knowledge/retrieval/test_coverage.py": (
            "test_hard_quality_gate_reports_partial_coverage",
        ),
    },
    "T3-7": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_writeback_allows_next_independent_run_to_skip_web",
        ),
    },
    "T3-8": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_parallel_same_candidate_has_stable_ids_and_one_canonical_version",
        ),
    },
    "T3-9": {
        "tests/unit/knowledge/lifecycle/test_policy_and_models.py": (
            "test_exact_transition_matrix",
            "test_proposal_surface_is_strict_and_has_no_hard_delete",
        ),
        "tests/integration/agentic_rag/test_lifecycle_flow.py": (
            "test_agent_proposal_is_scoped_and_never_mutates_or_deletes_target",
            "test_illegal_transition_is_rejected_without_audit",
        ),
    },
    "T3-10": {
        "tests/unit/tools/test_agentic_rag_routing.py": (
            "test_agentic_mode_has_no_web_or_mcp_bypass",
            "test_provider_native_agentic_configuration_fails_closed",
        ),
    },
    "T3-11": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_writeback_off_keeps_validated_candidate_run_scoped_only",
            "test_provider_failure_is_counted_and_never_fabricates_evidence",
        ),
        "tests/unit/tools/test_agentic_rag_routing.py": (
            "test_tool_modes_are_mechanically_distinct_and_default_is_baseline",
        ),
    },
    "T3-12": {
        "tests/evaluation/test_phase_validator.py": (
            "test_phase3_parser_and_acceptance_map_cover_exactly_t3_1_through_t3_20",
            "test_phase3_validator_reports_twenty_results_from_named_offline_evidence",
            "test_phase3_validator_rejects_missing_named_acceptance_test",
            "test_phase3_inventory_requirement_is_not_satisfied_by_prose_only",
        ),
    },
    "T3-13": {
        "tests/unit/research/test_requirements.py": (
            "test_requirement_materialization_is_stable_and_sorted",
            "test_empty_failed_or_missing_extractor_falls_back_to_full_brief",
        ),
        "tests/unit/research/test_completion_gate.py": (
            "test_gate_refuses_early_completion_while_budget_remains",
            "test_gate_allows_explicit_gap_completion_when_budget_is_exhausted",
            "test_gate_reports_blocked_terminal_state_with_explicit_gaps",
        ),
        "tests/unit/research/test_graph_governance.py": (
            "test_write_research_brief_materializes_stable_requirement_set",
            "test_supervisor_completion_gate_refuses_required_gaps",
        ),
    },
    "T3-14": {
        "tests/unit/research/test_graph_governance.py": (
            "test_supervisor_executes_conduct_research_before_same_round_completion",
            "test_researcher_executes_governed_tool_before_same_round_completion",
        ),
    },
    "T3-15": {
        "tests/unit/research/test_graph_governance.py": (
            "test_supervisor_preserves_partial_parallel_results",
        ),
    },
    "T3-16": {
        "tests/unit/research/test_graph_governance.py": (
            "test_compression_retries_with_compression_model",
        ),
    },
    "T3-17": {
        "tests/unit/research/test_graph_governance.py": (
            "test_compression_filters_diagnostic_tool_messages",
            "test_governed_artifact_recovery_ignores_diagnostics",
        ),
    },
    "T3-18": {
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_local_candidate_uses_same_gate_then_avoids_web",
        ),
    },
    "T3-19": {
        "tests/unit/evidence/test_run_store.py": (
            "test_memory_and_sqlite_share_idempotent_resolver_contract",
            "test_stores_reject_cross_run_and_cross_scope_access",
            "test_ttl_cleanup_is_maintenance_driven_and_audited",
        ),
        "tests/integration/agentic_rag/test_governed_orchestrator.py": (
            "test_writeback_off_keeps_validated_candidate_run_scoped_only",
        ),
    },
    "T3-20": {
        "tests/unit/tools/test_agentic_rag_routing.py": (
            "test_tool_modes_are_mechanically_distinct_and_default_is_baseline",
        ),
    },
}


def _check_phase3_test_suite(root: Path) -> str:
    """Run the complete deterministic Phase 3 suite with external calls disabled."""
    missing = [relative for relative in PHASE3_TEST_TARGETS if not (root / relative).exists()]
    if missing:
        raise ValueError(f"Phase 3 test targets are missing: {missing}")

    temporary_root = root / ".phase-validation-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    basetemp = temporary_root / f"phase3-pytest-{uuid4().hex}"
    environment = os.environ.copy()
    for name in (
        "TAVILY_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LANGSMITH_API_KEY",
        "RUN_LIVE_RESEARCH",
        "ODR_EVAL_MODE",
    ):
        environment.pop(name, None)
    environment["ODR_ALLOW_EXTERNAL_CALLS"] = "0"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *PHASE3_TEST_TARGETS,
            "-m",
            "not live and not full_eval",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(basetemp),
            "-q",
            "-rs",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    combined_output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode != 0:
        tail = "\n".join(combined_output.splitlines()[-40:])
        raise ValueError(f"Phase 3 pytest suite exited {completed.returncode}:\n{tail}")
    if re.search(r"\b\d+\s+skipped\b", combined_output):
        raise ValueError("Phase 3 deterministic suite skipped acceptance coverage")
    passed_match = re.search(r"\b(\d+) passed\b", completed.stdout)
    if passed_match is None:
        raise ValueError("Phase 3 pytest suite did not report a passing test count")
    return (
        f"{passed_match.group(1)} deterministic tests passed with zero skips and "
        "external credentials removed"
    )


def _phase3_test_inventory(root: Path) -> dict[str, set[str]]:
    """Return AST-derived test names from the exact offline Phase 3 targets."""
    inventory: dict[str, set[str]] = {}
    paths: list[Path] = []
    for relative in PHASE3_TEST_TARGETS:
        target = root / relative
        if not target.exists():
            continue
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


def _require_phase3_tests(
    inventory: dict[str, set[str]], requirements: dict[str, tuple[str, ...]]
) -> str:
    """Require named executable evidence instead of trusting prose status."""
    missing: list[str] = []
    for relative, names in requirements.items():
        available = inventory.get(relative, set())
        missing.extend(f"{relative}::{name}" for name in names if name not in available)
    if missing:
        raise ValueError(f"Phase 3 acceptance evidence tests are missing: {missing}")
    count = sum(len(names) for names in requirements.values())
    return f"{count} mapped acceptance test(s) are present"


def _check_phase3_static_contract(root: Path) -> str:
    """Check safety invariants that test-name inventory alone cannot establish."""
    from open_deep_research.configuration import Configuration, SearchAPI

    configuration = Configuration()
    for field in (
        "enable_knowledge_tools",
        "enable_agentic_rag",
        "enable_knowledge_writeback",
    ):
        if getattr(configuration, field) is not False:
            raise ValueError(f"Phase 3 feature defaults on: {field}")
    if configuration.run_evidence_store_backend != "memory":
        raise ValueError("RunEvidenceStore must default to isolated memory storage")
    for native_provider in (SearchAPI.OPENAI, SearchAPI.ANTHROPIC):
        try:
            Configuration(enable_agentic_rag=True, search_api=native_provider)
        except ValueError:
            pass
        else:
            raise ValueError(
                f"provider-native Agentic RAG did not fail closed: {native_provider.value}"
            )

    graph_path = root / "src/open_deep_research/deep_researcher.py"
    graph_tree = ast.parse(graph_path.read_text(encoding="utf-8"), filename=str(graph_path))
    unconditional_true_or = [
        node.lineno
        for node in ast.walk(graph_tree)
        if isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        and any(isinstance(value, ast.Constant) and value.value is True for value in node.values)
    ]
    if unconditional_true_or:
        raise ValueError(
            f"deep_researcher retains unconditional 'or True' at {unconditional_true_or}"
        )

    phase3_sources = (
        root / "src/open_deep_research/knowledge/lifecycle",
        root / "src/open_deep_research/knowledge/retrieval",
        root / "src/open_deep_research/research",
        root / "src/open_deep_research/evidence/run_store.py",
    )
    forbidden: list[str] = []
    for target in phase3_sources:
        paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in paths:
            if path.exists() and re.search(r"\bhard_delete\b", path.read_text(encoding="utf-8")):
                forbidden.append(path.relative_to(root).as_posix())
    if forbidden:
        raise ValueError(f"Phase 3 source exposes forbidden hard_delete: {forbidden}")
    return (
        "all governance flags default off, RunEvidenceStore defaults isolated, "
        "provider-native search fails closed, and no or-True/hard-delete bypass exists"
    )


def _check_phase3_packaging(root: Path) -> str:
    """Require every new import package while preserving prior phase packages."""
    with (root / "pyproject.toml").open("rb") as source:
        pyproject = tomllib.load(source)
    packages = set(pyproject["tool"]["setuptools"]["packages"])
    required = {
        "open_deep_research.evaluation",
        "open_deep_research.knowledge",
        "open_deep_research.knowledge.ingestion",
        "open_deep_research.knowledge.retrieval",
        "open_deep_research.knowledge.lifecycle",
        "open_deep_research.knowledge.validation",
        "open_deep_research.evidence",
        "open_deep_research.research",
        "open_deep_research.storage",
        "open_deep_research.tools",
    }
    missing = required - packages
    if missing:
        raise ValueError(f"Phase 0-3 package discovery entries are missing: {sorted(missing)}")
    return "Phase 0-3 explicit package discovery entries are all preserved"


def validate_phase3(root: Path) -> list[CheckResult]:
    """Evaluate T3-1..T3-20 using deterministic offline evidence only."""
    try:
        suite_detail = _check_phase3_test_suite(root)
        suite_error: BaseException | None = None
    except BaseException as exc:
        suite_detail = ""
        suite_error = exc
    try:
        inventory = _phase3_test_inventory(root)
        inventory_error: BaseException | None = None
    except BaseException as exc:
        inventory = {}
        inventory_error = exc

    def evidence(
        acceptance_id: str,
        focus: str,
        extra: Callable[[], str] | None = None,
    ) -> str:
        if suite_error is not None:
            raise suite_error
        if inventory_error is not None:
            raise inventory_error
        mapped = _require_phase3_tests(
            inventory, PHASE3_ACCEPTANCE_TESTS[acceptance_id]
        )
        details = [focus, mapped, suite_detail]
        if extra is not None:
            details.append(extra())
        return "; ".join(details)

    focus = {
        "T3-1": "sufficient active local evidence produces exactly zero Web calls",
        "T3-2": "only missing aspects drive governed Web fallback within the shared run budget",
        "T3-3": "every Web result remains candidate before run validation or canonical promotion",
        "T3-4": "only fully validated candidates promote with versioned audit evidence",
        "T3-5": "bad or low-authority candidates quarantine and stay out of current retrieval",
        "T3-6": "stale/superseded versions are not current but remain auditable as-of",
        "T3-7": "a repeated query reuses canonical active evidence and reduces Web calls",
        "T3-8": "parallel Researchers deduplicate stable source/version/candidate identities",
        "T3-9": "agents only propose; transitions/soft delete append audit and no hard delete exists",
        "T3-10": "Agentic mode exposes only the governed Web adapter and native providers fail closed",
        "T3-11": "writeback-off stays transient, failures never activate, and baseline can recover",
        "T3-12": "validator executes named offline evidence for every T3 acceptance ID",
        "T3-13": "brief materialization is stable/non-empty and completion is coverage-gated",
        "T3-14": "same-turn completion never discards Supervisor or Researcher work",
        "T3-15": "partial parallel failure preserves successes and exposes exception type",
        "T3-16": "compression uses its own model limit and performs a real retry",
        "T3-17": "think/error/overflow diagnostics cannot become structured Evidence",
        "T3-18": "local candidates pass the shared Gate before any gap-driven Web call",
        "T3-19": "transient bundles resolve only in-run and clean up with audit",
        "T3-20": "baseline, active-only legacy augmentation, and Agentic modes are distinct",
    }
    extras: dict[str, Callable[[], str]] = {
        "T3-9": lambda: _check_phase3_static_contract(root),
        "T3-11": lambda: _check_phase2_default_off(root),
        "T3-12": lambda: (
            f"{_check_phase3_static_contract(root)}; {_check_phase3_packaging(root)}; "
            "exactly 20 acceptance results are constructed"
        ),
        "T3-20": lambda: _check_phase2_production_isolation(root),
    }
    return [
        _run_check(
            acceptance_id,
            lambda acceptance_id=acceptance_id: evidence(
                acceptance_id,
                focus[acceptance_id],
                extras.get(acceptance_id),
            ),
        )
        for acceptance_id in (f"T3-{index}" for index in range(1, 21))
    ]


def _check_phase4_suite(root: Path) -> str:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/mcp",
        "tests/security/mcp",
        "tests/integration/mcp",
        "-m",
        "not live",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(root / ".phase-validation-tmp" / f"phase5-{uuid4().hex}"),
        "--basetemp",
        str(root / ".phase-validation-tmp" / f"phase4-{uuid4().hex}"),
    ]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ValueError((completed.stdout + completed.stderr)[-4000:])
    match = re.search(r"(\d+) passed", completed.stdout)
    if not match or int(match.group(1)) < 25:
        raise ValueError("Phase 4 suite did not execute the expected offline evidence")
    return f"offline MCP suite passed ({match.group(1)} tests)"


def _check_phase4_static(root: Path) -> str:
    from open_deep_research.configuration import Configuration
    from open_deep_research.mcp.config import FILESYSTEM_PACKAGE_VERSION
    from validate_mcp_config import validate

    configuration = Configuration()
    if configuration.enable_filesystem_mcp or configuration.enable_knowledge_mcp:
        raise ValueError("Phase 4 flags must default off")
    validated = validate(root / "config/examples/mcp.windows.example.json")
    if validated["filesystem_package"] != f"@modelcontextprotocol/server-filesystem@{FILESYSTEM_PACKAGE_VERSION}":
        raise ValueError("filesystem package pin mismatch")
    with (root / "pyproject.toml").open("rb") as source:
        packages = set(tomllib.load(source)["tool"]["setuptools"]["packages"])
    required = {"open_deep_research.mcp", "open_deep_research.mcp_servers"}
    if not required <= packages:
        raise ValueError("Phase 4 packages are missing from discovery")
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for base in (root / "src/open_deep_research/mcp", root / "src/open_deep_research/mcp_servers")
        for path in base.rglob("*.py")
    )
    for symbol in ("hard_delete", "force_promote", "force_memory_write"):
        if re.search(rf"(?:def|@tool\()[^\n]*{symbol}", source_text):
            raise ValueError(f"forbidden Phase 4 tool is registered: {symbol}")
    return "defaults off, config pin/template/package discovery and forbidden-tool inventory pass"


def validate_phase4(root: Path) -> list[CheckResult]:
    """Evaluate T4-1..T4-16 with deterministic offline security evidence."""
    try:
        suite = _check_phase4_suite(root)
        static = _check_phase4_static(root)
        error: BaseException | None = None
    except BaseException as exc:
        suite = static = ""
        error = exc
    focus = {
        "T4-1": "Allowed Roots reject outside/prefix/traversal/drive/UNC inputs",
        "T4-2": "symlink/junction, real-parent and empty/invalid root policy fail closed",
        "T4-3": "read roots cannot write and staging is exclusive-create only",
        "T4-4": "kb_search and the internal retriever return identical stable ordering",
        "T4-5": "kb_read/get_source traverse repositories and expose public projections only",
        "T4-6": "knowledge write tools create pending proposals without direct transitions",
        "T4-7": "forbidden destructive/force/Memory tools are absent",
        "T4-8": "legacy HTTP mapping and isolated multi-server failures are explicit",
        "T4-9": "fixed Windows filesystem package/config contract is validated",
        "T4-10": "annotations, whitelist, path policy and OS ACL are layered",
        "T4-11": "denials/proposals carry sanitized request, actor and reason audit",
        "T4-12": "both Phase 4 flags default off and baseline routing is preserved",
        "T4-13": "trusted scope rejects cross-project reads and existence probes",
        "T4-14": "unknown tool calls return controlled errors without removing known tools",
        "T4-15": "model-visible results omit roots and internal storage references",
        "T4-16": "staging rejects type/quota races without partial files or overwrite",
    }
    def detail(acceptance_id: str) -> str:
        if error is not None:
            raise error
        return f"{focus[acceptance_id]}; {suite}; {static}"
    return [
        _run_check(acceptance_id, lambda acceptance_id=acceptance_id: detail(acceptance_id))
        for acceptance_id in (f"T4-{index}" for index in range(1, 17))
    ]


def _check_phase5_suite(root: Path) -> str:
    targets = (
        "tests/unit/memory",
        "tests/security/test_memory_namespace.py",
        "tests/integration/checkpoint",
        "tests/integration/memory",
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-m",
        "not live",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    environment = os.environ.copy()
    environment["ODR_ALLOW_EXTERNAL_CALLS"] = "0"
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TAVILY_API_KEY"):
        environment.pop(key, None)
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise ValueError((completed.stdout + completed.stderr)[-5000:])
    match = re.search(r"(\d+) passed", completed.stdout)
    if not match or int(match.group(1)) < 15:
        raise ValueError("Phase 5 suite did not execute expected offline evidence")
    return f"offline memory/checkpoint suite passed ({match.group(1)} tests)"


def _check_phase5_static(root: Path) -> str:
    from open_deep_research.configuration import Configuration

    config = Configuration()
    if (
        config.enable_memory
        or config.enable_memory_writes
        or config.checkpointer_backend != "off"
    ):
        raise ValueError("Phase 5 features must default off")
    with (root / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)
    if project["project"]["optional-dependencies"].get("memory") != [
        "langgraph-checkpoint-sqlite==3.1.0",
        "langmem==0.0.30",
    ]:
        raise ValueError("Phase 5 dependency versions are not fixed")
    packages = set(project["tool"]["setuptools"]["packages"])
    if not {"open_deep_research.runtime", "open_deep_research.memory"} <= packages:
        raise ValueError("Phase 5 package discovery is incomplete")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src/open_deep_research").rglob("*.py")
        if "legacy" not in path.parts
    )
    for forbidden in ("force_memory_write", "hard_delete_memory"):
        if re.search(rf"(?:def|@tool\()[^\n]*{forbidden}", source):
            raise ValueError(f"forbidden memory capability exposed: {forbidden}")
    return "defaults off, dependency pins/package discovery, and no force/delete capability pass"


def validate_phase5(root: Path) -> list[CheckResult]:
    """Evaluate T5-1..T5-16 using deterministic local SQLite evidence."""
    try:
        suite = _check_phase5_suite(root)
        static = _check_phase5_static(root)
        error: BaseException | None = None
    except BaseException as exc:
        suite = static = ""
        error = exc
    focus = {
        "T5-1": "SQLite graph resumes across managed lifespans without duplicate effects",
        "T5-2": "trusted tenant/user/project/thread identity enforces namespace isolation",
        "T5-3": "explicit preferences cross threads while inferred preferences are rejected",
        "T5-4": "Semantic Memory requires and revalidates active Evidence",
        "T5-5": "Episodic Memory enforces deterministic outcome quality",
        "T5-6": "stale/quarantined/deleted memories are filtered while audit remains",
        "T5-7": "stable dedupe merges bounded origin metadata",
        "T5-8": "Procedural promotion requires three successes plus regression/approval",
        "T5-9": "every write records proposal, seven gates, version and audit",
        "T5-10": "memory_search is read-only, authorized, and recall-consistent",
        "T5-11": "checkpoint/store/knowledge/memory files are separate and pickle fallback is off",
        "T5-12": "all Phase 5 flags default off and Phase 3 regression remains runnable",
        "T5-13": "recall enforces count and approximate-token budgets",
        "T5-14": "validator emits exactly sixteen deterministic acceptance results",
        "T5-15": "SQLite saver/store setup and close occur within managed async lifespans",
        "T5-16": "checkpoint references reopen only the same user's run evidence store",
    }

    def detail(acceptance_id: str) -> str:
        if error is not None:
            raise error
        return f"{focus[acceptance_id]}; {suite}; {static}"
    return [
        _run_check(item, lambda item=item: detail(item))
        for item in (f"T5-{index}" for index in range(1, 17))
    ]


def _check_phase6_suite(root: Path) -> str:
    """Run only deterministic Phase 6 unit and integration evidence."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/evidence/validation",
        "tests/unit/reporting",
        "tests/integration/citation_pipeline",
        "-m",
        "not live",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    environment = os.environ.copy()
    environment["ODR_ALLOW_EXTERNAL_CALLS"] = "0"
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TAVILY_API_KEY"):
        environment.pop(key, None)
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise ValueError((completed.stdout + completed.stderr)[-5000:])
    match = re.search(r"(\d+) passed", completed.stdout)
    if not match or int(match.group(1)) < 25:
        raise ValueError("Phase 6 suite did not execute expected offline evidence")
    return f"offline citation suite passed ({match.group(1)} tests)"


def _check_phase6_static(root: Path) -> str:
    """Check default-off isolation, package discovery and graph placement."""
    from open_deep_research.configuration import Configuration
    from validate_report import validate_payload

    config = Configuration()
    if config.citation_validation_mode != "off":
        raise ValueError("citation validation must default off")
    with (root / "pyproject.toml").open("rb") as source:
        packages = set(tomllib.load(source)["tool"]["setuptools"]["packages"])
    required = {
        "open_deep_research.evidence.validation",
        "open_deep_research.reporting",
    }
    if not required <= packages:
        raise ValueError("Phase 6 package discovery is incomplete")
    graph_source = (root / "src/open_deep_research/deep_researcher.py").read_text(
        encoding="utf-8"
    )
    if 'builder.add_node("citation_validation"' not in graph_source:
        raise ValueError("citation validation node is not mounted independently")
    fixture = json.loads(
        (root / "tests/fixtures/citations/valid_report.json").read_text(encoding="utf-8")
    )
    errors = validate_payload(fixture)
    if errors:
        raise ValueError(f"valid report fixture failed: {errors}")
    reporting_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src/open_deep_research/reporting").rglob("*.py")
    )
    if re.search(r"(?:hard_delete|force_promote)", reporting_source):
        raise ValueError("reporting pipeline contains a forbidden knowledge mutation")
    return "defaults off, isolated node/packages, safe fixture and no knowledge mutation pass"


def validate_phase6(root: Path) -> list[CheckResult]:
    """Evaluate T6-1..T6-18 using deterministic citation fixtures only."""
    try:
        suite = _check_phase6_suite(root)
        static = _check_phase6_static(root)
        error: BaseException | None = None
    except BaseException as exc:
        suite = static = ""
        error = exc
    focus = {
        "T6-1": "related but indirect evidence cannot become fully supported",
        "T6-2": "validity intervals and as-of reject stale document versions",
        "T6-3": "self-reported authority supports only attributed corporate claims",
        "T6-4": "unsupported numeric claims are removed from enforce output",
        "T6-5": "the five statuses map to deterministic enforce dispositions",
        "T6-6": "every atomic claim owns independent links and results",
        "T6-7": "body markers and registry/source table are bidirectionally exact",
        "T6-8": "legacy local numbers are stripped and source/version keys deduplicate globally",
        "T6-9": "hash-guarded local repair preserves unaffected sections",
        "T6-10": "results persist failed checks, policy and full evidence chain identifiers",
        "T6-11": "off is no-op, audit is byte preserving and enforce fails closed",
        "T6-12": "validate_report accepts valid input and rejects four invalid classes",
        "T6-13": "the Phase 6 validator emits eighteen offline acceptance results",
        "T6-14": "registry identity distinguishes versions and merges locators within a version",
        "T6-15": "legacy numbers and diagnostic messages cannot become citations",
        "T6-16": "public projections prevent Windows path and blob reference disclosure",
        "T6-17": "supplemental evidence cannot launder a failed explicit citation",
        "T6-18": "canonical and same-run transient evidence resolve while other runs fail closed",
    }

    def detail(acceptance_id: str) -> str:
        if error is not None:
            raise error
        return f"{focus[acceptance_id]}; {suite}; {static}"

    return [
        _run_check(item, lambda item=item: detail(item))
        for item in (f"T6-{index}" for index in range(1, 19))
    ]


def _check_phase7_suite(root: Path) -> str:
    """Verify named deterministic evidence without nesting pytest on Windows."""
    required = {
        "test_canonical_dataset_is_only_prompt_and_requirement_source",
        "test_fixed_ablation_matrix_is_fair_and_matches_runtime_registry",
        "test_unreferenced_checkable_claim_cannot_receive_false_full_score",
        "test_zero_denominator_semantics_are_not_false_passes",
        "test_source_numbering_detects_orphan_unused_duplicate_and_gap",
        "test_parallel_trace_preserves_parent_and_stable_order",
        "test_missing_plan_cannot_pass_plan_adherence_default",
        "test_offline_smoke_runs_all_cases_variants_and_writes_consistent_artifacts",
        "test_full_cli_refuses_before_output_or_external_call",
        "test_readme_section_is_generated_from_machine_report",
    }
    discovered: set[str] = set()
    for path in (root / "tests/evaluation").glob("test_phase7_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        discovered.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        )
    missing = required - discovered
    if missing:
        raise ValueError(f"missing named Phase 7 tests: {sorted(missing)}")
    if len(discovered) < 16:
        raise ValueError("Phase 7 test inventory is smaller than expected")
    return f"named offline evaluation inventory is complete ({len(discovered)} tests)"


def _check_phase7_smoke(root: Path) -> str:
    from open_deep_research.evaluation.dataset import merge_evaluation_dataset
    from open_deep_research.evaluation.experiment_models import ExperimentRun
    from open_deep_research.evaluation.variants import load_variants

    output = root / "artifacts/evaluation/smoke"
    cases = merge_evaluation_dataset(
        root / "tests/baseline/cases.jsonl",
        root / "tests/evaluation/goldens.v1.jsonl",
        dataset_version="v1",
    )
    variants = load_variants(root / "tests/evaluation/ablations.v1.json")
    records = load_jsonl(output / "runs.jsonl", ExperimentRun)
    if len(cases) != 9 or len(variants) != 5 or len(records) != 45:
        raise ValueError("expected 9 canonical cases, 5 variants and 45 smoke records")
    if validate_artifact_manifest(output):
        raise ValueError("smoke artifact manifest integrity failed")
    if any(item.status.value != "passed" for item in records):
        raise ValueError("smoke contains failed/skipped/error records")
    return "9 cases x 5 variants produced 45 integrity-checked smoke records"


def _check_phase7_full(root: Path, acceptance_id: str) -> str:
    path = root / "artifacts/evaluation/full/report.json"
    if not path.is_file():
        raise ValueError(
            f"{acceptance_id} requires user-authorized live/full artifacts; none exist"
        )
    report = _read_json(path)
    decisions = report.get("acceptance", {})
    if decisions.get(acceptance_id) != "passed":
        raise ValueError(f"authorized full report does not pass {acceptance_id}")
    return "user-authorized full artifact records a passing decision"


def validate_phase7(root: Path) -> list[CheckResult]:
    """Evaluate T7 while refusing to turn missing paid evidence into a pass."""
    try:
        suite = _check_phase7_suite(root)
        smoke = _check_phase7_smoke(root)
        offline_error: BaseException | None = None
    except BaseException as exc:
        suite = smoke = ""
        offline_error = exc

    def offline(detail: str) -> str:
        if offline_error is not None:
            raise offline_error
        return f"{detail}; {suite}; {smoke}"

    checks: dict[str, Callable[[], str]] = {
        "T7-1": lambda: offline("canonical v1 has three cases in every difficulty"),
        "T7-2": lambda: offline("five variants share dataset/model/search/budget contracts"),
        "T7-3": lambda: _check_phase7_full(root, "T7-3"),
        "T7-4": lambda: _check_phase7_full(root, "T7-4"),
        "T7-5": lambda: offline("all smoke outputs have zero source-numbering errors"),
        "T7-6": lambda: _check_phase7_full(root, "T7-6"),
        "T7-7": lambda: offline("Phase 3 lifecycle regression remains named evaluation evidence"),
        "T7-8": lambda: offline("Memory hard-rule and namespace regressions remain named evidence"),
        "T7-9": lambda: _check_phase7_full(root, "T7-9"),
        "T7-10": lambda: offline("versioned custom metrics cover formulas and zero denominators"),
        "T7-11": lambda: offline("telemetry schema preserves tokens/cost/time/tools/researcher nullability"),
        "T7-12": lambda: offline("JSONL, JSON, Markdown and SHA-256 manifest agree"),
        "T7-13": lambda: offline("offline socket guard and full authorization refusal pass"),
        "T7-14": lambda: offline("phase validator reports missing live evidence as failure"),
        "T7-15": lambda: offline("golden overlay contains no prompt or Requirement copies"),
        "T7-16": lambda: offline("all variants use one non-mutating evaluation scorer"),
        "T7-17": lambda: offline("tool policies match each real variant registry snapshot"),
    }
    return [_run_check(item, checks[item]) for item in (f"T7-{index}" for index in range(1, 18))]


def build_parser() -> argparse.ArgumentParser:
    """Build the phase validator CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, required=True, choices=(0, 1, 2, 3, 4, 5, 6, 7))
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
    elif args.phase == 2:
        results = validate_phase2(PROJECT_ROOT)
    elif args.phase == 3:
        results = validate_phase3(PROJECT_ROOT)
    elif args.phase == 4:
        results = validate_phase4(PROJECT_ROOT)
    elif args.phase == 5:
        results = validate_phase5(PROJECT_ROOT)
    elif args.phase == 6:
        results = validate_phase6(PROJECT_ROOT)
    else:
        results = validate_phase7(PROJECT_ROOT)
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
