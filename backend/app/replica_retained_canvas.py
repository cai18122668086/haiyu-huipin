"""Admit a known retained canvas under the application aspect-delivery policy.

Original plans, invocation verdicts and source checkpoints are not rewritten.
Only a restored execution or a recovery projection receives a delivery record.
"""

from pathlib import Path
from typing import Any

from .replica_canvas import delivery_receipt, native_budget_matches
from .replica_native import ReplicaNativeError, contained, digest, read_json, write_json
from .replica_retry import _document


def retained_module(
    snapshot: dict[str, Any], module: dict[str, Any], canvas: list[int], prefix: str
) -> dict[str, Any] | None:
    if (
        module.get("status") != "AWAITING_ARTWORK"
        or "invocations.json" not in snapshot["files"]
    ):
        return None
    native = module.get("request", {})
    row = _document(snapshot, "invocations.json").get(native.get("token"), {})
    if row.get("state") != "canvas_rejected":
        return None
    attempts = row.get("canvas_attempts", [])
    if not attempts:
        return None
    latest = attempts[-1]
    if (
        row.get("target") != module.get("id")
        or native.get("module_id") != module.get("id")
        or native.get("canvas") != {"width": canvas[0], "height": canvas[1]}
        or row.get("native_prompt_sha256") != digest(native.get("prompt", "").encode())
        or latest.get("path") != row.get("path")
        or latest.get("sha256") != row.get("sha256")
        or latest.get("size") != row.get("actual_size")
        or latest.get("accepted_canvas") is not False
    ):
        raise ReplicaNativeError("保留画幅与原生请求或调用回执不一致。")
    size = row["actual_size"]
    if (
        not isinstance(size, list)
        or len(size) != 2
        or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in size
        )
    ):
        raise ReplicaNativeError("保留画幅的尺寸记录无效。")
    actual = (size[0], size[1])
    if not native_budget_matches(canvas, actual):
        return None
    relative = (
        contained(Path(snapshot["root"]), row["path"])
        .relative_to(snapshot["root"])
        .as_posix()
    )
    record = snapshot["files"].get(relative, {})
    if record.get("sha256") != row["sha256"] or not record.get(
        "object_key", ""
    ).startswith(prefix):
        raise ReplicaNativeError("保留画幅缺少当前工作区的持久哈希绑定。")
    return {
        **module,
        "status": "GENERATED",
        "artwork": row["path"],
        "sha256": row["sha256"],
        "size": size,
        "delivery_canvas": delivery_receipt(canvas, actual, row["sha256"]),
        "recovered_canvas_verdict": "canvas_rejected",
    }


def promote_retained(run: Any) -> None:
    from .replica_direct_delivery import inspect_artwork

    state = read_json(run.root / "direct-session.json")
    snapshot = {"root": str(run.root), "files": run.snapshot_files}
    prefix = f"{run.workspace_id}/replica/{run.document_id}/"
    changed = False
    for i, (module, planned) in enumerate(
        zip(state["modules"], state["plan"]["modules"], strict=True)
    ):
        canvas = [planned["output_canvas"]["width"], planned["output_canvas"]["height"]]
        candidate = retained_module(snapshot, module, canvas, prefix)
        if candidate is None:
            continue
        actual = inspect_artwork(
            run, candidate["artwork"], planned["output_canvas"], candidate["sha256"]
        )
        if actual["size"] != candidate["size"]:
            raise ReplicaNativeError("保留画幅的真实尺寸与原调用回执不一致。")
        state["modules"][i] = candidate
        changed = True
    if changed:
        write_json(run.root / "direct-session.json", state)
