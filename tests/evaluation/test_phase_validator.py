import json

import pytest

from scripts import validate_phase


def test_phase0_validator_accepts_committed_fixtures(capsys):
    exit_code = validate_phase.main(["--phase", "0"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "PASS T0-1" in output
    assert "PASS T0-12" in output


def test_phase0_validator_rejects_missing_manifest_field(tmp_path, capsys):
    manifest = json.loads(
        (validate_phase.PROJECT_ROOT / "tests/baseline/baseline_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest.pop("phase_start_commit")
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    exit_code = validate_phase.main(
        ["--phase", "0", "--manifest", str(path)]
    )

    assert exit_code != 0
    assert "FAIL" in capsys.readouterr().out


def test_phase0_validator_rejects_wrong_commit(tmp_path, capsys):
    manifest = json.loads(
        (validate_phase.PROJECT_ROOT / "tests/baseline/baseline_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["phase_start_commit"] = "0" * 40
    path = tmp_path / "wrong-commit.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    exit_code = validate_phase.main(
        ["--phase", "0", "--manifest", str(path)]
    )

    assert exit_code != 0
    output = capsys.readouterr().out
    assert "FAIL T0-9" in output
    assert "FAIL T0-11" in output


def test_phase0_validator_rejects_changed_protected_core_hash(tmp_path, capsys):
    manifest = json.loads(
        (validate_phase.PROJECT_ROOT / "tests/baseline/baseline_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["protected_core_sha256"][
        "src/open_deep_research/deep_researcher.py"
    ] = "0" * 64
    path = tmp_path / "wrong-core-hash.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    exit_code = validate_phase.main(
        ["--phase", "0", "--manifest", str(path)]
    )

    assert exit_code != 0
    assert "FAIL T0-12" in capsys.readouterr().out


def test_phase1_validator_reports_every_acceptance_id(capsys):
    exit_code = validate_phase.main(["--phase", "1"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "PASS T1-1:" in output
    assert "PASS T1-16:" in output
    assert output.count("PASS T1-") == 16


def _complete_phase3_inventory():
    inventory = {}
    for mapping in validate_phase.PHASE3_ACCEPTANCE_TESTS.values():
        for relative, names in mapping.items():
            inventory.setdefault(relative, set()).update(names)
    return inventory


def test_phase0_protected_hashes_remain_historical_after_later_phases():
    manifest = json.loads(
        (validate_phase.PROJECT_ROOT / "tests/baseline/baseline_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    detail = validate_phase._check_protected_core(
        validate_phase.PROJECT_ROOT, manifest
    )

    assert "immutable committed Phase 0 manifest" in detail


def test_phase3_parser_and_acceptance_map_cover_exactly_t3_1_through_t3_20():
    args = validate_phase.build_parser().parse_args(["--phase", "3"])

    assert args.phase == 3
    assert tuple(validate_phase.PHASE3_ACCEPTANCE_TESTS) == tuple(
        f"T3-{index}" for index in range(1, 21)
    )


def test_phase3_validator_reports_twenty_results_from_named_offline_evidence(
    monkeypatch,
):
    monkeypatch.setattr(
        validate_phase, "_check_phase3_test_suite", lambda _root: "offline suite"
    )
    monkeypatch.setattr(
        validate_phase,
        "_phase3_test_inventory",
        lambda _root: _complete_phase3_inventory(),
    )
    monkeypatch.setattr(
        validate_phase, "_check_phase3_static_contract", lambda _root: "static"
    )
    monkeypatch.setattr(
        validate_phase, "_check_phase3_packaging", lambda _root: "packaging"
    )
    monkeypatch.setattr(
        validate_phase, "_check_phase2_default_off", lambda _root: "default off"
    )
    monkeypatch.setattr(
        validate_phase,
        "_check_phase2_production_isolation",
        lambda _root: "isolated",
    )

    results = validate_phase.validate_phase3(validate_phase.PROJECT_ROOT)

    assert [result.acceptance_id for result in results] == [
        f"T3-{index}" for index in range(1, 21)
    ]
    assert all(result.passed for result in results)
    assert all("offline suite" in result.detail for result in results)


def test_phase3_validator_rejects_missing_named_acceptance_test(monkeypatch):
    inventory = _complete_phase3_inventory()
    required_path = "tests/unit/research/test_requirements.py"
    inventory[required_path].remove(
        "test_requirement_materialization_is_stable_and_sorted"
    )
    monkeypatch.setattr(
        validate_phase, "_check_phase3_test_suite", lambda _root: "offline suite"
    )
    monkeypatch.setattr(
        validate_phase, "_phase3_test_inventory", lambda _root: inventory
    )
    monkeypatch.setattr(
        validate_phase, "_check_phase3_static_contract", lambda _root: "static"
    )
    monkeypatch.setattr(
        validate_phase, "_check_phase3_packaging", lambda _root: "packaging"
    )
    monkeypatch.setattr(
        validate_phase, "_check_phase2_default_off", lambda _root: "default off"
    )
    monkeypatch.setattr(
        validate_phase,
        "_check_phase2_production_isolation",
        lambda _root: "isolated",
    )

    results = validate_phase.validate_phase3(validate_phase.PROJECT_ROOT)
    t3_13 = next(result for result in results if result.acceptance_id == "T3-13")

    assert not t3_13.passed
    assert "acceptance evidence tests are missing" in t3_13.detail


def test_phase3_inventory_requirement_is_not_satisfied_by_prose_only():
    with pytest.raises(ValueError, match="acceptance evidence tests are missing"):
        validate_phase._require_phase3_tests(
            {},
            {
                "tests/integration/agentic_rag/test_graph_recovery.py": (
                    "test_compression_retries_after_token_trim_with_compression_model",
                )
            },
        )
