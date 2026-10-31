"""Apply application delivery policy without rewriting frozen Skill protocols."""

from __future__ import annotations

from typing import Any

from .replica_native import NativeReplicaRun, read_json, write_json


def delivery_run_type(base: type[NativeReplicaRun]) -> type[NativeReplicaRun]:
    class PlatformDeliveryRun(base):  # type: ignore[valid-type,misc]
        def state(self) -> dict[str, Any]:
            if getattr(self, "direct_execution", False):
                return read_json(self.root / "direct-session.json")
            return super().state()

        async def checkpoint(self) -> None:
            parent = getattr(super(), "checkpoint", None)
            if parent is not None:
                await parent()
            else:
                write_json(self.root / "context.json", self.context)

        async def restore(self, snapshot: dict[str, Any]) -> None:
            await super().restore(snapshot)
            self.direct_execution = (
                self.context.get("post_generation_review") == "disabled"
            )
            if self.direct_execution:
                from .replica_direct_delivery import validate_direct_state
                from .replica_retained_canvas import promote_retained

                promote_retained(self)
                validate_direct_state(self)

    return PlatformDeliveryRun
