import io, json, os, uuid
from PIL import Image
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.outbox import publish_pending_events

for k in list(os.environ):
    if k.endswith("_PROVIDER_ENABLED"):
        os.environ[k] = "false"
app = create_app(Settings())
with TestClient(app) as client:
    assert client.get("/health/ready").status_code == 200
    client.get("/api/v1/me")
    client.get("/api/v1/workspaces")
    base = "/api/v1/workspaces/ws_development"
    data = io.BytesIO()
    Image.new("RGB", (100, 100), "pink").save(data, "PNG")
    r = client.post(
        base + "/assets",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        files={"file": ("restore-smoke.png", data.getvalue(), "image/png")},
    )
    assert r.status_code == 201, r.text
    asset = r.json()["id"]
    r = client.post(
        base + "/jobs",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "type": "product_image",
            "intent": "submit",
            "display_name": "Isolated restore smoke",
            "input": {"product_asset_ids": [asset]},
            "configuration": {"task_type": "white_background", "image_count": 1},
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    publish_pending_events(
        app.state.settings, app.state.session_factory, app.state.task_runner
    )
    result = client.get(base + "/jobs/" + job_id).json()
    assert result["status"] == "completed", result["status"]
    print(
        json.dumps(
            {
                "restored_api_ready": True,
                "mock_job_completed": True,
                "output_count": result["succeeded_item_count"],
            }
        )
    )
