"""New domestic jobs go to their frozen worker; existing products keep their queue."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.outbox import _dispatch


@pytest.mark.parametrize(
    "kind,pipeline,expected",
    [
        ("detail_pack", "aurora-domestic-detail-v4.3.1", "domestic_detail_v431"),
        ("detail_pack", "xinghe-domestic-detail-v4.0", "domestic_detail_v431"),
        ("domestic_detail_review", None, "domestic_detail_v431"),
        ("detail_pack", None, None),
        ("detail_pack", "aurora-domestic-detail-v4.3", None),
        ("domestic_main_image", None, None),
        ("amazon_visual_suite", None, None),
    ],
)
@pytest.mark.parametrize("event", ["job.dispatch_requested", "job.cancel_requested"])
def test_current_domestic_queue_uses_durable_job_version_and_preserves_legacy(
    monkeypatch, kind, pipeline, expected, event
):
    import sys

    calls = []
    process = SimpleNamespace(apply_async=lambda **kw: calls.append(("run", kw)))
    cancel = SimpleNamespace(apply_async=lambda **kw: calls.append(("cancel", kw)))
    monkeypatch.setitem(
        sys.modules,
        "app.tasks",
        SimpleNamespace(
            process_job=process,
            cancel_job_task=cancel,
        ),
    )
    job = SimpleNamespace(
        type=kind, configuration={"domestic_detail_pipeline": pipeline}
    )
    runner = SimpleNamespace(
        session_factory=lambda: nullcontext(SimpleNamespace(get=lambda model, key: job))
    )
    _dispatch(
        SimpleNamespace(task_dispatcher="celery"),
        runner,
        event_type=event,
        dedupe_key="owned-test-event",
        payload={
            "job_id": "owned-test-job",
            "domestic_detail_pipeline": "untrusted-payload",
        },
    )
    label, options = calls[0]
    assert label == ("cancel" if event == "job.cancel_requested" else "run")
    assert options["args"] == ["owned-test-job"]
    assert options["headers"] == {"outbox_dedupe_key": "owned-test-event"}
    if expected is None:
        assert "queue" not in options
    else:
        assert options["queue"] == expected
