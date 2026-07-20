"""Canonicalization, SHA-256 content identity, and stable entity IDs."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


_ID_PREFIX = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonicalize_text(value: str) -> str:
    """Normalize logical text identity without changing stored raw bytes."""
    normalized = unicodedata.normalize("NFC", value)
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(content: bytes) -> str:
    """Hash the exact immutable byte snapshot; no text/HTML rewriting occurs."""
    return hashlib.sha256(content).hexdigest()


def validate_sha256(value: str) -> str:
    """Return a normalized SHA-256 hex digest or reject it."""
    normalized = value.lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError("expected a 64-character SHA-256 hexadecimal digest")
    return normalized


def canonicalize_uri(value: str) -> str:
    """Canonicalize a public URI for stable source identity."""
    value = canonicalize_text(value).strip()
    parts = urlsplit(value)
    if not parts.scheme:
        raise ValueError("canonical URI must include a scheme")
    if parts.username is not None or parts.password is not None:
        raise ValueError("source URIs must not contain credentials")

    scheme = parts.scheme.lower()
    if scheme in {"http", "https"}:
        if not parts.hostname:
            raise ValueError("HTTP source URI must include a host")
        host = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port
        if port is not None and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
        ):
            host = f"{host}:{port}"
        path = quote(unquote(parts.path or "/"), safe="/%:@-._~!$&'()*+,;=")
        query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
        return urlunsplit((scheme, host, path, query, ""))
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


def canonicalize_local_ref(value: str) -> str:
    """Normalize a storage identity lexically without accessing the filesystem."""
    normalized = canonicalize_text(value).strip().replace("\\", "/")
    if "\x00" in normalized:
        raise ValueError("storage reference contains a null byte")
    drive = ""
    windows_identity = normalized.startswith("//")
    if re.match(r"^[A-Za-z]:/", normalized):
        windows_identity = True
        drive, normalized = normalized[:2].lower(), normalized[2:]
        normalized = "/" + normalized.lstrip("/")
    normalized = posixpath.normpath(normalized)
    if normalized == ".":
        normalized = ""
    if windows_identity:
        normalized = normalized.casefold()
    return f"{drive}{normalized}"


def stable_id(prefix: str, *parts: object) -> str:
    """Build a deterministic full-length SHA-256 ID from explicit components."""
    if not _ID_PREFIX.fullmatch(prefix):
        raise ValueError("stable ID prefix must be lowercase snake_case")
    payload = json.dumps(
        [canonicalize_text(str(part)) for part in parts],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()}"


def scope_id_for(
    tenant_id: str,
    project_id: str,
    owner_user_id: str | None,
    visibility: str,
) -> str:
    return stable_id(
        "scope",
        tenant_id.strip(),
        project_id.strip(),
        owner_user_id.strip() if owner_user_id else "",
        visibility,
    )


def source_id_for(scope_id: str, kind: str, identity_key: str) -> str:
    return stable_id("src", scope_id, kind, identity_key)


def document_id_for(scope_id: str, source_id: str, logical_key: str) -> str:
    return stable_id("doc", scope_id, source_id, canonicalize_text(logical_key).strip())


def blob_id_for(scope_id: str, content_sha256: str) -> str:
    return stable_id("blob", scope_id, validate_sha256(content_sha256))


def version_id_for(scope_id: str, document_id: str, content_sha256: str) -> str:
    return stable_id("ver", scope_id, document_id, validate_sha256(content_sha256))


def chunk_id_for(
    scope_id: str,
    version_id: str,
    ordinal: int,
    text_sha256: str,
    locator_key: str,
) -> str:
    return stable_id(
        "chk", scope_id, version_id, ordinal, validate_sha256(text_sha256), locator_key
    )


def requirement_id_for(
    scope_id: str,
    run_id: str | None,
    template_id: str | None,
    text: str,
    parent_id: str | None,
) -> str:
    return stable_id(
        "req",
        scope_id,
        run_id or "",
        template_id or "",
        canonicalize_text(text).strip(),
        parent_id or "",
    )


def evidence_id_for(
    scope_id: str,
    chunk_id: str,
    requirement_id: str | None,
    excerpt: str,
    relation: str,
) -> str:
    return stable_id(
        "evd",
        scope_id,
        chunk_id,
        requirement_id or "",
        canonicalize_text(excerpt).strip(),
        relation,
    )
