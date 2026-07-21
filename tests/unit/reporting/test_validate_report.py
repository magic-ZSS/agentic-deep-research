from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.validate_report import validate_payload


@pytest.fixture
def valid_payload():
    return {
        "final_report": "Fact.[1]\n\n### Sources\n\n[1] Source — https://example.test",
        "mode": "enforce",
        "claims": [{"claim_id": "claim-1"}],
        "results": [
            {
                "claim_id": "claim-1",
                "status": "fully_supported",
                "links": [{"accepted": True, "temporal_status": "current"}],
            }
        ],
        "registry": [{"display_number": 1}],
    }


def test_valid_report_contract(valid_payload):
    assert validate_payload(valid_payload) == []


@pytest.mark.parametrize("mutation", ["orphan", "missing", "stale", "unsupported"])
def test_invalid_report_contracts_fail(valid_payload, mutation):
    payload = deepcopy(valid_payload)
    if mutation == "orphan":
        payload["final_report"] = payload["final_report"].replace("[1]", "[2]", 1)
    elif mutation == "missing":
        payload["registry"] = []
    elif mutation == "stale":
        payload["results"][0]["links"][0]["temporal_status"] = "stale"
    else:
        payload["results"][0]["status"] = "unsupported"
    assert validate_payload(payload)


def test_local_path_is_rejected(valid_payload):
    valid_payload["registry"][0]["uri"] = r"C:\private\secret.pdf"
    assert "artifact exposes a local/internal storage path" in validate_payload(valid_payload)
