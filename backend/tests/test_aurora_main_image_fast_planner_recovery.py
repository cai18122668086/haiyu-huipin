"""Planning dispatch fences distinguish preflight from a paid unknown result."""

import asyncio

from app.models import JobPlanRecord, JobRecord

from .test_api import dispatch_pending, idem
from .test_aurora_main_image_fast_workflow import BASE, Gateway, create, detail


def test_duplicate_planning_worker_does_not_fail_or_resubmit(client, tmp_path):
    class SlowPlanner(Gateway):
        async def plan(self, request):
            entered.set()
            await release.wait()
            return await super().plan(request)

    gateway = SlowPlanner()
    job_id, _ = create(client, tmp_path, gateway=gateway)
    coordinator = client.app.state.task_runner.coordinator
    coordinator.prepare(job_id)

    async def run():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(coordinator.campaign_planner.plan(job_id))
        await entered.wait()
        assert await coordinator.campaign_planner.plan(job_id) == "planning"
        assert detail(client, job_id)["status"] == "planning"
        release.set()
        assert await task == "processing"

    entered = release = None
    asyncio.run(run())
    assert len(gateway.planner_calls) == 1
    assert not gateway.image_calls
    with client.app.state.session_factory() as session:
        assert session.query(JobPlanRecord).filter_by(job_id=job_id).count() == 1
    assert "_main_image_fast_planning" not in detail(client, job_id)["configuration"]


def test_source_preflight_failure_records_zero_dispatch(client, tmp_path, monkeypatch):
    job_id, gateway = create(client, tmp_path)

    async def corrupt(_):
        return b"original-source-has-changed"

    monkeypatch.setattr(client.app.state.storage, "read_bytes", corrupt)
    dispatch_pending(client)
    assert detail(client, job_id)["status"] == "failed"
    assert not gateway.planner_calls and not gateway.image_calls
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job.configuration["_main_image_fast_planning"] == {"state": "preparing"}


def test_unknown_planning_submission_is_not_silently_replanned(client, tmp_path):
    class UnknownPlanner(Gateway):
        async def plan(self, request):
            self.planner_calls.append(request)
            raise RuntimeError("Unknown upstream submission result")

    gateway = UnknownPlanner()
    job_id, _ = create(client, tmp_path, gateway=gateway)
    dispatch_pending(client)
    assert detail(client, job_id)["status"] == "failed"
    asyncio.run(client.app.state.task_runner.run(job_id))
    assert len(gateway.planner_calls) == 1 and not gateway.image_calls
    response = client.post(
        BASE + f"/jobs/{job_id}/retry",
        headers=idem(),
        json={"scope": "failed", "item_ids": []},
    )
    assert response.status_code == 409
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job.configuration["_main_image_fast_planning"] == {"state": "invoked"}
