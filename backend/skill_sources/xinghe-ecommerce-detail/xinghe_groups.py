"""Pure helpers for grouping multiple product-reference images."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any


MAX_PRODUCT_GROUPS = 10
MAX_REFERENCES_PER_GROUP = 10
MAX_TOTAL_REFERENCES = 50

ANGLE_SUFFIXES = (
    "正面",
    "背面",
    "侧面",
    "左侧",
    "右侧",
    "顶部",
    "底部",
    "平铺",
    "俯拍",
    "仰拍",
    "斜拍",
    "前视图",
    "后视图",
    "正视图",
    "背视图",
    "左视图",
    "右视图",
    "顶部图",
    "底部图",
    "45度",
    "45°",
    "细节",
    "特写",
    "局部",
    "包装",
    "配件",
    "打开",
    "关闭",
    "开合",
    "front",
    "back",
    "side",
    "left",
    "right",
    "top",
    "bottom",
    "flatlay",
    "detail",
    "closeup",
    "angle",
)
_ANGLE_SUFFIX_PATTERN = "|".join(
    re.escape(value) for value in sorted(ANGLE_SUFFIXES, key=len, reverse=True)
)
_ANGLE_SUFFIX_RE = re.compile(
    rf"^(?P<group>.+?)(?:[-_—–\s]+|[（(])"
    rf"(?P<angle>{_ANGLE_SUFFIX_PATTERN})(?:[）)])?$",
    flags=re.IGNORECASE,
)


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", stem, flags=re.UNICODE).strip("_")
    return cleaned[:40] or "product"


def default_group_label(name: str, index: int) -> str:
    """Infer a group from explicit ``__`` or a known angle suffix."""
    stem = Path(name).stem
    prefix, separator, _ = stem.partition("__")
    if separator and prefix.strip():
        return safe_stem(prefix)
    match = _ANGLE_SUFFIX_RE.fullmatch(stem.strip())
    if match and match.group("group").strip():
        return safe_stem(match.group("group"))
    return f"商品组{index:02d}"


def build_source_groups(files: Sequence[Any], labels: Sequence[str]) -> list[dict[str, Any]]:
    """Materialize uploaded files and group them while preserving UI order."""
    if len(files) != len(labels):
        raise ValueError("图片数量与分组标签数量不一致")
    if len(files) > MAX_TOTAL_REFERENCES:
        raise ValueError(
            f"一次最多上传 {MAX_TOTAL_REFERENCES} 张参考图，当前为 {len(files)} 张"
        )
    groups_by_label: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    for index, (file, raw_label) in enumerate(zip(files, labels, strict=True), start=1):
        label = str(raw_label or "").strip()
        if not label:
            raise ValueError(f"第 {index} 张图片的商品组不能为空")
        image_bytes = file.getvalue()
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        source = {
            "name": str(file.name),
            "bytes": image_bytes,
            "sha256": image_hash,
        }
        if label not in groups_by_label:
            group = {"label": label, "sources": []}
            groups_by_label[label] = group
            groups.append(group)
        groups_by_label[label]["sources"].append(source)

    if len(groups) > MAX_PRODUCT_GROUPS:
        raise ValueError(f"商品组最多 {MAX_PRODUCT_GROUPS} 组，当前为 {len(groups)} 组")
    for group in groups:
        if len(group["sources"]) > MAX_REFERENCES_PER_GROUP:
            raise ValueError(
                f"商品组“{group['label']}”最多 {MAX_REFERENCES_PER_GROUP} 张参考图，"
                f"当前为 {len(group['sources'])} 张"
            )
        group["source_hash"] = group_source_hash(group["sources"])
    return groups


def group_source_hash(sources: Sequence[dict[str, Any]]) -> str:
    """Hash ordered image identities so angle changes invalidate resume safely."""
    payload = [{"name": source["name"], "sha256": source["sha256"]} for source in sources]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def job_signature(groups: Sequence[dict[str, Any]], config: dict[str, Any]) -> str:
    payload = {
        "groups": [
            {
                "label": group["label"],
                "sources": [
                    {"name": source["name"], "sha256": source["sha256"]}
                    for source in group["sources"]
                ],
            }
            for group in groups
        ],
        "config": config,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reference_tuples(group: dict[str, Any]) -> list[tuple[str, bytes]]:
    return [(source["name"], source["bytes"]) for source in group["sources"]]


def replace_page_record(group_record: dict[str, Any], record: dict[str, Any]) -> None:
    page = record["page"]
    group_record["outputs"] = [
        item for item in group_record.get("outputs", []) if item.get("page") != page
    ]
    group_record["outputs"].append(record)
    group_record["outputs"].sort(key=lambda item: item.get("page", 0))


def successful_page(group_record: dict[str, Any], page: int, quality: str) -> bool:
    for item in group_record.get("outputs", []):
        if (
            item.get("page") != page
            or item.get("status") != "ok"
            or item.get("quality") != quality
        ):
            continue
        path = Path(item.get("path", ""))
        if path.is_file() and path.stat().st_size > 0:
            return True
    return False
