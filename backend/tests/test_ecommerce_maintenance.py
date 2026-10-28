from __future__ import annotations

from types import SimpleNamespace


def test_maintenance_isolates_recovery_queue_and_cleanup_failures(monkeypatch) -> None:
    import app.ecommerce_maintenance as maintenance

    calls: list[str] = []

    class FakeSession:
        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def scalar(self, statement: object) -> int:
            del statement
            return 3

    runtime = SimpleNamespace(
        state=SimpleNamespace(session_factory=lambda: FakeSession())
    )
    monkeypatch.setattr(maintenance, "_build_runtime", lambda: runtime)

    def fail_recovery(session_factory: object) -> list[str]:
        del session_factory
        calls.append("recovery")
        raise RuntimeError("recovery unavailable")

    def fail_resume(app: object, session_factory: object) -> None:
        del app, session_factory
        calls.append("resume")
        raise RuntimeError("queue unavailable")

    async def fail_cleanup(app: object) -> int:
        del app
        calls.append("cleanup")
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(maintenance, "recover_interrupted_crawl_tasks", fail_recovery)
    monkeypatch.setattr(maintenance, "resume_queued_crawl_tasks", fail_resume)
    monkeypatch.setattr(maintenance, "cleanup_orphaned_crawl_assets", fail_cleanup)

    result = maintenance.run_once()

    assert calls == ["recovery", "resume", "cleanup"]
    assert result["recovered"] == 0
    assert result["resumed"] == 3
    assert result["orphan_assets_cleaned"] == 0
    assert result["recovery_errors"] == 1
    assert result["resume_errors"] == 1
    assert result["cleanup_errors"] == 1
    assert result["duration_ms"] >= 0

