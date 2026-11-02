"""Server-owned billing limits for references expanded into multiple screens."""

from collections.abc import Mapping
from typing import Any

VERSION = "submitted-references-v1"
KEY = "replica_billing"


def submitted_units(values: Mapping[str, Any], screens: int) -> int:
    references = {
        item["asset_id"]
        for item in values.get("references") or []
        if isinstance(item, Mapping) and item.get("asset_id")
    }
    # Historical sessions without source metadata retain per-screen billing.
    return min(screens, len(references)) if references else screens


def job_units(job_type: str, configuration: Mapping[str, Any], runnable: int) -> int:
    # Public request settings cannot set this provenance. create_job writes it
    # only from the authenticated replica confirmation handler's request state.
    billing = configuration.get(KEY)
    if (
        job_type == "reference_remix"
        and configuration.get("replica_session_id")
        and isinstance(billing, Mapping)
        and billing.get("version") == VERSION
    ):
        cap = billing.get("submitted_units")
        if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0:
            return min(runnable, cap)
    return runnable
