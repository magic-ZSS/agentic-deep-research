"""Trusted, non-model-controlled runtime identity."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeIdentity(BaseModel):
    """Identity established by CLI/auth middleware, never by tool arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    auth_source: Literal["local_cli", "hosted_auth", "test"]

    @model_validator(mode="after")
    def normalize(self) -> Self:
        """Normalize and reject path-like identity fragments."""
        for name in ("tenant_id", "user_id", "project_id", "thread_id"):
            value = getattr(self, name).strip()
            if not value or any(char in value for char in ("/", "\\", "\x00")):
                raise ValueError(f"invalid runtime identity field: {name}")
            object.__setattr__(self, name, value)
        return self

    def namespace(self, memory_type: str) -> tuple[str, ...]:
        """Return the only authorized long-term memory namespace shape."""
        value = memory_type.strip().lower()
        if not value:
            raise ValueError("memory_type cannot be blank")
        return ("odr", self.tenant_id, self.user_id, self.project_id, value)

    def checkpoint_config(self) -> dict:
        """Return a LangGraph checkpoint configuration scoped to this thread."""
        return {"configurable": {"thread_id": self.thread_id}}
