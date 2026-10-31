"""Lossless wire-only JSON compaction for Haiyu's UTF-8 prompt byte limit.

Approved plans, native prompts and invocation hashes stay unchanged. JSON string
values (including poster copy), numbers and prose are never shortened. Repeated
object keys may use an explicit, reversible dictionary when whitespace alone is
insufficient. Requests that still do not fit are rejected before dispatch.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

MAX_PROMPT_BYTES = 32_000
ALIAS_INSTRUCTION = (
    "Expand aliased JSON object keys using JSON_KEY_ALIASES below. "
    "This is a key-name dictionary only; all values, approved copy and "
    "instructions retain their full meaning.\nJSON_KEY_ALIASES="
)


@dataclass(frozen=True)
class _Part:
    text: str
    key: str | None = None


def _parts(prompt: str) -> list[_Part]:
    # Decimal prevents validation of a valid JSON number from rounding it; the
    # actual number token is copied verbatim, without any reserialization.
    decoder = json.JSONDecoder(parse_float=Decimal)
    parts: list[_Part] = []
    literal: list[str] = []
    start = 0
    position = 0
    while position < len(prompt):
        if prompt[position] not in "[{":
            position += 1
            continue
        try:
            _, end = decoder.raw_decode(prompt, position)
        except (ValueError, RecursionError):
            position += 1
            continue
        literal.append(prompt[start:position])
        while position < end:
            char = prompt[position]
            if char == '"':
                value, after = decoder.raw_decode(prompt, position)
                following = after
                while following < end and prompt[following] in " \t\r\n":
                    following += 1
                key = value if following < end and prompt[following] == ":" else None
                if key is not None:
                    parts.append(_Part("".join(literal)))
                    literal = []
                    parts.append(_Part(prompt[position:after], key))
                else:
                    literal.append(prompt[position:after])
                position = after
            else:
                if char not in " \t\r\n":
                    literal.append(char)
                position += 1
        start = end
    literal.append(prompt[start:])
    parts.append(_Part("".join(literal)))
    return parts


def fit_prompt(prompt: str) -> str:
    """Return the original or a reversible representation within 32,000 bytes.

    The caller includes every transport-added instruction before calling this.
    Never truncate a plan or silently drop evidence to make a request fit.
    """
    if len(prompt.encode("utf-8")) <= MAX_PROMPT_BYTES:
        return prompt
    parts = _parts(prompt)
    compact = "".join(part.text for part in parts)
    if len(compact.encode("utf-8")) <= MAX_PROMPT_BYTES:
        return compact
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for part in parts:
        if part.key is not None:
            counts[part.key] += 1
            sizes[part.key] += len(part.text.encode("utf-8"))
    aliases: dict[str, str] = {}
    legend: dict[str, str] = {}
    # Stable ordering gives retries the same wire fingerprint. Include the
    # dictionary's own bytes when deciding whether a key is worth shortening.
    for key in sorted(counts, key=lambda k: (-sizes[k] + counts[k] * 4, k)):
        alias = f"@{len(aliases)}"
        while alias in counts:
            alias = "@" + alias
        token = json.dumps(alias)
        entry = json.dumps({alias: key}, ensure_ascii=False, separators=(",", ":"))
        if sizes[key] - counts[key] * len(token) <= len(entry.encode("utf-8")):
            continue
        aliases[key] = token
        legend[alias] = key
    if aliases:
        compact = (
            ALIAS_INSTRUCTION
            + json.dumps(legend, ensure_ascii=False, separators=(",", ":"))
            + "\n\n"
            + "".join(
                aliases.get(part.key, part.text) if part.key is not None else part.text
                for part in parts
            )
        )
        if len(compact.encode("utf-8")) <= MAX_PROMPT_BYTES:
            return compact
    raise ValueError(
        "最终生图提示词超过上游 32,000 字节限制；无损压缩后仍无法提交，"
        "未发送新的生图请求。"
    )
