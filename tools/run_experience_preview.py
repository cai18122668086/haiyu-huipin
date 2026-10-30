"""Isolated UX preview: synthetic membership, local data, no payment/generation."""

import sys
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings
from app.credit_memberships import add_months
from app.main import create_app
from app.models import CreditMembershipRecord, utc_now

if __name__ == "__main__":
    data = ROOT / "backend/.data/experience-preview"
    data.mkdir(parents=True, exist_ok=True)
    app = create_app(
        Settings(
            _env_file=None,
            environment="development",
            auth_mode="development",
            development_user_id="usr_development",
            development_workspace_id="ws_experience",
            development_workspace_name="体验验收工作区（测试）",
            database_url="sqlite:///" + (data / "preview.db").as_posix(),
            auto_create_schema=True,
            storage_backend="local",
            storage_dir=data / "storage",
            aurora_work_root=data / "aurora",
            auto_process_jobs=False,
            task_dispatcher="inline",
            credit_payments_enabled=False,
            credit_payment_reconciliation_enabled=False,
            workspace_credit_enforcement_enabled=True,
            reference_remix_provider_enabled=False,
            development_provider_settings_enabled=False,
            ecommerce_startup_recovery_enabled=False,
            ecommerce_session_dir=data / "sessions",
            ecommerce_1688_session_dir=data / "1688",
            ecommerce_jd_session_dir=data / "jd",
            ecommerce_pinduoduo_session_dir=data / "pdd",
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/workspaces").status_code == 200
        with app.state.session_factory() as session:
            membership = session.get(CreditMembershipRecord, "mem_experience_preview")
            if membership is None:
                now = utc_now()
                session.add(
                    CreditMembershipRecord(
                        id="mem_experience_preview",
                        workspace_id="ws_experience",
                        plan_id="pro",
                        plan_name="专业版",
                        monthly_credits=1500,
                        months=1,
                        starts_at=now,
                        ends_at=add_months(now, 1),
                    )
                )
                session.commit()
            else:
                membership.ends_at = add_months(membership.starts_at, 1)
                session.commit()
    uvicorn.run(app, host="127.0.0.1", port=18965, access_log=False)
