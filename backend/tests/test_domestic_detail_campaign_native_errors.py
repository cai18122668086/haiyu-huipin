"""Native command errors distinguish contract rejection from local IO failure."""

import pytest

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)


@pytest.mark.parametrize("kind", ["LOCAL_IO", "CONTRACT_REJECTION"])
def test_native_error_kind_is_structured_without_weakening_the_gate(tmp_path, kind):
    runner = AuroraDetailHarnessRunner()
    if kind == "LOCAL_IO":
        command = "inspect"
        args = {"session": tmp_path / "missing-session.json"}
    else:
        path = tmp_path / "input.json"
        path.write_bytes(b"{}")
        command = "start"
        args = {"input": path, "session": tmp_path / "session.json"}
    with pytest.raises(AuroraDetailHarnessError) as failure:
        runner.run(command, tmp_path, args)
    assert failure.value.native_failure_kind == kind
    assert str(failure.value).strip()
    assert not (tmp_path / "session.json").exists()
