import hashlib
import json
import subprocess

import pytest

from open_deep_research.evaluation.calibration_state import (
    CalibrationInFlightError,
    CalibrationJournalError,
    CalibrationJournalStore,
    CalibrationRunDefinition,
    dirty_diff_fingerprint,
    make_experiment_identity,
    stable_run_id,
    stable_step_id,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(**updates):
    values = {
        "git_head_value": "a" * 40,
        "dirty_diff_sha256": _hash("dirty"),
        "plan_sha256": _hash("plan"),
        "ablation_sha256": _hash("ablation"),
        "dataset_id": "v1",
        "model_ids": {
            "judge": "openai:qwen3.7-plus",
            "research": "openai:qwen3.7-plus",
        },
    }
    values.update(updates)
    return make_experiment_identity(**values)


def _store(tmp_path, *, metrics=("task_completion", "faithfulness")):
    path = tmp_path / "calibration" / "journal.json"
    store = CalibrationJournalStore.create(
        path,
        identity=_identity(),
        runs=[
            CalibrationRunDefinition(
                case_id="simple-001", variant_id="baseline", repeat=1
            )
        ],
        judge_metric_names=metrics,
    )
    return path, store


def _known_usage(total=30):
    return {"input_tokens": total - 10, "output_tokens": 10, "total_tokens": total}


def test_experiment_run_and_step_ids_change_with_every_paid_identity_input():
    original = _identity()
    variants = [
        _identity(git_head_value="b" * 40),
        _identity(dirty_diff_sha256=_hash("other-dirty")),
        _identity(plan_sha256=_hash("other-plan")),
        _identity(ablation_sha256=_hash("other-ablation")),
        _identity(dataset_id="v2"),
        _identity(
            model_ids={
                "judge": "openai:qwen3.7-plus-v2",
                "research": "openai:qwen3.7-plus",
            }
        ),
    ]
    original_run = stable_run_id(
        original, case_id="simple-001", variant_id="baseline", repeat=1
    )
    original_step = stable_step_id(original_run, step_kind="research")

    assert len({original.experiment_id, *(item.experiment_id for item in variants)}) == 7
    for changed in variants:
        changed_run = stable_run_id(
            changed, case_id="simple-001", variant_id="baseline", repeat=1
        )
        assert changed_run != original_run
        assert stable_step_id(changed_run, step_kind="research") != original_step


def test_dirty_diff_fingerprint_tracks_tracked_and_untracked_without_ignored_files(
    tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "offline@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Offline Test"], cwd=root, check=True
    )
    (root / ".gitignore").write_text(".env\nartifacts/\n", encoding="utf-8")
    tracked = root / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)

    clean = dirty_diff_fingerprint(root)
    tracked.write_text("two\n", encoding="utf-8")
    tracked_changed = dirty_diff_fingerprint(root)
    assert tracked_changed != clean

    untracked = root / "candidate.txt"
    untracked.write_text("candidate\n", encoding="utf-8")
    with_untracked = dirty_diff_fingerprint(root)
    assert with_untracked != tracked_changed
    assert dirty_diff_fingerprint(
        root, exclude_untracked_paths=["candidate.txt"]
    ) == tracked_changed

    (root / ".env").write_text("API_KEY=must-not-be-read\n", encoding="utf-8")
    assert dirty_diff_fingerprint(root) == with_untracked
    (root / "artifacts").mkdir()
    (root / "artifacts" / "journal.json").write_text("changes", encoding="utf-8")
    assert dirty_diff_fingerprint(root) == with_untracked


def test_atomic_journal_recovers_after_each_terminal_and_skips_paid_work(tmp_path):
    path, store = _store(tmp_path)
    plan = store.load().runs[0]
    assert [event.event_type for event in store.load().events] == ["planned"]
    assert store.resume_summary().pending_step_ids == [plan.research_step_id]

    store.start_research(plan.run_id)
    recovered = CalibrationJournalStore(path)
    with pytest.raises(CalibrationInFlightError, match=plan.research_step_id):
        recovered.assert_resumable()

    recovered.complete_research(plan.run_id, **_known_usage(100))
    recovered = CalibrationJournalStore(path)
    summary = recovered.assert_resumable()
    assert set(summary.pending_step_ids) == set(plan.judge_step_ids.values())

    for index, (metric, step_id) in enumerate(plan.judge_step_ids.items(), start=1):
        recovered.start_judge_metric(plan.run_id, metric)
        recovered.complete_judge_metric(
            plan.run_id,
            metric,
            status="passed",
            **_known_usage(index * 10),
        )
        recovered = CalibrationJournalStore(path)
        assert recovered.should_skip_metric(step_id)

    assert recovered.assert_resumable().pending_step_ids == [
        plan.run_terminal_step_id
    ]
    recovered.complete_run(plan.run_id, status="completed")
    final = CalibrationJournalStore(path)
    summary = final.assert_resumable()
    assert summary.completed_run_ids == [plan.run_id]
    assert summary.pending_step_ids == []
    assert final.should_skip_run(plan.run_id)
    assert [event.event_type for event in final.load().events] == [
        "planned",
        "started",
        "research_completed",
        "started",
        "judge_metric_terminal",
        "started",
        "judge_metric_terminal",
        "run_terminal",
    ]


def test_started_judge_without_terminal_blocks_automatic_resume(tmp_path):
    path, store = _store(tmp_path, metrics=("faithfulness",))
    plan = store.load().runs[0]
    store.start_research(plan.run_id)
    store.complete_research(plan.run_id, **_known_usage())
    store.start_judge_metric(plan.run_id, "faithfulness")

    recovered = CalibrationJournalStore(path)
    summary = recovered.resume_summary()
    assert summary.can_resume is False
    assert summary.blocked_in_flight_step_ids == [
        plan.judge_step_ids["faithfulness"]
    ]
    with pytest.raises(CalibrationInFlightError):
        recovered.assert_resumable()


def test_unknown_terminal_usage_fails_closed_and_cannot_be_counted_as_zero(tmp_path):
    _, store = _store(tmp_path, metrics=("faithfulness",))
    plan = store.load().runs[0]
    store.start_research(plan.run_id)
    store.complete_research(
        plan.run_id,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )

    summary = store.resume_summary()
    assert summary.can_resume is False
    assert summary.unknown_usage_step_ids == [plan.research_step_id]
    with pytest.raises(CalibrationInFlightError):
        store.assert_resumable()


def test_journal_schema_rejects_secrets_endpoints_and_free_form_payloads(tmp_path):
    with pytest.raises(ValueError, match="secret or endpoint"):
        _identity(model_ids={"judge": "https://private.example/v1"})
    with pytest.raises(ValueError, match="secret or endpoint"):
        _identity(model_ids={"judge": "sk-super-secret-value"})

    path, store = _store(tmp_path)
    serialized = path.read_text(encoding="utf-8")
    assert "endpoint" not in serialized.lower()
    assert "api_key" not in serialized.lower()
    assert "secret" not in serialized.lower()
    payload = json.loads(serialized)
    payload["events"][0]["raw_output"] = "must never be accepted"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CalibrationJournalError):
        store.load()


def test_failed_atomic_replace_preserves_last_recoverable_journal(
    tmp_path, monkeypatch
):
    import open_deep_research.evaluation.calibration_state as state

    path, store = _store(tmp_path)
    before = path.read_bytes()
    plan = store.load().runs[0]

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(state.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        store.start_research(plan.run_id)

    assert path.read_bytes() == before
    assert CalibrationJournalStore(path).resume_summary().can_resume is True
    assert not list(path.parent.glob("*.tmp"))


def test_completed_metric_and_run_terminals_cannot_be_recorded_twice(tmp_path):
    _, store = _store(tmp_path, metrics=("faithfulness",))
    plan = store.load().runs[0]
    store.start_research(plan.run_id)
    store.complete_research(plan.run_id, **_known_usage())
    store.start_judge_metric(plan.run_id, "faithfulness")
    store.complete_judge_metric(
        plan.run_id,
        "faithfulness",
        status="passed",
        **_known_usage(),
    )
    with pytest.raises(CalibrationJournalError, match="already recorded"):
        store.complete_judge_metric(
            plan.run_id,
            "faithfulness",
            status="passed",
            **_known_usage(),
        )
    store.complete_run(plan.run_id, status="completed")
    with pytest.raises(CalibrationJournalError):
        store.complete_run(plan.run_id, status="completed")
