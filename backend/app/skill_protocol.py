"""Load immutable, prompt-safe protocol text from bundled Skill sources.

Only ``SKILL.md`` and direct ``references/*.md`` documents are eligible.  The
loader deliberately excludes agent configuration and executable source files.
Callers must validate the complete manifest/source tree before using this
module; this module provides the second, narrow boundary around the text that
is actually sent to a model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SINGLE_IMAGE_SKILLS = frozenset(
    {
        "aurora-remix-fast",
        "aurora-ecommerce-reference-remix",
        "all-category-style-benchmark-still",
        "xinghe-amazon-visual-suite",
        "xinghe-ecommerce-creative-image",
        "xinghe-reference-image-remix-3",
        "reference-visual-reverse-remix",
        "ecommerce-model-pose-transfer",
        "xinghe-wanneng-shengtu-3-0",
    }
)

MAX_PROTOCOL_DOCUMENT_BYTES = 64 * 1024
MAX_PROTOCOL_TOTAL_BYTES = 128 * 1024


class SkillProtocolError(RuntimeError):
    """A stable local failure raised before any model request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SkillProtocolDocument:
    path: str
    content: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class SkillProtocol:
    skill_id: str
    source_content_hash: str
    documents: tuple[SkillProtocolDocument, ...]
    byte_length: int
    sha256: str

    def prompt_payload(self) -> dict[str, object]:
        """Return the exact source text with explicit, stable path boundaries."""

        return {
            "schema_version": "skill-protocol-v1",
            "skill_id": self.skill_id,
            "source_content_hash": self.source_content_hash,
            "documents": [
                {"path": document.path, "content": document.content}
                for document in self.documents
            ],
        }


def bundled_skill_source_root(skill_id: str) -> Path:
    """Return only the repository-owned bundled source for a supported Skill."""

    _require_supported_skill(skill_id)
    return Path(__file__).resolve().parents[1] / "skill_sources" / skill_id


def load_skill_protocol(
    skill_id: str,
    *,
    source_content_hash: str,
    source_root: Path | None = None,
) -> SkillProtocol:
    """Read the complete Markdown protocol in deterministic path order.

    ``SKILL.md`` is always first, followed by direct ``references/*.md`` files
    sorted by their POSIX relative paths.  Every resolved file must remain
    inside the resolved Skill root and all reads are subject to per-document
    and aggregate byte limits.
    """

    _require_supported_skill(skill_id)
    if (
        len(source_content_hash) != 64
        or any(character not in "0123456789abcdef" for character in source_content_hash)
    ):
        raise SkillProtocolError(
            "SKILL_PROTOCOL_CONTENT_HASH_INVALID",
            "The verified Skill source content hash is invalid.",
        )
    root_candidate = source_root or bundled_skill_source_root(skill_id)
    try:
        root = root_candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SkillProtocolError(
            "SKILL_PROTOCOL_SOURCE_MISSING",
            f"Skill protocol source is unavailable ({type(exc).__name__}).",
        ) from None
    if not root.is_dir():
        raise SkillProtocolError(
            "SKILL_PROTOCOL_SOURCE_MISSING",
            "Skill protocol source is not a directory.",
        )
    if source_root is None:
        bundled_parent = (
            Path(__file__).resolve().parents[1] / "skill_sources"
        ).resolve(strict=True)
        if not root.is_relative_to(bundled_parent):
            raise SkillProtocolError(
                "SKILL_PROTOCOL_PATH_INVALID",
                "Bundled Skill protocol source escaped the repository source root.",
            )

    paths = [_bounded_file(root, root / "SKILL.md")]
    references = root / "references"
    if references.exists():
        try:
            resolved_references = references.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SkillProtocolError(
                "SKILL_PROTOCOL_PATH_INVALID",
                f"Skill reference path is invalid ({type(exc).__name__}).",
            ) from None
        if not resolved_references.is_relative_to(root) or not references.is_dir():
            raise SkillProtocolError(
                "SKILL_PROTOCOL_PATH_INVALID",
                "Skill references must remain inside the bundled source root.",
            )
        paths.extend(
            _bounded_file(root, path)
            for path in sorted(
                references.glob("*.md"),
                key=lambda value: value.relative_to(root).as_posix(),
            )
        )

    documents: list[SkillProtocolDocument] = []
    total_bytes = 0
    aggregate = hashlib.sha256()
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise SkillProtocolError(
                "SKILL_PROTOCOL_READ_FAILED",
                f"Skill protocol document could not be read ({type(exc).__name__}).",
            ) from None
        if not payload or len(payload) > MAX_PROTOCOL_DOCUMENT_BYTES:
            raise SkillProtocolError(
                "SKILL_PROTOCOL_SIZE_INVALID",
                "A Skill protocol document is empty or exceeds its byte limit.",
            )
        total_bytes += len(payload)
        if total_bytes > MAX_PROTOCOL_TOTAL_BYTES:
            raise SkillProtocolError(
                "SKILL_PROTOCOL_SIZE_INVALID",
                "The Skill protocol exceeds its aggregate byte limit.",
            )
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise SkillProtocolError(
                "SKILL_PROTOCOL_ENCODING_INVALID",
                "Skill protocol documents must be valid UTF-8.",
            ) from None
        document_hash = hashlib.sha256(payload).hexdigest()
        documents.append(
            SkillProtocolDocument(
                path=relative_path,
                content=content,
                byte_length=len(payload),
                sha256=document_hash,
            )
        )
        aggregate.update(relative_path.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(payload)
        aggregate.update(b"\0")

    return SkillProtocol(
        skill_id=skill_id,
        source_content_hash=source_content_hash,
        documents=tuple(documents),
        byte_length=total_bytes,
        sha256=aggregate.hexdigest(),
    )


def _require_supported_skill(skill_id: str) -> None:
    if skill_id not in SUPPORTED_SINGLE_IMAGE_SKILLS:
        raise SkillProtocolError(
            "SKILL_PROTOCOL_UNSUPPORTED",
            "The requested Skill has no single-image protocol binding.",
        )


def _bounded_file(root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SkillProtocolError(
            "SKILL_PROTOCOL_SOURCE_MISSING",
            (
                "A required Skill protocol document is unavailable "
                f"({type(exc).__name__})."
            ),
        ) from None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise SkillProtocolError(
            "SKILL_PROTOCOL_PATH_INVALID",
            "Skill protocol documents must remain inside the bundled source root.",
        )
    return resolved
