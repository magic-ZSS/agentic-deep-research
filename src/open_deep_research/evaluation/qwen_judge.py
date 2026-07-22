"""Budget-aware OpenAI-compatible Qwen judge used by paid evaluation only.

The adapter is project-owned so DeepEval never has to infer the provider from a
model string.  Credentials and the complete endpoint are kept behind
``SecretStr`` values and are never included in audit metadata.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from pydantic import BaseModel, SecretStr

from open_deep_research.evaluation.deepeval_adapter import (
    EXPECTED_DEEPEVAL_VERSION,
    DeepEvalUnavailableError,
    _guarded_deepeval_import,
    deepeval_version,
)

_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9._:/-]+$")
_PROTOCOL_INPUT_TOKEN_MARGIN = 4_096
_PROTOCOL_OUTPUT_TOKEN_MARGIN = 256


class QwenJudgeConfigurationError(RuntimeError):
    """The judge cannot be created without exposing or guessing configuration."""


class UnknownJudgeUsageError(RuntimeError):
    """A paid judge response omitted complete token usage and must fail closed."""


class QwenJudgeResponseError(RuntimeError):
    """The provider response could not be projected to the requested result."""


@dataclass(frozen=True, slots=True)
class JudgeTokenUsage:
    """Complete token usage for exactly one provider call."""

    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        """Return the complete provider-reported token count."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class JudgeReservationRequest:
    """Non-sensitive information supplied before dispatching a paid call."""

    call_id: str
    audit_model_id: str
    input_upper_bound: int
    max_output_tokens: int
    output_upper_bound: int


class JudgeReservation(Protocol):
    """A caller-owned budget reservation settled exactly once by the adapter."""

    def settle(
        self,
        usage: JudgeTokenUsage | None,
        *,
        error_type: str | None,
    ) -> None:
        """Settle one call; ``error_type`` remains authoritative with known usage."""


ReservationCallback = Callable[[JudgeReservationRequest], JudgeReservation]
ChatModelFactory = Callable[..., Any]


class _NoopReservation:
    def settle(
        self,
        usage: JudgeTokenUsage | None,
        *,
        error_type: str | None,
    ) -> None:
        del usage, error_type


def _noop_reservation(_: JudgeReservationRequest) -> JudgeReservation:
    return _NoopReservation()


class _ReservationGuard:
    """Prevent an error path from settling the same reservation twice."""

    def __init__(self, reservation: JudgeReservation) -> None:
        self._reservation = reservation
        self.settled = False

    def settle(
        self,
        usage: JudgeTokenUsage | None,
        *,
        error_type: str | None,
    ) -> None:
        if self.settled:
            return
        self.settled = True
        self._reservation.settle(usage, error_type=error_type)


@dataclass(frozen=True, slots=True)
class _QwenJudgeSettings:
    """Runtime-only sensitive settings with a redacted representation."""

    audit_model_id: str
    api_model_id: str
    api_key: SecretStr
    base_url: SecretStr

    def __repr__(self) -> str:
        return (
            "_QwenJudgeSettings("
            f"audit_model_id={self.audit_model_id!r}, "
            f"api_model_id={self.api_model_id!r}, "
            "api_key=SecretStr('**********'), "
            "base_url=SecretStr('**********'))"
        )


def _merged_environment(
    environment: Mapping[str, str] | None,
    dotenv_path: str | Path | None,
) -> dict[str, str]:
    """Load an optional dotenv file and let the process environment win."""
    values: dict[str, str] = {}
    if dotenv_path is not None:
        path = Path(dotenv_path)
        if path.is_file():
            values.update(
                {
                    str(key): str(value)
                    for key, value in dotenv_values(path).items()
                    if value is not None
                }
            )
    source = os.environ if environment is None else environment
    values.update({str(key): str(value) for key, value in source.items()})
    return values


def _settings_from_environment(
    *,
    audit_model_id: str | None,
    environment: Mapping[str, str] | None,
    dotenv_path: str | Path | None,
) -> _QwenJudgeSettings:
    values = _merged_environment(environment, dotenv_path)
    resolved_model = (
        (audit_model_id or "").strip()
        or values.get("EVALUATION_JUDGE_MODEL", "").strip()
        or values.get("RESEARCH_MODEL", "").strip()
    )
    if not resolved_model:
        raise QwenJudgeConfigurationError(
            "missing EVALUATION_JUDGE_MODEL and RESEARCH_MODEL"
        )
    if not _SAFE_MODEL_ID.fullmatch(resolved_model):
        raise QwenJudgeConfigurationError("judge model identifier has an unsafe format")
    if not resolved_model.lower().startswith("openai:"):
        raise QwenJudgeConfigurationError(
            "Qwen judge model must use the openai: provider prefix"
        )
    api_model_id = resolved_model.split(":", 1)[1]
    if "qwen" not in api_model_id.lower():
        raise QwenJudgeConfigurationError("judge model must identify a Qwen model")

    api_key = values.get("OPENAI_API_KEY", "").strip()
    base_url = values.get("OPENAI_API_BASE", "").strip()
    if not api_key:
        raise QwenJudgeConfigurationError("missing OPENAI_API_KEY")
    if not base_url:
        raise QwenJudgeConfigurationError("missing OPENAI_API_BASE")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise QwenJudgeConfigurationError("OPENAI_API_BASE is not a valid HTTP endpoint")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise QwenJudgeConfigurationError(
            "OPENAI_API_BASE must not contain credentials, query, or fragment"
        )
    return _QwenJudgeSettings(
        audit_model_id=resolved_model,
        api_model_id=api_model_id,
        api_key=SecretStr(api_key),
        base_url=SecretStr(base_url.rstrip("/")),
    )


def _default_chat_model_factory(**kwargs: Any) -> Any:
    """Import the provider client only when a paid adapter is constructed."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(**kwargs)


def _usage_from_message(message: Any) -> JudgeTokenUsage | None:
    metadata = getattr(message, "usage_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    input_tokens = metadata.get("input_tokens")
    output_tokens = metadata.get("output_tokens")
    if (
        isinstance(input_tokens, bool)
        or isinstance(output_tokens, bool)
        or not isinstance(input_tokens, int)
        or not isinstance(output_tokens, int)
        or input_tokens < 0
        or output_tokens < 0
    ):
        return None
    total = metadata.get("total_tokens")
    if total is not None and (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total != input_tokens + output_tokens
    ):
        return None
    return JudgeTokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _usage_from_exception(error: BaseException) -> JudgeTokenUsage | None:
    """Recover provider-reported usage from a failed completion when available.

    OpenAI-compatible clients attach the terminal completion to errors such as
    ``LengthFinishReasonError``.  Treating that known usage as unknown would
    conservatively charge the whole reservation and permanently obscure the
    actual cost.  This remains duck-typed so importing the optional OpenAI SDK
    is not required by offline evaluation paths.
    """
    completion = getattr(error, "completion", None)
    usage = (
        completion.get("usage")
        if isinstance(completion, Mapping)
        else getattr(completion, "usage", None)
    )
    if usage is None:
        return None

    def value(name: str) -> Any:
        if isinstance(usage, Mapping):
            return usage.get(name)
        return getattr(usage, name, None)

    input_tokens = value("prompt_tokens")
    output_tokens = value("completion_tokens")
    total_tokens = value("total_tokens")
    if (
        isinstance(input_tokens, bool)
        or isinstance(output_tokens, bool)
        or not isinstance(input_tokens, int)
        or not isinstance(output_tokens, int)
        or input_tokens < 0
        or output_tokens < 0
    ):
        return None
    if total_tokens is not None and (
        isinstance(total_tokens, bool)
        or not isinstance(total_tokens, int)
        or total_tokens != input_tokens + output_tokens
    ):
        return None
    return JudgeTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _serialized_schema_size(schema: type[BaseModel] | None) -> int:
    """Return a conservative byte-based token bound for an injected JSON schema."""
    if schema is None:
        return 0
    try:
        serialized = json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise QwenJudgeConfigurationError(
            "judge structured-output schema is not serializable"
        ) from exc
    return len(serialized.encode("utf-8"))


class QwenJudgeAdapter:
    """Plain/schema sync+async generation with fail-closed usage accounting."""

    def __init__(
        self,
        *,
        audit_model_id: str | None = None,
        environment: Mapping[str, str] | None = None,
        dotenv_path: str | Path | None = None,
        reservation_callback: ReservationCallback | None = None,
        chat_model_factory: ChatModelFactory | None = None,
        max_output_tokens: int = 2048,
        timeout_seconds: float = 60.0,
    ) -> None:
        """Resolve secrets privately and create a zero-retry provider client."""
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        settings = _settings_from_environment(
            audit_model_id=audit_model_id,
            environment=environment,
            dotenv_path=dotenv_path,
        )
        factory = chat_model_factory or _default_chat_model_factory
        self._audit_model_id = settings.audit_model_id
        self._max_output_tokens = max_output_tokens
        self._reserve = reservation_callback or _noop_reservation
        self._chat_model = factory(
            model=settings.api_model_id,
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url.get_secret_value(),
            max_tokens=max_output_tokens,
            timeout=timeout_seconds,
            max_retries=0,
            temperature=0,
            extra_body={"enable_thinking": False},
        )

    def __repr__(self) -> str:
        """Return a representation containing only safe audit settings."""
        return (
            "QwenJudgeAdapter("
            f"audit_model_id={self._audit_model_id!r}, "
            f"max_output_tokens={self._max_output_tokens})"
        )

    def get_model_name(self) -> str:
        """Return the provider-prefixed audit identifier, never credentials."""
        return self._audit_model_id

    def audit_metadata(self) -> dict[str, Any]:
        """Return the only settings safe to persist in evaluation artifacts."""
        return {
            "model_id": self._audit_model_id,
            "provider": "openai_compatible_qwen",
            "base_url_configured": True,
            "max_output_tokens": self._max_output_tokens,
            "max_retries": 0,
        }

    def _reservation(
        self, prompt: str, schema: type[BaseModel] | None
    ) -> _ReservationGuard:
        # UTF-8 bytes conservatively bound tokenizer output. Structured calls
        # also inject their JSON schema outside the visible prompt, while the
        # fixed margin covers provider message/tool wrappers.
        input_upper_bound = (
            len(prompt.encode("utf-8"))
            + _serialized_schema_size(schema)
            + _PROTOCOL_INPUT_TOKEN_MARGIN
        )
        reservation = self._reserve(
            JudgeReservationRequest(
                call_id=str(uuid4()),
                audit_model_id=self._audit_model_id,
                input_upper_bound=input_upper_bound,
                max_output_tokens=self._max_output_tokens,
                output_upper_bound=(
                    self._max_output_tokens + _PROTOCOL_OUTPUT_TOKEN_MARGIN
                ),
            )
        )
        if not hasattr(reservation, "settle"):
            raise TypeError("reservation callback must return a settlement handle")
        return _ReservationGuard(reservation)

    @staticmethod
    def _settle_exception(
        reservation: _ReservationGuard, error: BaseException
    ) -> None:
        if reservation.settled:
            return
        try:
            reservation.settle(
                _usage_from_exception(error),
                error_type=type(error).__name__,
            )
        except BaseException as settlement_error:
            error.add_note(
                "judge reservation settlement also failed: "
                f"{type(settlement_error).__name__}"
            )

    @staticmethod
    def _complete(
        reservation: _ReservationGuard,
        raw: Any,
        value: Any,
        parsing_error: Any = None,
    ) -> Any:
        usage = _usage_from_message(raw)
        if usage is None:
            usage_error = UnknownJudgeUsageError(
                "judge response omitted complete usage_metadata"
            )
            reservation.settle(None, error_type=type(usage_error).__name__)
            raise usage_error
        if parsing_error is not None or value is None:
            response_error = QwenJudgeResponseError(
                "judge response could not satisfy schema"
            )
            reservation.settle(usage, error_type=type(response_error).__name__)
            raise response_error
        reservation.settle(usage, error_type=None)
        return value

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        """Perform one synchronous judge call and settle its exact usage."""
        reservation = self._reservation(prompt, schema)
        try:
            if schema is None:
                response = self._chat_model.invoke(prompt)
                return self._complete(
                    reservation,
                    response,
                    getattr(response, "content", None),
                )
            runnable = self._chat_model.with_structured_output(
                schema, include_raw=True
            )
            result = runnable.invoke(prompt)
            if not isinstance(result, Mapping):
                response_error = QwenJudgeResponseError(
                    "structured judge did not return include_raw payload"
                )
                reservation.settle(
                    None, error_type=type(response_error).__name__
                )
                raise response_error
            return self._complete(
                reservation,
                result.get("raw"),
                result.get("parsed"),
                result.get("parsing_error"),
            )
        except BaseException as error:
            if isinstance(error, UnknownJudgeUsageError | QwenJudgeResponseError):
                raise
            self._settle_exception(reservation, error)
            raise

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> Any:
        """Perform one asynchronous judge call and settle its exact usage."""
        reservation = self._reservation(prompt, schema)
        try:
            if schema is None:
                response = await self._chat_model.ainvoke(prompt)
                return self._complete(
                    reservation,
                    response,
                    getattr(response, "content", None),
                )
            runnable = self._chat_model.with_structured_output(
                schema, include_raw=True
            )
            result = await runnable.ainvoke(prompt)
            if not isinstance(result, Mapping):
                response_error = QwenJudgeResponseError(
                    "structured judge did not return include_raw payload"
                )
                reservation.settle(
                    None, error_type=type(response_error).__name__
                )
                raise response_error
            return self._complete(
                reservation,
                result.get("raw"),
                result.get("parsed"),
                result.get("parsing_error"),
            )
        except BaseException as error:
            if isinstance(error, UnknownJudgeUsageError | QwenJudgeResponseError):
                raise
            self._settle_exception(reservation, error)
            raise


def _load_deepeval_base_llm() -> type:
    """Import the optional DeepEval base class only inside the guarded boundary."""
    installed = deepeval_version()
    if installed != EXPECTED_DEEPEVAL_VERSION:
        raise DeepEvalUnavailableError(
            f"Qwen judge requires deepeval=={EXPECTED_DEEPEVAL_VERSION}; "
            f"installed={installed or 'missing'}"
        )
    with _guarded_deepeval_import():
        from deepeval.models import DeepEvalBaseLLM  # type: ignore[import-not-found]

        return DeepEvalBaseLLM


def build_deepeval_qwen_model(adapter: QwenJudgeAdapter) -> Any:
    """Wrap the project adapter in DeepEval's optional public model contract."""
    with _guarded_deepeval_import():
        base_class = _load_deepeval_base_llm()

        class DeepEvalQwenModel(base_class):  # type: ignore[misc, valid-type]
            def __init__(self, delegate: QwenJudgeAdapter) -> None:
                self._delegate = delegate
                super().__init__(model=delegate.get_model_name())

            def load_model(self, *args: Any, **kwargs: Any) -> QwenJudgeAdapter:
                del args, kwargs
                return self._delegate

            def generate(
                self, prompt: str, schema: type[BaseModel] | None = None
            ) -> Any:
                return self._delegate.generate(prompt, schema=schema)

            async def a_generate(
                self, prompt: str, schema: type[BaseModel] | None = None
            ) -> Any:
                return await self._delegate.a_generate(prompt, schema=schema)

            def get_model_name(self, *args: Any, **kwargs: Any) -> str:
                del args, kwargs
                return self._delegate.get_model_name()

        wrapped = DeepEvalQwenModel(adapter)
    if inspect.isawaitable(wrapped):
        raise TypeError("DeepEval model construction unexpectedly returned awaitable")
    return wrapped


__all__ = [
    "JudgeReservation",
    "JudgeReservationRequest",
    "JudgeTokenUsage",
    "QwenJudgeAdapter",
    "QwenJudgeConfigurationError",
    "QwenJudgeResponseError",
    "ReservationCallback",
    "UnknownJudgeUsageError",
    "build_deepeval_qwen_model",
]
