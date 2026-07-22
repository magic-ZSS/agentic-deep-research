import asyncio
import json
from contextlib import contextmanager

import pytest
from pydantic import BaseModel

from open_deep_research.evaluation import qwen_judge
from open_deep_research.evaluation.qwen_judge import (
    JudgeTokenUsage,
    QwenJudgeAdapter,
    QwenJudgeConfigurationError,
    QwenJudgeResponseError,
    UnknownJudgeUsageError,
    build_deepeval_qwen_model,
)

ENV = {
    "RESEARCH_MODEL": "openai:qwen3.7-plus",
    "OPENAI_API_KEY": "unit-test-secret-value",
    "OPENAI_API_BASE": "https://private-provider.example/v1",
}


class Verdict(BaseModel):
    passed: bool


class FakeMessage:
    def __init__(self, content="plain", usage=True):
        self.content = content
        self.usage_metadata = (
            {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
            if usage
            else None
        )


class FakeStructuredRunnable:
    def __init__(self, owner):
        self.owner = owner

    def invoke(self, prompt):
        self.owner.prompts.append(("schema_sync", prompt))
        if self.owner.error:
            raise self.owner.error
        return {
            "raw": FakeMessage(content="", usage=self.owner.usage),
            "parsed": self.owner.parsed,
            "parsing_error": self.owner.parsing_error,
        }

    async def ainvoke(self, prompt):
        self.owner.prompts.append(("schema_async", prompt))
        if self.owner.error:
            raise self.owner.error
        return {
            "raw": FakeMessage(content="", usage=self.owner.usage),
            "parsed": self.owner.parsed,
            "parsing_error": self.owner.parsing_error,
        }


class FakeChatModel:
    def __init__(self, *, usage=True, error=None, parsed=None, parsing_error=None):
        self.usage = usage
        self.error = error
        self.parsed = Verdict(passed=True) if parsed is None else parsed
        self.parsing_error = parsing_error
        self.prompts = []
        self.structured_calls = []

    def invoke(self, prompt):
        self.prompts.append(("plain_sync", prompt))
        if self.error:
            raise self.error
        return FakeMessage(usage=self.usage)

    async def ainvoke(self, prompt):
        self.prompts.append(("plain_async", prompt))
        if self.error:
            raise self.error
        return FakeMessage(usage=self.usage)

    def with_structured_output(self, schema, *, include_raw):
        self.structured_calls.append((schema, include_raw))
        return FakeStructuredRunnable(self)


class ReservationHandle:
    def __init__(self, ledger, request):
        self.ledger = ledger
        self.request = request

    def settle(self, usage, *, error_type):
        self.ledger.settlements.append((self.request, usage, error_type))


class ReservationLedger:
    def __init__(self):
        self.requests = []
        self.settlements = []

    def __call__(self, request):
        self.requests.append(request)
        return ReservationHandle(self, request)


def adapter_with_fake(fake, ledger=None, captured=None, **updates):
    observed = {} if captured is None else captured

    def factory(**kwargs):
        observed.update(kwargs)
        return fake

    values = {
        "environment": ENV,
        "chat_model_factory": factory,
        "reservation_callback": ledger,
    }
    values.update(updates)
    return QwenJudgeAdapter(**values), observed


def test_explicit_endpoint_and_stripped_api_model_are_not_in_audit_artifact():
    fake = FakeChatModel()
    adapter, captured = adapter_with_fake(fake)

    assert captured["model"] == "qwen3.7-plus"
    assert captured["base_url"] == ENV["OPENAI_API_BASE"]
    assert captured["api_key"] == ENV["OPENAI_API_KEY"]
    assert captured["max_tokens"] == 2048
    assert captured["max_retries"] == 0
    assert captured["extra_body"] == {"enable_thinking": False}
    artifact = json.dumps(adapter.audit_metadata(), sort_keys=True)
    assert ENV["OPENAI_API_KEY"] not in artifact
    assert ENV["OPENAI_API_BASE"] not in artifact
    assert ENV["OPENAI_API_KEY"] not in repr(adapter)
    assert ENV["OPENAI_API_BASE"] not in repr(adapter)
    assert adapter.get_model_name() == "openai:qwen3.7-plus"


def test_dotenv_is_supported_but_environment_takes_precedence(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "RESEARCH_MODEL=openai:qwen-from-file\n"
        "OPENAI_API_KEY=file-secret\n"
        "OPENAI_API_BASE=https://file-provider.example/v1\n",
        encoding="utf-8",
    )
    fake = FakeChatModel()
    adapter, captured = adapter_with_fake(fake, dotenv_path=env_file)

    assert captured["model"] == "qwen3.7-plus"
    assert captured["base_url"] == ENV["OPENAI_API_BASE"]
    assert adapter.audit_metadata()["base_url_configured"] is True


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@private-provider.example/v1",
        "https://private-provider.example/v1?token=secret",
        "https://private-provider.example/v1#secret",
    ],
)
def test_endpoint_rejects_embedded_credentials_query_and_fragment(base_url):
    environment = {**ENV, "OPENAI_API_BASE": base_url}

    with pytest.raises(QwenJudgeConfigurationError, match="must not contain"):
        adapter_with_fake(FakeChatModel(), environment=environment)


def test_sync_and_async_plain_and_schema_calls_reserve_and_settle_usage():
    fake = FakeChatModel()
    ledger = ReservationLedger()
    adapter, _ = adapter_with_fake(fake, ledger=ledger)

    assert adapter.generate("plain sync") == "plain"
    assert adapter.generate("schema sync", schema=Verdict) == Verdict(passed=True)
    assert asyncio.run(adapter.a_generate("plain async")) == "plain"
    assert asyncio.run(adapter.a_generate("schema async", schema=Verdict)) == Verdict(
        passed=True
    )

    assert len(ledger.requests) == len(ledger.settlements) == 4
    assert all(request.audit_model_id == "openai:qwen3.7-plus" for request in ledger.requests)
    schema_bytes = len(
        json.dumps(
            Verdict.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    assert ledger.requests[0].input_upper_bound == (
        len("plain sync".encode("utf-8"))
        + qwen_judge._PROTOCOL_INPUT_TOKEN_MARGIN
    )
    assert ledger.requests[1].input_upper_bound == (
        len("schema sync".encode("utf-8"))
        + schema_bytes
        + qwen_judge._PROTOCOL_INPUT_TOKEN_MARGIN
    )
    assert all(request.max_output_tokens == 2048 for request in ledger.requests)
    assert all(
        request.output_upper_bound
        == request.max_output_tokens + qwen_judge._PROTOCOL_OUTPUT_TOKEN_MARGIN
        for request in ledger.requests
    )
    assert all(
        usage == JudgeTokenUsage(input_tokens=11, output_tokens=7)
        and error_type is None
        for _, usage, error_type in ledger.settlements
    )
    assert fake.structured_calls == [(Verdict, True), (Verdict, True)]


def test_unknown_usage_settles_unknown_and_fails_closed():
    ledger = ReservationLedger()
    adapter, _ = adapter_with_fake(FakeChatModel(usage=False), ledger=ledger)

    with pytest.raises(UnknownJudgeUsageError, match="usage_metadata"):
        adapter.generate("missing usage")

    assert len(ledger.settlements) == 1
    _, usage, error_type = ledger.settlements[0]
    assert usage is None
    assert error_type == "UnknownJudgeUsageError"


def test_schema_error_with_known_usage_is_settled_as_an_error():
    ledger = ReservationLedger()
    adapter, _ = adapter_with_fake(
        FakeChatModel(parsed=False, parsing_error=ValueError("invalid schema")),
        ledger=ledger,
    )

    with pytest.raises(QwenJudgeResponseError, match="satisfy schema"):
        adapter.generate("invalid structured output", schema=Verdict)

    assert len(ledger.settlements) == 1
    _, usage, error_type = ledger.settlements[0]
    assert usage == JudgeTokenUsage(input_tokens=11, output_tokens=7)
    assert error_type == "QwenJudgeResponseError"


def test_runner_persists_known_usage_error_settlement():
    from open_deep_research.evaluation.calibration_runner import _judge_reserver
    from open_deep_research.evaluation.live_budget import LiveTokenReservationLedger

    ledger = LiveTokenReservationLedger(
        hard_token_limit=10_000,
        per_run_token_limit=10_000,
    )
    snapshots = []
    reserve = _judge_reserver(
        ledger=ledger,
        run_id="run-known-error",
        persist_budget=snapshots.append,
    )
    handle = reserve(
        qwen_judge.JudgeReservationRequest(
            call_id="known-error",
            audit_model_id="openai:qwen3.7-plus",
            input_upper_bound=200,
            max_output_tokens=100,
            output_upper_bound=125,
        )
    )

    handle.settle(
        JudgeTokenUsage(input_tokens=120, output_tokens=20),
        error_type="QwenJudgeResponseError",
    )

    final = ledger.snapshot()
    assert snapshots[-1] == final
    assert final["committed_tokens"] == 140
    assert final["unknown_usage"] is False
    assert final["error_calls"] == 1
    assert final["active_reservations"] == []
    assert final["fail_closed_reason"] == "model_call_error:QwenJudgeResponseError"


def test_runner_persists_ledger_mutation_when_settlement_raises():
    from open_deep_research.evaluation.calibration_runner import _judge_reserver
    from open_deep_research.evaluation.live_budget import (
        LiveTokenBudgetFailClosed,
        LiveTokenReservationLedger,
    )

    ledger = LiveTokenReservationLedger(
        hard_token_limit=10_000,
        per_run_token_limit=10_000,
    )
    snapshots = []
    reserve = _judge_reserver(
        ledger=ledger,
        run_id="run-overflow",
        persist_budget=snapshots.append,
    )
    handle = reserve(
        qwen_judge.JudgeReservationRequest(
            call_id="overflow",
            audit_model_id="openai:qwen3.7-plus",
            input_upper_bound=100,
            max_output_tokens=20,
            output_upper_bound=30,
        )
    )

    with pytest.raises(LiveTokenBudgetFailClosed, match="exceeded"):
        handle.settle(
            JudgeTokenUsage(input_tokens=50, output_tokens=31),
            error_type=None,
        )

    final = ledger.snapshot()
    assert snapshots[-1] == final
    assert final["committed_tokens"] == 81
    assert final["error_calls"] == 1
    assert final["active_reservations"] == []
    assert final["fail_closed_reason"] == "actual_usage_exceeded_reservation"


def test_provider_exception_is_settled_once_and_original_error_is_preserved():
    class ProviderFailure(RuntimeError):
        pass

    failure = ProviderFailure("redacted provider failure")
    ledger = ReservationLedger()
    adapter, _ = adapter_with_fake(
        FakeChatModel(error=failure), ledger=ledger
    )

    with pytest.raises(ProviderFailure) as exc_info:
        adapter.generate("fails")

    assert exc_info.value is failure
    assert len(ledger.settlements) == 1
    _, usage, error_type = ledger.settlements[0]
    assert usage is None
    assert error_type == "ProviderFailure"


@pytest.mark.parametrize("async_call", [False, True])
def test_length_error_with_completion_usage_is_settled_exactly(async_call):
    class Usage:
        prompt_tokens = 321
        completion_tokens = 2048
        total_tokens = 2369

    class Completion:
        usage = Usage()

    class LengthFinishReasonError(RuntimeError):
        def __init__(self):
            super().__init__("truncated structured output")
            self.completion = Completion()

    failure = LengthFinishReasonError()
    ledger = ReservationLedger()
    adapter, _ = adapter_with_fake(
        FakeChatModel(error=failure), ledger=ledger
    )

    with pytest.raises(LengthFinishReasonError) as exc_info:
        if async_call:
            asyncio.run(adapter.a_generate("too long"))
        else:
            adapter.generate("too long")

    assert exc_info.value is failure
    assert len(ledger.settlements) == 1
    _, usage, error_type = ledger.settlements[0]
    assert usage == JudgeTokenUsage(input_tokens=321, output_tokens=2048)
    assert error_type == "LengthFinishReasonError"


@pytest.mark.parametrize("async_call", [False, True])
def test_structured_length_error_with_completion_usage_is_settled_exactly(
    async_call,
):
    class Usage:
        prompt_tokens = 777
        completion_tokens = 2048
        total_tokens = 2825

    class Completion:
        usage = Usage()

    class LengthFinishReasonError(RuntimeError):
        def __init__(self):
            super().__init__("truncated structured output")
            self.completion = Completion()

    failure = LengthFinishReasonError()
    ledger = ReservationLedger()
    adapter, _ = adapter_with_fake(
        FakeChatModel(error=failure), ledger=ledger
    )

    with pytest.raises(LengthFinishReasonError) as exc_info:
        if async_call:
            asyncio.run(adapter.a_generate("too long", schema=Verdict))
        else:
            adapter.generate("too long", schema=Verdict)

    assert exc_info.value is failure
    assert len(ledger.settlements) == 1
    _, usage, error_type = ledger.settlements[0]
    assert usage == JudgeTokenUsage(input_tokens=777, output_tokens=2048)
    assert error_type == "LengthFinishReasonError"


def test_length_error_with_invalid_completion_usage_remains_unknown():
    class Usage:
        prompt_tokens = 321
        completion_tokens = 2048
        total_tokens = 1

    class Completion:
        usage = Usage()

    class LengthFinishReasonError(RuntimeError):
        def __init__(self):
            super().__init__("truncated structured output")
            self.completion = Completion()

    ledger = ReservationLedger()
    adapter, _ = adapter_with_fake(
        FakeChatModel(error=LengthFinishReasonError()), ledger=ledger
    )

    with pytest.raises(LengthFinishReasonError):
        adapter.generate("too long")

    _, usage, error_type = ledger.settlements[0]
    assert usage is None
    assert error_type == "LengthFinishReasonError"


def test_deepeval_factory_is_lazy_and_preserves_audit_model_id(monkeypatch):
    guard_events = []

    @contextmanager
    def guarded_import():
        guard_events.append("enter")
        try:
            yield
        finally:
            guard_events.append("exit")

    class FakeDeepEvalBaseLLM:
        def __init_subclass__(cls, **kwargs):
            del kwargs
            guard_events.append("subclass")
            assert guard_events[0] == "enter"
            assert "exit" not in guard_events

        def __init__(self, model=None):
            guard_events.append("instance")
            assert "exit" not in guard_events
            self.name = model
            self.model = self.load_model()

    monkeypatch.setattr(
        qwen_judge, "_load_deepeval_base_llm", lambda: FakeDeepEvalBaseLLM
    )
    monkeypatch.setattr(qwen_judge, "_guarded_deepeval_import", guarded_import)
    adapter, _ = adapter_with_fake(FakeChatModel())

    wrapped = build_deepeval_qwen_model(adapter)

    assert isinstance(wrapped, FakeDeepEvalBaseLLM)
    assert guard_events == ["enter", "subclass", "instance", "exit"]
    assert wrapped.get_model_name() == "openai:qwen3.7-plus"
    assert wrapped.generate("plain") == "plain"
    assert asyncio.run(wrapped.a_generate("schema", schema=Verdict)) == Verdict(
        passed=True
    )
