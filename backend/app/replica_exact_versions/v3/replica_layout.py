"""Source-bound layout evidence, separate from proposed copy and audit verdicts."""

from __future__ import annotations

from typing import Any


def _box(value: Any, width: int = 1000, height: int = 1000) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(type(v) is int for v in value)
        and 0 <= value[0] < value[2] <= width
        and 0 <= value[1] < value[3] <= height
    )


def validate_product_evidence(
    value: dict[str, Any], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = value.get("products")
    if not isinstance(rows, list) or len(rows) != len(sources):
        raise ValueError("商品文字观察未覆盖全部原始产品照片。")
    result: dict[int, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("商品文字观察必须是对象。")
        index = row.get("product_index")
        if type(index) is not int or not 0 <= index < len(sources) or index in result:
            raise ValueError("商品文字观察的原照编号无效或重复。")
        source = sources[index]
        if row.get("source_sha256") != source["sha256"]:
            raise ValueError(f"商品原照 {index} 的文字观察哈希不一致。")
        marks = row.get("readable_marks")
        if not isinstance(marks, list):
            raise ValueError(f"商品原照 {index} 的可读文字区域无效。")
        for mark in marks:
            if (
                not isinstance(mark, dict)
                or not isinstance(mark.get("text"), str)
                or not 1 <= len(mark["text"].strip()) <= 2000
                or not _box(mark.get("bbox"), source["width"], source["height"])
            ):
                raise ValueError(f"商品原照 {index} 的文字或原始像素坐标无效。")
        notes = row.get("uncertainty_notes")
        if not isinstance(notes, str) or not 8 <= len(notes) <= 4000:
            raise ValueError(f"商品原照 {index} 缺少可读性和未知事实说明。")
        result[index] = {
            "product_index": index,
            "source_sha256": source["sha256"],
            "readable_marks": [{"text": m["text"], "bbox": m["bbox"]} for m in marks],
            "uncertainty_notes": notes,
        }
    return [result[index] for index in range(len(sources))]


def validate_reference_layouts(
    value: dict[str, Any], expected: dict[str, str]
) -> dict[str, Any]:
    rows = value.get("units")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("参考图独立构图观察未覆盖全部裁图。")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("reference_unit_id"), str)
            or row.get("reference_unit_id") not in expected
            or row["reference_unit_id"] in result
            or row.get("reference_sha256") != expected[row["reference_unit_id"]]
        ):
            raise ValueError("参考图独立构图观察未绑定当前裁图哈希。")
        if type(row.get("reference_product_present")) is not bool:
            raise ValueError("参考图必须明确商品实物是否出现。")
        anchors = row.get("layout_anchors")
        if not isinstance(anchors, list) or not 1 <= len(anchors) <= 24:
            raise ValueError("参考图缺少独立主体位置观察。")
        for anchor in anchors:
            if (
                not isinstance(anchor, dict)
                or not isinstance(anchor.get("label"), str)
                or not 2 <= len(anchor["label"]) <= 200
                or not _box(anchor.get("bbox"))
            ):
                raise ValueError("参考主体位置观察坐标无效。")
        regions = row.get("text_regions")
        if not isinstance(regions, list) or len(regions) > 48:
            raise ValueError("参考文字区域观察无效。")
        if type(row.get("has_poster_text")) is not bool or row[
            "has_poster_text"
        ] != bool(regions):
            raise ValueError("参考有无海报文字与区域观察不一致。")
        ids: set[str] = set()
        for region in regions:
            if (
                not isinstance(region, dict)
                or not isinstance(region.get("id"), str)
                or not 1 <= len(region["id"]) <= 40
                or region["id"] in ids
                or region.get("role")
                not in {
                    "brand",
                    "headline",
                    "connector",
                    "subtitle",
                    "badge",
                    "body",
                    "footnote",
                }
                or region.get("font_tier") not in {"dominant", "secondary", "small"}
                or not (
                    (
                        isinstance(region.get("line_group"), str)
                        and 1 <= len(region["line_group"].strip()) <= 80
                    )
                    or (
                        type(region.get("line_group")) is int
                        and 0 <= region["line_group"] <= 1000
                    )
                )
                or not isinstance(region.get("observed_text"), str)
                or len(region["observed_text"]) > 2000
                or not _box(region.get("bbox"))
            ):
                region_id = region.get("id") if isinstance(region, dict) else "unknown"
                raise ValueError(
                    f"参考 {row['reference_unit_id']} 文字区域 {region_id} 无效；"
                    "line_group 须为非空字符串或非负整数，bbox 须为有效归一化坐标。"
                )
            ids.add(region["id"])
        if (
            not isinstance(row.get("typography_notes"), str)
            or not 8 <= len(row["typography_notes"]) <= 4000
        ):
            raise ValueError("参考字体层级缺少具体像素观察。")
        result[row["reference_unit_id"]] = row
    return result


def bind_module_text(
    scopes: dict[str, Any], modules: list[dict[str, Any]], layouts: dict[str, Any]
) -> dict[str, Any]:
    """Resolve only approved fragments; never send reference OCR as renderable copy."""
    result = {}
    for module in modules:
        scope = scopes[module["id"]]
        layout = layouts[module["reference_unit_id"]]
        if scope.get("product_visibility") not in {"visible_product", "context_only"}:
            raise ValueError("逐屏必须明确商品实物是否应当出现。")
        regions = {row["id"]: row for row in layout["text_regions"]}
        bindings = scope.get("text_bindings")
        if not isinstance(bindings, list) or len(bindings) != len(regions):
            raise ValueError("每个参考文字区域都必须绑定获准文案或说明省略原因。")
        bound = {}
        copy = module["copy_units"]
        for row in bindings:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("region_id"), str)
                or row.get("region_id") not in regions
                or row["region_id"] in bound
            ):
                raise ValueError("文案区域绑定重复或不属于当前参考图。")
            region = regions[row["region_id"]]
            item = {
                "region_id": region["id"],
                **{k: region[k] for k in ("bbox", "role", "font_tier", "line_group")},
                "action": row.get("action"),
            }
            if row.get("action") == "render":
                index, fragment = row.get("copy_unit_index"), row.get("fragment")
                if (
                    type(index) is not int
                    or not 0 <= index < len(copy)
                    or not isinstance(fragment, str)
                    or not fragment.strip()
                    or fragment not in copy[index]
                ):
                    raise ValueError("海报区域出现未经批准的文案，不能复制参考口号。")
                item.update(copy_unit_index=index, render_text=fragment)
            elif row.get("action") == "omit_unsupported_copy":
                reason = row.get("reason")
                if (
                    region["font_tier"] == "dominant"
                    or row.get("retain_carrier") is not True
                    or not isinstance(reason, str)
                    or not 12 <= len(reason) <= 1000
                ):
                    raise ValueError("不能省略主标题或无理由改变参考文字载体。")
                item.update(reason=reason, retain_carrier=True)
            else:
                raise ValueError("未知的文案区域绑定动作。")
            bound[region["id"]] = item
        result[module["id"]] = {
            **{
                k: scope[k]
                for k in (
                    "module_id",
                    "reference_sha256",
                    "required_identity_checks",
                    "not_applicable_checks",
                    "layout_anchors",
                    "composition_notes",
                    "product_visibility",
                )
                if k in scope
            },
            "text_bindings": list(bound.values()),
            "source_layout_anchors": layout["layout_anchors"],
        }
    return result


def exact_review_context(
    module: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Exclude planned visual conclusions and other modules from fresh review."""
    scope = context.get("module_constraints", {}).get(module["id"])
    return {
        "module": {
            k: module[k]
            for k in (
                "id",
                "reference_unit_id",
                "reference_path",
                "output_canvas",
                "page_duty",
                "buyer_conclusion",
                "copy_units",
            )
            if k in module
        },
        "user_context": {
            k: context[k]
            for k in ("user_facts", "language", "platform")
            if k in context
        },
        "module_constraints": {
            k: scope[k]
            for k in (
                "module_id",
                "reference_sha256",
                "required_identity_checks",
                "not_applicable_checks",
                "product_visibility",
                "text_bindings",
            )
            if k in scope
        }
        if scope
        else None,
    }
