"""A single-segment POST alias for older edge path matchers.

Keep the full continuation validation and idempotency contract in routes_v1.
This alias changes no generation inputs, paid calls or runtime bindings.
"""

from fastapi import APIRouter, status

from .routes_v1 import continue_detail_brand
from .schemas_v1 import JobDetailResponse

router = APIRouter(prefix="/api/v1", tags=["jobs"])
router.add_api_route(
    "/workspaces/{workspace_id}/jobs/{job_id}",
    continue_detail_brand,
    methods=["POST"],
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    name="continue_detail_brand_compatible_edge",
)
