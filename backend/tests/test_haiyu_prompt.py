import asyncio
import json

import pytest

from app.haiyu_prompt import ALIAS_INSTRUCTION, MAX_PROMPT_BYTES, fit_prompt
from app.haiyu_transport import HaiyuTransport


def json_sections(prompt):
    decoder = json.JSONDecoder()
    sections = []
    pos = 0
    while pos < len(prompt):
        if prompt[pos] in "[{":
            try:
                value, end = decoder.raw_decode(prompt, pos)
            except ValueError:
                pass
            else:
                sections.append(value)
                pos = end
                continue
        pos += 1
    return sections


def expand(value, legend):
    if isinstance(value, dict):
        return {legend.get(k, k): expand(v, legend) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, legend) for v in value]
    return value


def test_under_limit_is_byte_identical_including_chinese_boundary():
    prompt = "中" * 10_666 + "ab"
    assert len(prompt.encode()) == MAX_PROMPT_BYTES
    assert fit_prompt(prompt) == prompt
    with pytest.raises(ValueError, match="未发送"):
        fit_prompt(prompt + "c")


def test_json_whitespace_only_preserves_prose_copy_escapes_and_number_tokens():
    raw = (
        '{ "render_text" : "中 文  \\n  \\"引号\\"", '
        '"value" : 0.1234567890123456789012345 }'
    )
    prompt = "prose  spacing\n" + "\n".join([raw] * 330)
    assert len(json_sections(prompt)) == 330
    result = fit_prompt(prompt)
    assert not result.startswith(ALIAS_INSTRUCTION)
    assert json_sections(result) == json_sections(prompt)
    assert "prose  spacing\n" in result
    assert "0.1234567890123456789012345" in result
    assert '"中 文  \\n  \\"引号\\""' in result


def test_key_dictionary_is_reversible_and_never_rewrites_values():
    evidence = [
        {
            "approved_render_text": "中 文 保留全部内容 @0",
            "source_product_index": i,
            "source_evidence_region_identifier": "R001",
            "@0": {"approved_render_text": ""},
        }
        for i in range(250)
    ]
    prompt = "ALL EVIDENCE=" + json.dumps(evidence, ensure_ascii=False, indent=2)
    result = fit_prompt(prompt)
    assert result.startswith(ALIAS_INSTRUCTION)
    assert len(result.encode()) <= MAX_PROMPT_BYTES
    legend, restored = json_sections(result)
    assert "@0" not in legend
    assert expand(restored, legend) == evidence
    assert '"中 文 保留全部内容 @0"' in result
    assert fit_prompt(prompt) == result


def test_json_duplicate_keys_and_string_spacing_survive_minification():
    raw = '{ "same" : "first", "same" : "second  value" }'
    result = fit_prompt("\n".join([raw] * 680))
    assert result.count('"same"') == 1360
    assert result.count('"second  value"') == 680


class Delegate:
    def __init__(self):
        self.calls = []

    async def post_multipart(self, **kwargs):
        self.calls.append(kwargs)
        return 400, {"error": {"message": "synthetic rejection"}}


def wire(transport, prompt, *, url="https://api.haiyushuke.com/v1/images/edits"):
    return asyncio.run(
        transport.post_multipart(
            url=url,
            headers={"Idempotency-Key": "stable-budget-fixture"},
            fields=[("model", "image-2.5-flare"), ("prompt", prompt), ("n", "1")],
            files=[("image", "input.png", "image/png", b"unchanged image bytes")],
            timeout_seconds=10,
        )
    )


def test_transport_counts_appended_canvas_note_before_dispatch(tmp_path):
    delegate = Delegate()
    transport = HaiyuTransport(delegate, journal_dir=tmp_path)
    status, error = asyncio.run(
        transport.post_multipart(
            url="https://api.haiyushuke.com/v1/images/edits",
            headers={"Idempotency-Key": "stable-budget-fixture"},
            fields=[
                ("model", "image-2.5-flare"),
                ("prompt", "x" * 31_999),
                ("size", "1024x1024"),
            ],
            files=[("image", "input.png", "image/png", b"unchanged image bytes")],
            timeout_seconds=10,
        )
    )
    assert status == 400
    assert error["error"]["code"] == "image_prompt_too_long"
    assert delegate.calls == []
    assert list(tmp_path.iterdir()) == []


def test_transport_compacts_only_wire_prompt_and_keeps_replay_fence(tmp_path):
    prompt = json.dumps(
        [
            {"approved_render_text": "中文文案", "product_original_index": i}
            for i in range(480)
        ],
        ensure_ascii=False,
    )
    assert len(prompt.encode()) > MAX_PROMPT_BYTES
    fields_before = prompt
    delegate = Delegate()
    transport = HaiyuTransport(delegate, journal_dir=tmp_path)
    wire(transport, prompt)
    wire(transport, prompt)
    assert len(delegate.calls) == 1
    sent = dict(delegate.calls[0]["fields"])["prompt"]
    assert len(sent.encode()) <= MAX_PROMPT_BYTES
    assert prompt == fields_before
    sections = json_sections(sent)
    if sent.startswith(ALIAS_INSTRUCTION):
        assert expand(sections[1], sections[0]) == json.loads(prompt)
    else:
        assert sections == [json.loads(prompt)]
    assert delegate.calls[0]["files"][0][3] == b"unchanged image bytes"


def test_other_provider_protocol_is_unchanged():
    delegate = Delegate()
    prompt = "中文" * 20_000
    wire(HaiyuTransport(delegate), prompt, url="https://other.example/v1/images/edits")
    assert dict(delegate.calls[0]["fields"])["prompt"] == prompt


def test_reconciliation_never_turns_unknown_outcome_into_local_rejection(tmp_path):
    from app.xinghe_agent import XingheAgentError

    delegate = Delegate()
    with pytest.raises(XingheAgentError, match="reconciliation is still required"):
        asyncio.run(
            HaiyuTransport(delegate, journal_dir=tmp_path).post_multipart(
                url="https://api.haiyushuke.com/v1/images/edits",
                headers={},
                fields=[("prompt", "x" * 32_001)],
                files=[("image", "input.png", "image/png", b"original bytes")],
                timeout_seconds=10,
                reconcile_only=True,
            )
        )
    assert delegate.calls == []
    assert list(tmp_path.iterdir()) == []
