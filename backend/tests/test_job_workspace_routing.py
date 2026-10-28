from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import job_list_response, job_task_preset


def job_record(task_preset: str) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=f"job_{task_preset}",
        workspace_id="ws_test",
        type="product_image",
        status="completed",
        stage="completed",
        display_name="Product image",
        configuration={"settings": {"task_type": task_preset}},
        progress=100,
        expected_item_count=1,
        succeeded_item_count=1,
        failed_item_count=0,
        currency=None,
        estimated_cost_min_micros=None,
        estimated_cost_max_micros=None,
        actual_cost_micros=None,
        estimated_credits=None,
        actual_credits=None,
        usage_completeness="complete",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "task_preset",
    [
        "scene_main",
        "white_background",
        "model_image",
        "change_model",
        "try_on",
        "change_scene",
        "change_background",
        "change_color",
    ],
)
def test_job_list_exposes_supported_product_image_task_preset(
    task_preset: str,
) -> None:
    job = job_record(task_preset)

    assert job_task_preset(job) == task_preset
    assert job_list_response(job).task_preset == task_preset


@pytest.mark.parametrize(
    ("job_type", "configuration"),
    [
        ("product_image", {}),
        ("product_image", {"settings": {"task_type": "not_a_real_preset"}}),
        ("local_edit", {"settings": {"task_type": "scene_main"}}),
    ],
)
def test_job_list_omits_missing_unknown_or_irrelevant_task_preset(
    job_type: str,
    configuration: dict[str, object],
) -> None:
    job = SimpleNamespace(type=job_type, configuration=configuration)

    assert job_task_preset(job) is None
