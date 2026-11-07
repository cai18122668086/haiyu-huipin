"""Image-only Huipin cutover: preserve schema, campaigns, runtime and receipts."""

import fcntl
import json
import subprocess
import time
from urllib.request import urlopen

import yaml
from runtime_policy import environment_values


def run(r):
    lock = (r.ROOT / "activation.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    snapshot = r.verify_baseline()
    built = json.loads((r.ROOT / "build-result.json").read_text())
    tested = json.loads((r.ROOT / "test-result.json").read_text())
    frontend = json.loads((r.ROOT / "frontend-test-result.json").read_text())
    assert tested["exit_code"] == 0
    assert tested["image_id"] == built["backend_id"] == r.inspect(r.BACKEND)["Id"]
    assert frontend["status"] == "passed"
    assert frontend["image_id"] == built["frontend_id"] == r.inspect(r.FRONTEND)["Id"]
    expected = {"jobs": 0, "replicas": 0, "migration": "20261106_0040"}
    assert r.inner(r.base.STATUS_PROBE) == expected, "Wait for active customer tasks"
    before = snapshot["containers"]
    names = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"], text=True
    ).splitlines()
    others = {
        n: r.inspect(n)["Id"]
        for n in names
        if n not in {"huipin-prelaunch-" + s + "-1" for s in r.base.SERVICES}
        and not n.startswith(("async-acceptance-test-", "huipin-provider-failure-test-"))
    }
    original = r.base.OVERRIDE.read_bytes()
    config = json.loads(
        subprocess.check_output(
            r.COMPOSE + ["config", "--format", "json"], cwd=r.base.RUNTIME
        )
    )
    dump = r.ROOT / "before-provider-failure.private.dump"
    assert not dump.exists()
    with dump.open("wb") as output:
        subprocess.run(
            [
                "docker",
                "exec",
                "huipin-prelaunch-postgres-1",
                "pg_dump",
                "-U",
                "polaris",
                "-d",
                "huipin_prelaunch",
                "--format=custom",
            ],
            stdout=output,
            check=True,
        )
    dump.chmod(0o600)
    subprocess.run(
        ["docker", "exec", "-i", "huipin-prelaunch-postgres-1", "pg_restore", "--list"],
        input=dump.read_bytes(),
        stdout=subprocess.DEVNULL,
        check=True,
    )
    updated = yaml.safe_load(original)
    expected_config = json.loads(json.dumps(config))
    for s in ["api", "worker", "dispatcher", "migrate", "frontend"]:
        image = r.FRONTEND if s == "frontend" else r.BACKEND
        updated["services"][s]["image"] = image
        expected_config["services"][s]["image"] = image
    try:
        r.write_config(yaml.safe_dump(updated, sort_keys=False).encode())
        assert (
            json.loads(
                subprocess.check_output(
                    r.COMPOSE + ["config", "--format", "json"], cwd=r.base.RUNTIME
                )
            )
            == expected_config
        )
        assert r.inner(r.base.STATUS_PROBE) == expected
        subprocess.run(
            r.COMPOSE + ["up", "-d", "--no-deps", *r.base.SERVICES],
            cwd=r.base.RUNTIME,
            check=True,
        )
        for _ in range(90):
            states = [
                r.inspect("huipin-prelaunch-" + s + "-1")["State"]
                for s in r.base.SERVICES
            ]
            if all(s.get("Health", {}).get("Status") == "healthy" for s in states):
                break
            assert all(s["Running"] for s in states)
            time.sleep(1)
        else:
            raise RuntimeError("Candidate services not healthy")
        for s, previous in before.items():
            current = r.inspect("huipin-prelaunch-" + s + "-1")
            assert environment_values(current["Config"]["Env"]) == environment_values(
                previous["Config"]["Env"]
            )
            for key in ["Cmd", "Entrypoint", "WorkingDir", "User"]:
                assert current["Config"].get(key) == previous["Config"].get(key)
            for key in [
                "Memory",
                "NanoCpus",
                "PidsLimit",
                "Ulimits",
                "PortBindings",
            ]:
                assert current["HostConfig"].get(key) == previous["HostConfig"].get(
                    key
                ), (s, key)
            assert sorted(current["HostConfig"].get("Binds") or []) == sorted(
                previous["HostConfig"].get("Binds") or []
            ), (s, "Binds")
            if s != "frontend":
                assert (
                    json.loads(
                        subprocess.check_output(
                            ["docker", "exec", "-i", current["Id"], "python", "-"],
                            input=r.base.SOURCE_PROBE.encode(),
                        )
                    )
                    == r.MANIFEST["backend_sources"]
                )
        assert {n: r.inspect(n)["Id"] for n in others} == others
        assert {str(p): r.base.digest(p) for p in r.PROTECTED} == snapshot["protected"]
        assert r.welcome_campaign() == snapshot["welcome_campaign"]
        assert r.inner(r.base.STATUS_PROBE)["migration"] == expected["migration"]
        proof = r.verify_frontend(
            "huipin-prelaunch-frontend-1", r.MANIFEST["frontend_sources"]
        )
        for path in [
            "/",
            "/style-replica",
            "/account",
            "/api/v1/public/promotions/replica-welcome",
        ]:
            with urlopen(
                "https://huipin.haiyushuke.com" + path, timeout=30
            ) as response:
                assert response.status == 200
                response.read()
    except BaseException:
        r.write_config(original)
        subprocess.run(
            r.COMPOSE + ["up", "-d", "--no-deps", *r.base.SERVICES],
            cwd=r.base.RUNTIME,
            check=True,
        )
        r.write_json("activation.json", {"status": "rolled_back"})
        raise
    result = {
        "status": "activated",
        "revision": r.MANIFEST["revision"],
        "backend_image": r.BACKEND,
        "frontend_image": r.FRONTEND,
        "backup_bytes": dump.stat().st_size,
        "backup_sha256": r.base.digest(dump),
        "schema_and_campaigns_preserved": True,
        "runtime_preserved": True,
        "customer_tasks_restarted": False,
        **proof,
    }
    r.write_json("activation.json", result)
    print(json.dumps(result))
