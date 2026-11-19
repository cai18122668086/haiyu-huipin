import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import re
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

from app.config import Settings
from app.main import create_app
from app.provider import FakeProvider, ProviderArtifact
from app.task_runner import MockTaskRunner
from app.xinghe_agent import FakeXingheGateway, XingheImageProvider
from fastapi.testclient import TestClient
from httpx import HTTPError
from PIL import Image

WORKSPACE = "ws_shared_fixture"
BASE = f"/api/v1/workspaces/{WORKSPACE}"
SUITE_OUTPUTS = {"amazon-suite": 21, "detail-pack": 8, "domestic-main": 6}


class FixtureDelivery(FakeProvider):
    name = "isolated-fixture-replay"

    def __init__(self, path):
        super().__init__()
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
        self.artifact = ProviderArtifact(
            data,
            path.name,
            mimetypes.guess_type(path)[0],
            width,
            height,
            hashlib.sha256(data).hexdigest(),
        )
        self.artifacts = {(width, height): self.artifact}

    async def execute(self, request):
        result = await super().execute(request)
        inputs = request.configuration["item"]
        size = inputs.get("size")
        if isinstance(size, str) and re.fullmatch(r"[1-9]\d{0,3}x[1-9]\d{0,3}", size):
            width, height = map(int, size.split("x"))
        else:
            width, height = (
                inputs.get("target_width") or self.artifact.width,
                inputs.get("target_height") or self.artifact.height,
            )
        assert 0 < width <= 8192 and 0 < height <= 8192
        dimensions = (width, height)
        if dimensions not in self.artifacts:
            buffer = BytesIO()
            with Image.open(BytesIO(self.artifact.payload)) as image:
                image.resize(dimensions).save(buffer, "PNG")
            data = buffer.getvalue()
            self.artifacts[dimensions] = ProviderArtifact(
                data,
                "synthetic-fixture.png",
                "image/png",
                width,
                height,
                hashlib.sha256(data).hexdigest(),
            )
        return replace(result, artifact=self.artifacts[dimensions])


class FixtureReplicaPlanner(FakeXingheGateway):
    async def plan(self, request):
        return SimpleNamespace(
            payload={
                "product_identity_lock": "Keep original product identity and silhouette",
                "confirmed_facts": [],
                "style_lock": "Preserve approved hierarchy without new product claims",
                "references": [
                    {
                        "reference_index": 0,
                        "columns": [
                            {
                                "bbox": [0, 0, 1000, 1000],
                                "screens": [
                                    {
                                        "end_y": 1000,
                                        "page_duty": "Present the supplied product accurately",
                                        "visual_plan": "Keep product proportions unchanged in a neutral scene",
                                        "visible_copy": [],
                                        "forbidden_elements": "No invented benefits or unrelated brands",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            normalized_usage={"reported": True},
            actual_cost_micros=0,
            provider_request_id="fixture-replica-plan",
            api_latency_seconds=0,
            error_code=None,
        )


class FixtureNativeGateway(FakeXingheGateway):
    provider_name = "synthetic"

    async def plan(self, request):
        if request.planning_profile == "aurora_native_fast_identity":
            return await super().plan(
                replace(request, planning_profile="aurora_identity")
            )
        return await super().plan(request)


async def fixture_pair_review(provider, request, native_request, sources, artifact):
    return provider._native_detail_judgment(native_request, artifact)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked(response):
    if not response.is_success:
        raise AssertionError(f"HTTP {response.status_code}: {error_code(response)}")
    return response.json()


def error_code(response):
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:
        return None


def original(client, asset_id):
    access = checked(
        client.post(
            f"{BASE}/assets/{asset_id}/access-url",
            json={"disposition": "attachment", "variant": "original"},
        )
    )
    url = urlsplit(access["url"])
    response = client.get(f"{url.path}?{url.query}")
    response.raise_for_status()
    return hashlib.sha256(response.content).hexdigest()


def verify_case(root, state, case):
    native = case in {"domestic-main", "detail-pack"}
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="development",
        development_user_id="usr_shared_fixture",
        development_workspace_id=WORKSPACE,
        access_url_secret="isolated-shared-fixture",
        database_url=f"sqlite:///{(state / 'fixture.db').as_posix()}",
        auto_create_schema=True,
        storage_backend="local",
        storage_dir=state / "storage",
        aurora_work_root=state / "work",
        task_runner="mock",
        task_dispatcher="inline",
        auto_process_jobs=False,
        mock_job_delay_seconds=0,
        ecommerce_login_enabled=False,
        ecommerce_startup_recovery_enabled=False,
        workspace_credit_enforcement_enabled=False,
        credit_payments_enabled=False,
        max_image_pixels=6_000_000,
    )
    app = create_app(settings)
    provider = FixtureDelivery(root / "inputs" / "input-1.jpg")
    gateway = FixtureNativeGateway()
    if native:
        settings.task_runner = "tengli"
        settings.domestic_main_image_provider_enabled = case == "domestic-main"
        app.state.task_runner = MockTaskRunner(
            app.state.session_factory,
            0,
            storage=app.state.storage,
            xinghe_gateway=gateway,
            domestic_main_image_provider_enabled=case == "domestic-main",
            domestic_revision_settings=settings,
            aurora_work_root=settings.aurora_work_root,
        )
    else:
        app.state.task_runner.coordinator.provider = provider
    with (
        TestClient(app) as client,
        patch.object(
            XingheImageProvider,
            "_native_detail_judgment_with_review",
            fixture_pair_review,
        ),
    ):
        for path in ("/api/v1/me", "/api/v1/workspaces", f"{BASE}/rewards"):
            checked(client.get(path))
        uploaded = {}

        def upload(name):
            if name not in uploaded:
                if Path(name).name != name:
                    raise ValueError("Fixture must be a direct child of inputs")
                data = (root / "inputs" / name).read_bytes()
                asset = checked(
                    client.post(
                        f"{BASE}/assets",
                        headers={"Idempotency-Key": str(uuid4())},
                        data={"media_kind": "mask" if name == "mask.png" else "image"},
                        files={"file": (name, data, mimetypes.guess_type(name)[0])},
                    )
                )
                uploaded[name] = asset["id"]
                assert original(client, asset["id"]) == hashlib.sha256(data).hexdigest()
            return uploaded[name]

        def rewrite(value):
            if isinstance(value, str) and value.startswith("@asset:"):
                return upload(value.removeprefix("@asset:"))
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            return value

        if case == "creative":
            source = load(root / "inputs" / "source.private.json")
            mapping = {
                asset["id"]: upload(asset["file"])
                for asset in source["assets"]
                if asset["direction"] == "input"
            }
            payload = {
                "type": source["type"],
                "input": {
                    key: [mapping[asset] for asset in values]
                    for key, values in source["configuration"]["input"].items()
                },
                "configuration": source["configuration"]["settings"],
            }
        else:
            payload = rewrite(load(root / "cases" / f"{case}.private.json"))
        if case == "replica":
            disabled = client.post(f"{BASE}/replica/sessions", json=payload)
            assert (
                disabled.status_code == 503
                and error_code(disabled) == "REPLICA_DISABLED"
            )
            app.state.settings.reference_remix_provider_enabled = True
            app.state.replica_gateway = FixtureReplicaPlanner()
            payload["request_id"] = str(uuid4())
            session = checked(client.post(f"{BASE}/replica/sessions", json=payload))
            document = checked(client.get(f"{BASE}/replica/documents/{session['id']}"))
            assert document["status"] == "awaiting_confirmation"
            confirm = checked(
                client.post(
                    f"{BASE}/replica/sessions/{document['id']}/confirm",
                    json={"plan_hash": document["payload"]["plan_hash"]},
                )
            )
            job_id = confirm["payload"]["job_ids"][0]
        else:
            payload.update(intent="submit", display_name=f"Shared fixture {case}")
            headers = {"Idempotency-Key": str(uuid4())}
            submit = client.post(f"{BASE}/jobs", headers=headers, json=payload)
            if case == "reference-remix" and submit.status_code == 409:
                assert error_code(submit) == "REPLICA_CONFIRM_REQUIRED"
                return {"status": "confirmation_required", "inputs": len(uploaded)}
            job_id = checked(submit)["id"]
            replay = checked(client.post(f"{BASE}/jobs", headers=headers, json=payload))
            assert replay["id"] == job_id
        assert any(
            item["id"] == job_id
            for item in checked(client.get(f"{BASE}/jobs"))["items"]
        )
        asyncio.run(app.state.task_runner.run(job_id))
        job = checked(client.get(f"{BASE}/jobs/{job_id}"))
        assert job["status"] not in {"queued", "processing"}
        assert (
            provider.external_network_request_count
            == gateway.external_network_request_count
            == 0
        )

        def call_count():
            return len(gateway.image_calls) if native else provider.call_count

        if job["status"] == "completed":
            fixture_calls = call_count()
            asyncio.run(app.state.task_runner.run(job_id))
            assert call_count() == fixture_calls
        outputs = [
            asset
            for asset in job["assets"]
            if asset["direction"] == "output" and asset["is_deliverable"]
        ]
        hashes = sorted(original(client, asset["asset_id"]) for asset in outputs)
        if not native:
            assert set(hashes) <= {
                artifact.sha256 for artifact in provider.artifacts.values()
            }
        archive = client.get(f"{BASE}/jobs/{job_id}/deliverables.zip")
        archive_count = None
        if archive.is_success:
            with ZipFile(BytesIO(archive.content)) as bundle:
                assert bundle.testzip() is None
                names = [
                    entry for entry in bundle.namelist() if not entry.endswith("/")
                ]
                if case == "detail-pack":
                    packet = json.loads(bundle.read("final-review.json"))
                    masters = [
                        entry for entry in names if entry.startswith("pair-masters/")
                    ]
                    assert sorted(
                        hashlib.sha256(bundle.read(entry)).hexdigest()
                        for entry in masters
                    ) == sorted(pair["sha256"] for pair in packet["pair_canvases"])
                    names = [entry for entry in names if entry.startswith("screens/")]
                archive_hashes = sorted(
                    hashlib.sha256(bundle.read(entry)).hexdigest() for entry in names
                )
                assert archive_hashes == hashes
                archive_count = len(names)
        return {
            "status": job["status"],
            "error": job["error_code"],
            "expected": job["expected_item_count"],
            "inputs": len(uploaded),
            "original_downloads": len(hashes),
            "zip_count": archive_count,
            "fixture_calls": call_count(),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture_dir", type=Path)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    if any(name.upper().startswith("POLARIS_") for name in os.environ):
        parser.error(
            "Run without POLARIS_ environment variables in a network-isolated container"
        )
    root = args.fixture_dir.resolve()
    cases = [
        "creative",
        *(
            path.name.removesuffix(".private.json")
            for path in sorted((root / "cases").glob("*.private.json"))
        ),
    ]
    assert len(cases) > 1, "No shared image fixture cases found"
    if args.case:
        if missing := set(args.case) - set(cases):
            parser.error("Unknown fixture case: " + ", ".join(sorted(missing)))
        cases = args.case
    failed = False
    for case in cases:
        with TemporaryDirectory(prefix="huipin-shared-") as directory:
            try:
                report = verify_case(root, Path(directory), case)
            except (
                AssertionError,
                HTTPError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                report = {"status": "test_error", "error": type(exc).__name__}
                code = getattr(exc, "code", None)
                if isinstance(code, str) and re.fullmatch(r"[A-Z_]{2,90}", code):
                    report["code"] = code
                if isinstance(exc, AssertionError) and str(exc).startswith("HTTP "):
                    report["detail"] = str(exc)
            failed |= report["status"] != "completed" or report.get(
                "expected"
            ) != report.get("original_downloads")
            if case in SUITE_OUTPUTS:
                failed |= report.get("zip_count") != SUITE_OUTPUTS[case]
        print(json.dumps({"case": case, **report}), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
