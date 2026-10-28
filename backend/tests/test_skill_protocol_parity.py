from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.new_detail_skill_provider import BundledSkillManifestValidator
from app.skill_protocol import (
    MAX_PROTOCOL_DOCUMENT_BYTES,
    SUPPORTED_SINGLE_IMAGE_SKILLS,
    SkillProtocolError,
    bundled_skill_source_root,
    load_skill_protocol,
)


@pytest.mark.parametrize("skill_id", sorted(SUPPORTED_SINGLE_IMAGE_SKILLS))
def test_bundled_protocol_preserves_every_authoritative_markdown_byte(
    skill_id: str,
) -> None:
    root = bundled_skill_source_root(skill_id)
    content_hash = BundledSkillManifestValidator().validate(skill_id)

    protocol = load_skill_protocol(
        skill_id,
        source_content_hash=content_hash,
    )

    expected_paths = [root / "SKILL.md"]
    references = root / "references"
    if references.is_dir():
        expected_paths.extend(
            sorted(
                references.glob("*.md"),
                key=lambda value: value.relative_to(root).as_posix(),
            )
        )
    expected_relative_paths = [
        path.relative_to(root).as_posix() for path in expected_paths
    ]
    assert [document.path for document in protocol.documents] == (
        expected_relative_paths
    )
    assert protocol.documents[0].path == "SKILL.md"
    assert all(
        document.path == "SKILL.md"
        or document.path.startswith("references/")
        for document in protocol.documents
    )
    assert not any(
        document.path.startswith(("agents/", "scripts/"))
        for document in protocol.documents
    )

    expected_total = 0
    aggregate = hashlib.sha256()
    for document, path in zip(protocol.documents, expected_paths, strict=True):
        payload = path.read_bytes()
        expected_total += len(payload)
        assert document.content == payload.decode("utf-8")
        assert document.byte_length == len(payload)
        assert document.sha256 == hashlib.sha256(payload).hexdigest()
        aggregate.update(document.path.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(payload)
        aggregate.update(b"\0")

    assert protocol.source_content_hash == content_hash
    assert protocol.byte_length == expected_total
    assert protocol.sha256 == aggregate.hexdigest()
    payload = protocol.prompt_payload()
    assert payload["source_content_hash"] == content_hash
    assert payload["documents"] == [
        {"path": document.path, "content": document.content}
        for document in protocol.documents
    ]


def test_protocol_loading_is_deterministic() -> None:
    skill_id = "xinghe-wanneng-shengtu-3-0"
    content_hash = BundledSkillManifestValidator().validate(skill_id)

    first = load_skill_protocol(skill_id, source_content_hash=content_hash)
    second = load_skill_protocol(skill_id, source_content_hash=content_hash)

    assert first == second


def test_protocol_fails_closed_when_a_document_exceeds_the_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"x" * (MAX_PROTOCOL_DOCUMENT_BYTES + 1))

    with pytest.raises(SkillProtocolError) as raised:
        load_skill_protocol(
            "xinghe-wanneng-shengtu-3-0",
            source_content_hash="a" * 64,
            source_root=root,
        )

    assert raised.value.code == "SKILL_PROTOCOL_SIZE_INVALID"


def test_protocol_fails_closed_for_non_utf8_source(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"\xff\xfe")

    with pytest.raises(SkillProtocolError) as raised:
        load_skill_protocol(
            "xinghe-wanneng-shengtu-3-0",
            source_content_hash="a" * 64,
            source_root=root,
        )

    assert raised.value.code == "SKILL_PROTOCOL_ENCODING_INVALID"


def test_protocol_rejects_a_required_document_outside_its_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        os.symlink(outside, root / "SKILL.md")
    except OSError:
        pytest.skip("This host does not permit test symlink creation.")

    with pytest.raises(SkillProtocolError) as raised:
        load_skill_protocol(
            "xinghe-wanneng-shengtu-3-0",
            source_content_hash="a" * 64,
            source_root=root,
        )

    assert raised.value.code == "SKILL_PROTOCOL_PATH_INVALID"


def test_unsupported_skill_cannot_select_an_arbitrary_source(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("unsafe", encoding="utf-8")

    with pytest.raises(SkillProtocolError) as raised:
        load_skill_protocol(
            "arbitrary-skill",
            source_content_hash="a" * 64,
            source_root=tmp_path,
        )

    assert raised.value.code == "SKILL_PROTOCOL_UNSUPPORTED"
