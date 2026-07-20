import json

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
