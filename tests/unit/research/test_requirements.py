from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from open_deep_research.research.requirements import (
    RequirementDraft,
    RequirementMaterializer,
)


class FakeExtractor:
    def __init__(self, drafts):
        self.drafts = drafts

    async def extract(self, **_kwargs):
        return self.drafts


class FailingExtractor:
    def extract(self, **_kwargs):
        raise RuntimeError("offline extractor failure")


def materialize(materializer, **kwargs):
    return asyncio.run(materializer.materialize(**kwargs))


def test_requirement_materialization_is_stable_and_sorted():
    drafts = (
        RequirementDraft(text="Optional appendix", required=False, priority=9),
        RequirementDraft(text="Required Z", required=True, priority=1),
        RequirementDraft(text="Required A", required=True, priority=1),
        RequirementDraft(text="Required A", required=True, priority=1),
    )
    materializer = RequirementMaterializer(
        extractor=FakeExtractor(drafts),
        extractor_version="fake-v1",
        policy_version="policy-v7",
    )
    values = {
        "research_brief": "Compare the systems.",
        "scope_id": "scope-test",
        "run_id": "run-test",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }

    first = materialize(materializer, **values)
    second = materialize(materializer, **values)

    assert first.plan_id == second.plan_id
    assert first.requirement_ids == second.requirement_ids
    assert [item.text for item in first.requirements] == [
        "Required A",
        "Required Z",
        "Optional appendix",
    ]
    assert [item.ordinal for item in first.requirements] == [0, 1, 2]
    assert first.scope_id == "scope-test"
    assert first.run_id == "run-test"
    assert first.extractor_version == "fake-v1"
    assert first.policy_version == "policy-v7"
    assert first.research_brief_hash == first.brief_sha256
    assert not first.used_fallback


@pytest.mark.parametrize(
    ("extractor", "expected_reason"),
    [
        (FakeExtractor(()), "extractor_returned_empty"),
        (FailingExtractor(), "extractor_failed:RuntimeError"),
        (FakeExtractor(("invalid",)), "extractor_output_invalid:TypeError"),
        (None, "extractor_not_configured"),
    ],
)
def test_empty_failed_or_missing_extractor_falls_back_to_full_brief(
    extractor, expected_reason
):
    brief = "First line.\nSecond line contains the whole brief."
    result = materialize(
        RequirementMaterializer(extractor=extractor),
        research_brief=brief,
        scope_id="scope-test",
        run_id="run-test",
    )

    assert len(result.requirements) == 1
    assert result.requirements[0].text == brief
    assert result.requirements[0].required is True
    assert result.used_fallback
    assert result.fallback_reason == expected_reason


def test_materializer_rejects_blank_brief_before_extraction():
    with pytest.raises(ValueError, match="cannot be blank"):
        materialize(
            RequirementMaterializer(),
            research_brief="  ",
            scope_id="scope-test",
            run_id="run-test",
        )


def test_requirement_set_rejects_naive_created_at():
    with pytest.raises(ValidationError, match="timezone-aware"):
        materialize(
            RequirementMaterializer(),
            research_brief="A brief",
            scope_id="scope-test",
            run_id="run-test",
            created_at=datetime(2026, 1, 1),
        )
