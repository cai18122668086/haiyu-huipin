"""Keep retry identities fresh while reusing verified semantic checkpoints."""

from dataclasses import replace
from typing import Any


class AttemptScopedPlanner:
    def __init__(self, gateway: Any, attempt_id: str) -> None:
        self.gateway: Any = (
            gateway.gateway if isinstance(gateway, AttemptScopedPlanner) else gateway
        )
        self.attempt_id = attempt_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self.gateway, name)

    async def plan(self, request: Any) -> Any:
        return await self.gateway.plan(
            replace(
                request,
                idempotency_key=(
                    request.idempotency_key + ":analysis-attempt:" + self.attempt_id
                ),
            )
        )
