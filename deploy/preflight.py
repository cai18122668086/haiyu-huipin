#!/usr/bin/env python3
"""Fail-closed static and deployment preflight for internal staging.

This script uses only the Python standard library. It never prints environment
values, so deployment checks can safely name a failing variable without
disclosing its content.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_SERVICES = {
    "postgres",
    "redis",
    "minio",
    "migrate",
    "api",
    "worker",
    "dispatcher",
    "frontend",
    "caddy",
}
EXPECTED_NETWORKS = {"edge", "backend", "ingress", "provider-egress"}
EXPECTED_SERVICE_NETWORKS = {
    "postgres": {"backend"},
    "redis": {"backend"},
    "minio": {"backend"},
    "migrate": {"backend"},
    "api": {"edge", "backend"},
    "worker": {"backend", "provider-egress"},
    "dispatcher": {"backend"},
    "frontend": {"edge"},
    "caddy": {"edge", "ingress"},
}
LONG_RUNNING_SERVICES = EXPECTED_SERVICES - {"migrate"}
PROVIDER_SERVICES = {"api", "worker"}
PROVIDER_FLAG_SERVICES = {"api", "worker", "dispatcher"}
PROVIDER_FLAG_KEYS = (
    "POLARIS_PRODUCT_IMAGE_PROVIDER_ENABLED",
    "POLARIS_REFERENCE_REMIX_PROVIDER_ENABLED",
    "POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED",
    "POLARIS_CREATIVE_IMAGE_PROVIDER_ENABLED",
    "POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED",
    "POLARIS_DOMESTIC_MAIN_IMAGE_PROVIDER_ENABLED",
    "POLARIS_CROSSBORDER_DETAIL_PROVIDER_ENABLED",
)
PROVIDER_QUOTA_KEY = "POLARIS_PROVIDER_QUOTA_ENABLED"
MOCK_SERVICES = {"migrate", "dispatcher"}
ALLOWED_TASK_RUNNERS = {"mock", "tengli"}
INTERNAL_DEPLOYMENT_ENVIRONMENT = "development"
MAX_PROVIDER_ALLOWANCE = 20_000_000
AURORA_WORK_VOLUME = "polaris_aurora_work_data:/app/.data/aurora-main-image"
AURORA_WORK_SERVICES = {"api", "worker", "dispatcher"}
THIRD_PARTY_IMAGE_KEYS = {
    "POLARIS_PYTHON_BASE_IMAGE",
    "POLARIS_NODE_BASE_IMAGE",
    "POLARIS_POSTGRES_IMAGE",
    "POLARIS_REDIS_IMAGE",
    "POLARIS_MINIO_IMAGE",
    "POLARIS_CADDY_IMAGE",
}
APPLICATION_IMAGE_KEYS = {
    "POLARIS_BACKEND_IMAGE",
    "POLARIS_FRONTEND_IMAGE",
}
SENSITIVE_KEYS = {
    "POLARIS_POSTGRES_PASSWORD",
    "POLARIS_REDIS_PASSWORD",
    "POLARIS_MINIO_ROOT_USER",
    "POLARIS_MINIO_ROOT_PASSWORD",
    "POLARIS_ACCESS_URL_SECRET",
}
PROVIDER_SENSITIVE_KEYS = {
    "POLARIS_TENGLI_API_KEY",
    "POLARIS_ANALYSIS_API_KEY",
    "POLARIS_IMAGE_API_KEY",
}
DIGEST_REFERENCE = re.compile(
    r"^[a-z0-9][a-z0-9./_-]*(?::[A-Za-z0-9._-]+)?@sha256:([0-9a-f]{64})$"
)
RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
APPLICATION_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9./_-]*:([0-9a-f]{40})$")
REQUIRED_RUNTIME_PACKAGES = {"alembic", "celery", "uvicorn"}
NPM_LIFECYCLE_SCRIPTS = {
    "preinstall",
    "install",
    "postinstall",
    "prepare",
    "prepublish",
    "prepublishonly",
    "prepack",
    "postpack",
    "dependencies",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_services(compose: str) -> dict[str, str]:
    lines = compose.splitlines()
    try:
        start = lines.index("services:") + 1
    except ValueError:
        return {}
    services: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", line)
        if match:
            current = match.group(1)
            services[current] = [line]
        elif current is not None:
            services[current].append(line)
    return {name: "\n".join(block) for name, block in services.items()}


def service_networks(block: str) -> set[str]:
    lines = block.splitlines()
    try:
        start = lines.index("    networks:") + 1
    except ValueError:
        return set()
    networks: set[str] = set()
    for line in lines[start:]:
        match = re.fullmatch(r"      - ([a-z][a-z0-9_-]*)", line)
        if match:
            networks.add(match.group(1))
            continue
        if line and not line.startswith("      "):
            break
    return networks


def parse_networks(compose: str) -> dict[str, str]:
    lines = compose.splitlines()
    try:
        start = lines.index("networks:") + 1
    except ValueError:
        return {}
    networks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break
        match = re.fullmatch(r"  ([a-z][a-z0-9_-]*):\s*", line)
        if match:
            current = match.group(1)
            networks[current] = [line]
        elif current is not None:
            networks[current].append(line)
    return {name: "\n".join(block) for name, block in networks.items()}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("environment file contains a non-assignment line")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def memory_mib(value: str) -> int:
    match = re.fullmatch(r"([0-9]+)([mMgG])", value.strip())
    if not match:
        raise ValueError(f"unsupported memory limit format: {value}")
    amount = int(match.group(1))
    return amount if match.group(2).lower() == "m" else amount * 1024


def service_resources(
    services: dict[str, str],
) -> dict[str, tuple[int, float, int]]:
    resources: dict[str, tuple[int, float, int]] = {}
    for name in EXPECTED_SERVICES:
        block = services.get(name, "")
        memory_match = re.search(r"(?m)^    mem_limit:\s*(\S+)\s*$", block)
        cpu_match = re.search(r'(?m)^    cpus:\s*"?([0-9.]+)"?\s*$', block)
        pids_match = re.search(r"(?m)^    pids_limit:\s*([0-9]+)\s*$", block)
        if memory_match and cpu_match and pids_match:
            resources[name] = (
                memory_mib(memory_match.group(1)),
                float(cpu_match.group(1)),
                int(pids_match.group(1)),
            )
    return resources


def resource_totals(services: dict[str, str]) -> tuple[int, float]:
    resources = service_resources(services)
    missing = EXPECTED_SERVICES - set(resources)
    if missing:
        raise ValueError(
            "cannot total resources with incomplete services: "
            + ", ".join(sorted(missing))
        )
    total_memory = sum(resources[name][0] for name in LONG_RUNNING_SERVICES)
    total_cpus = sum(resources[name][1] for name in LONG_RUNNING_SERVICES)
    return total_memory, total_cpus


def service_queues(block: str) -> set[str]:
    """Return queue names explicitly passed to a Celery service command."""

    queues: set[str] = set()
    for match in re.findall(r"--queues(?:=|\s+)([a-z0-9_,-]+)", block):
        queues.update(queue for queue in match.split(",") if queue)
    return queues


def validate_queue_topology(
    compose: str, *, expected: dict[str, set[str]]
) -> list[str]:
    """Fail closed when worker queue assignments drift from the task routes."""

    services = parse_services(compose)
    errors: list[str] = []
    for service, expected_queues in expected.items():
        if service not in services:
            errors.append(f"{service} service is missing for queue topology validation")
            continue
        actual_queues = service_queues(services[service])
        if actual_queues != expected_queues:
            expected_text = ", ".join(sorted(expected_queues)) or "none"
            actual_text = ", ".join(sorted(actual_queues)) or "none"
            errors.append(
                f"{service} must listen only to queue(s) {expected_text}; found {actual_text}"
            )
    return errors


def validate_resource_documentation(
    readme: str,
    services: dict[str, str],
    errors: list[str],
) -> None:
    marker_start = "<!-- preflight:resource-budget:start -->"
    marker_end = "<!-- preflight:resource-budget:end -->"
    if readme.count(marker_start) != 1 or readme.count(marker_end) != 1:
        errors.append("README resource budget markers are missing or duplicated")
        return
    table = readme.split(marker_start, 1)[1].split(marker_end, 1)[0]
    resources = service_resources(services)
    if set(resources) != EXPECTED_SERVICES:
        errors.append("Compose resources cannot be compared with README")
        return
    for name in sorted(EXPECTED_SERVICES):
        memory, cpus, pids = resources[name]
        expected_row = f"| `{name}` | {memory} | {cpus:.2f} | {pids} |"
        if table.count(expected_row) != 1:
            errors.append(f"README resource row does not match Compose for {name}")
    total_memory = sum(resources[name][0] for name in LONG_RUNNING_SERVICES)
    total_cpus = sum(resources[name][1] for name in LONG_RUNNING_SERVICES)
    total_pids = sum(resources[name][2] for name in LONG_RUNNING_SERVICES)
    expected_total = (
        "| **Long-running total** | "
        f"**{total_memory}** | **{total_cpus:.2f}** | **{total_pids}** |"
    )
    if table.count(expected_total) != 1:
        errors.append("README long-running resource total does not match Compose")


def dockerfile_instructions(dockerfile: str) -> list[tuple[str, str]]:
    logical_lines: list[str] = []
    pending = ""
    for raw_line in dockerfile.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""
    if pending:
        logical_lines.append(pending)
    instructions: list[tuple[str, str]] = []
    for line in logical_lines:
        parts = line.split(None, 1)
        instructions.append((parts[0].upper(), parts[1] if len(parts) == 2 else ""))
    return instructions


def command_tokens(command: str) -> list[str]:
    if command.lstrip().startswith("["):
        try:
            value = json.loads(command)
        except ValueError:
            return []
        return [str(token) for token in value] if isinstance(value, list) else []
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def npm_scripts_from_tokens(tokens: list[str]) -> list[str]:
    scripts: list[str] = []
    lowered = [token.lower() for token in tokens]
    separators = {"&&", "||", ";", "|"}
    executables = {"npm", "npm.cmd", "npm.exe"}
    for index, token in enumerate(lowered):
        executable = token.replace("\\", "/").rsplit("/", 1)[-1]
        if executable not in executables:
            continue
        end = index + 1
        while end < len(lowered):
            candidate = lowered[end].replace("\\", "/").rsplit("/", 1)[-1]
            if lowered[end] in separators or candidate in executables:
                break
            end += 1
        segment = lowered[index + 1 : end]
        run_index = next(
            (
                offset
                for offset, candidate in enumerate(segment)
                if candidate in {"run", "run-script"}
            ),
            None,
        )
        if run_index is None:
            continue
        script = next(
            (
                candidate
                for candidate in segment[run_index + 1 :]
                if not candidate.startswith("-")
            ),
            "<dynamic>",
        )
        scripts.append(script)
    return scripts


def raw_command_tokens(command: str) -> list[str]:
    """Lex raw RUN text without trusting outer-shell quoting boundaries."""

    return re.findall(
        r"&&|\|\||[;|]|[A-Za-z0-9_.:@/\\-]+",
        command,
        flags=re.IGNORECASE,
    )


def npm_run_scripts(dockerfile: str) -> tuple[list[str], list[str]]:
    raw_scripts: list[str] = []
    token_scripts: list[str] = []
    for instruction, command in dockerfile_instructions(dockerfile):
        if instruction != "RUN":
            continue
        raw_scripts.extend(npm_scripts_from_tokens(raw_command_tokens(command)))
        token_scripts.extend(npm_scripts_from_tokens(command_tokens(command)))
    return raw_scripts, token_scripts


def validate_source(repo_root: Path) -> tuple[list[str], tuple[int, float]]:
    errors: list[str] = []
    deploy = repo_root / "deploy"
    required = {
        ".env.internal.example",
        "Caddyfile",
        "README.md",
        "backend.Dockerfile",
        "compose.internal.yml",
        "frontend.Dockerfile",
    }
    missing = sorted(name for name in required if not (deploy / name).is_file())
    if missing:
        errors.append(f"missing deployment files: {', '.join(missing)}")
        return errors, (0, 0.0)

    approved_compose = {"compose.internal.yml", "compose.production.yml"}
    forbidden_compose = sorted(
        path.name
        for path in deploy.glob("compose*.yml")
        if path.name not in approved_compose
    )
    if forbidden_compose:
        errors.append("unapproved real-provider or alternate compose files are not allowed")


    compose = read_text(deploy / "compose.internal.yml")
    services = parse_services(compose)
    if set(services) != EXPECTED_SERVICES:
        errors.append(
            "service set must be exactly: " + ", ".join(sorted(EXPECTED_SERVICES))
        )

    environment_expression = (
        f"POLARIS_ENVIRONMENT: {INTERNAL_DEPLOYMENT_ENVIRONMENT}"
    )
    if compose.count(environment_expression) != 1:
        errors.append(
            "internal staging must remain pinned to the development environment"
        )
    runner_expression = "POLARIS_TASK_RUNNER: ${POLARIS_TASK_RUNNER:-mock}"
    if compose.count(runner_expression) != 1:
        errors.append("the Provider runner must be environment-selectable with mock default")
    quota_expression = (
        "POLARIS_PROVIDER_QUOTA_ENABLED: "
        "${POLARIS_PROVIDER_QUOTA_ENABLED:-false}"
    )
    if compose.count(quota_expression) != 1:
        errors.append(
            "Provider quota must be environment-selectable with false default"
        )
    for key in PROVIDER_FLAG_KEYS:
        expression = f"{key}: ${{{key}:-false}}"
        if compose.count(expression) != 1:
            errors.append(f"{key} must be environment-selectable with false default")
    if compose.count("POLARIS_ASSET_ACCESS_URL_MODE: same_origin") != 1:
        errors.append("internal S3 assets must use the same-origin access proxy")
    if compose.count("POLARIS_AURORA_WORK_ROOT: /app/.data/aurora-main-image") != 1:
        errors.append("internal staging must configure the Aurora work root")
    for name in AURORA_WORK_SERVICES:
        if AURORA_WORK_VOLUME not in services.get(name, ""):
            errors.append(f"{name} must mount the shared Aurora work volume")
    skill_root_expression = (
        "POLARIS_XINGHE_SKILL_ROOT: "
        "${POLARIS_XINGHE_SKILL_ROOT:-/app/skill_sources/xinghe-ecommerce-detail}"
    )
    if compose.count(skill_root_expression) != 1:
        errors.append("the Xinghe skill root must use the packaged runtime default")
    provider_merge = "<<: [*backend-common-environment, *provider-environment]"
    for name in PROVIDER_SERVICES:
        if provider_merge not in services.get(name, ""):
            errors.append(f"{name} is missing the shared Provider configuration")
    for name in PROVIDER_FLAG_SERVICES:
        if "*backend-common-environment" not in services.get(name, ""):
            errors.append(f"{name} is missing the shared Provider feature switches")
    for name in MOCK_SERVICES:
        if "POLARIS_TASK_RUNNER: mock" not in services.get(name, ""):
            errors.append(f"{name} is not explicitly pinned to the mock runner")
    errors.extend(
        validate_queue_topology(
            compose,
            expected={"worker": {"default"}},
        )
    )
    if "crawl-worker" in services:
        errors.extend(
            validate_queue_topology(
                compose,
                expected={"crawl-worker": {"crawl"}},
            )
        )
    for name in EXPECTED_SERVICES - PROVIDER_SERVICES:
        if "*provider-environment" in services.get(name, ""):
            errors.append(f"{name} must not receive Provider credentials")

    if compose.count("\n    ports:") != 1:
        errors.append("exactly one service may declare host ports")
    if '"127.0.0.1:8080:8080"' not in services.get("caddy", ""):
        errors.append("Caddy must be the sole 127.0.0.1:8080 publisher")
    for name, block in services.items():
        if name != "caddy" and "\n    ports:" in block:
            errors.append(f"{name} must not publish a host port")

    networks = parse_networks(compose)
    if set(networks) != EXPECTED_NETWORKS:
        errors.append(
            "network set must be exactly: " + ", ".join(sorted(EXPECTED_NETWORKS))
        )
    expected_internal = {
        "edge": "true",
        "backend": "true",
        "ingress": "false",
        "provider-egress": "false",
    }
    for network, expected in expected_internal.items():
        flags = re.findall(r"(?m)^    internal:\s*(true|false)\s*$", networks.get(network, ""))
        if flags != [expected]:
            errors.append(f"{network} network must set internal exactly to {expected}")
    for name, block in services.items():
        actual_networks = service_networks(block)
        expected_networks = EXPECTED_SERVICE_NETWORKS.get(name, set())
        if actual_networks != expected_networks:
            errors.append(
                f"{name} network attachments must be exactly: "
                + ", ".join(sorted(expected_networks))
            )

    image_lines = re.findall(r"(?m)^\s+image:\s*(.+?)\s*$", compose)
    if len(image_lines) != len(EXPECTED_SERVICES):
        errors.append("each service must declare one immutable image reference")
    for image in image_lines:
        if not re.fullmatch(r"\$\{POLARIS_[A-Z0-9_]+_IMAGE:\?[^}]+\}", image):
            errors.append("compose image references must be required environment variables")
            break

    for name, block in services.items():
        for key in ("mem_limit:", "cpus:", "pids_limit:"):
            if key not in block:
                errors.append(f"{name} is missing {key[:-1]}")
        if "<<: *service-security" not in block:
            errors.append(f"{name} is missing the shared security/logging policy")
        if "pull_policy: never" not in block:
            errors.append(f"{name} must refuse implicit image pulls")
        if name in LONG_RUNNING_SERVICES:
            if "healthcheck:" not in block:
                errors.append(f"{name} is missing a healthcheck")
            if "restart: unless-stopped" not in block:
                errors.append(f"{name} is missing the restart policy")

    for name in ("migrate", "api", "worker", "dispatcher"):
        block = services.get(name, "")
        for requirement in ("user: \"10001:10001\"", "read_only: true", "cap_drop:"):
            if requirement not in block:
                errors.append(f"{name} is missing hardened application isolation")
                break
    for name in ("frontend", "caddy"):
        block = services.get(name, "")
        for requirement in ("read_only: true", "cap_drop:", "tmpfs:"):
            if requirement not in block:
                errors.append(f"{name} is missing hardened runtime isolation")
                break

    caddy_block = services.get("caddy", "")
    caddy_capability = "    cap_add:\n      - NET_BIND_SERVICE\n    init: true"
    if caddy_capability not in caddy_block:
        errors.append("Caddy must retain only NET_BIND_SERVICE for its upstream file capability")
    if compose.count("\n    cap_add:") != 1:
        errors.append("only Caddy may add a Linux capability")
    if "http://127.0.0.1:8080/health/ready" not in caddy_block:
        errors.append("Caddy healthcheck must use API readiness")
    if "condition: service_healthy" not in caddy_block:
        errors.append("Caddy must wait for healthy upstreams")

    readme = read_text(deploy / "README.md")
    if "--cap-add NET_BIND_SERVICE" not in readme:
        errors.append("README Caddy validation must retain NET_BIND_SERVICE")

    caddyfile = read_text(deploy / "Caddyfile")
    hsts_name = "strict" + "-transport-security"
    if hsts_name in caddyfile.lower() or "upgrade-insecure-requests" in caddyfile.lower():
        errors.append("HTTP staging must not enable transport-upgrade headers or CSP")
    required_headers = (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    )
    for header in required_headers:
        if header not in caddyfile:
            errors.append(f"Caddyfile is missing {header}")
    csp_match = re.search(r'Content-Security-Policy "([^"]+)"', caddyfile)
    if not csp_match:
        errors.append("Caddyfile is missing a parseable CSP")
    else:
        csp = csp_match.group(1)
        for directive in (
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
        ):
            if directive not in csp:
                errors.append(f"CSP is missing required directive: {directive}")
        allowed_loopback_bridge = {
            "http://localhost:8765/v1/enrollment",
            "http://127.0.0.1:8765/v1/enrollment",
        }
        http_sources = re.findall(r"https?://[^\s;]+", csp)
        if any(source not in allowed_loopback_bridge for source in http_sources):
            errors.append("CSP must not allow external HTTP origins")

    for dockerfile_name, arg_name, allowed_stages in (
        ("backend.Dockerfile", "POLARIS_PYTHON_BASE_IMAGE", set()),
        ("frontend.Dockerfile", "POLARIS_NODE_BASE_IMAGE", {"dependencies"}),
    ):
        dockerfile = read_text(deploy / dockerfile_name)
        if f"ARG {arg_name}" not in dockerfile:
            errors.append(f"{dockerfile_name} is missing required base image ARG")
        from_lines = re.findall(r"(?m)^FROM\s+(\S+)", dockerfile)
        allowed_from = {f"${{{arg_name}}}"} | allowed_stages
        if not from_lines or any(line not in allowed_from for line in from_lines):
            errors.append(f"{dockerfile_name} contains a non-ARG base image")
        if re.search(rf"(?m)^ARG {arg_name}=", dockerfile):
            errors.append(f"{dockerfile_name} must not default its base image")

    backend_dockerfile = read_text(deploy / "backend.Dockerfile")
    if "backend/requirements.lock" not in backend_dockerfile:
        errors.append("backend image must require the product dependency lock")
    if "PIP_REQUIRE_HASHES=1" not in backend_dockerfile:
        errors.append("backend dependency installation must require hashes")
    if "PIP_REQUIRE_HASHES=0" in backend_dockerfile:
        errors.append("backend image must never disable pip hash mode")
    pip_install_lines = [
        line.strip()
        for line in backend_dockerfile.splitlines()
        if re.search(r"\b(?:python\s+-m\s+)?pip\s+install\b", line)
    ]
    required_pip_install = (
        "RUN python -m pip install --require-hashes "
        "--only-binary=:all: -r requirements.lock"
    )
    if pip_install_lines != [required_pip_install]:
        errors.append(
            "backend image must have exactly one hash-locked, binary-only pip install"
        )
    forbidden_pip_patterns = (
        r"\bpip\s+install\b[^\n]*(?:^|\s)\.(?:\s|$)",
        r"(?:^|\s)(?:-e|--editable)(?:\s|$)",
        r"git\+",
        r"https?://",
        r"\s@\s",
    )
    for pattern in forbidden_pip_patterns:
        if re.search(pattern, backend_dockerfile, flags=re.IGNORECASE):
            errors.append("backend image contains a forbidden project/VCS/URL pip install")
            break
    for forbidden_copy in ("backend/pyproject.toml", "backend/README.md"):
        if forbidden_copy in backend_dockerfile:
            errors.append("backend runtime image must not copy build metadata")
            break
    for required_copy in (
        "COPY backend/alembic.ini ./",
        "COPY backend/app ./app",
        "COPY backend/catalog_assets ./catalog_assets",
        "COPY backend/skill_sources ./skill_sources",
        "COPY backend/migrations ./migrations",
    ):
        if required_copy not in backend_dockerfile:
            errors.append(f"backend runtime image is missing: {required_copy}")

    frontend_dockerfile = read_text(deploy / "frontend.Dockerfile")
    hosting_copy = "COPY .openai/hosting.json .openai/hosting.json"
    if hosting_copy not in frontend_dockerfile:
        errors.append("frontend build image is missing tracked hosting metadata")
    npm_install_lines = [
        line.strip()
        for line in frontend_dockerfile.splitlines()
        if re.search(r"\bnpm\s+(?:ci|install)\b", line)
    ]
    required_npm_ci = "RUN npm ci --ignore-scripts --no-audit --fund=false"
    if npm_install_lines != [required_npm_ci]:
        errors.append(
            "frontend image must use exactly one npm ci with --ignore-scripts"
        )
    raw_run_scripts, token_run_scripts = npm_run_scripts(frontend_dockerfile)
    run_scripts = raw_run_scripts + token_run_scripts
    lifecycle_scripts = [
        script for script in run_scripts if script in NPM_LIFECYCLE_SCRIPTS
    ]
    if lifecycle_scripts:
        errors.append(
            "frontend Dockerfile RUN must not invoke npm lifecycle scripts: "
            + ", ".join(sorted(set(lifecycle_scripts)))
        )
    if raw_run_scripts != ["build"]:
        errors.append(
            "frontend Dockerfile raw RUN text may invoke exactly one npm run build"
        )
    if any(script != "build" for script in token_run_scripts):
        errors.append("frontend Dockerfile tokenized RUN may invoke only npm run build")
    try:
        package = json.loads(read_text(repo_root / "package.json"))
        scripts = package.get("scripts", {})
    except (OSError, ValueError, AttributeError):
        scripts = {}
        errors.append("package.json scripts cannot be validated")
    patch_script = "node build/patch-vinext-production.mjs"
    for script_name in ("build", "start"):
        script = scripts.get(script_name, "") if isinstance(scripts, dict) else ""
        if patch_script not in script:
            errors.append(
                f"package.json {script_name} must apply the verified Vinext patch"
            )

    example = parse_env(deploy / ".env.internal.example")
    backend_example_path = repo_root / "backend" / ".env.example"
    if backend_example_path.is_file():
        backend_example = parse_env(backend_example_path)
    else:
        backend_example = {}
        errors.append("backend/.env.example is missing")
    for key in SENSITIVE_KEYS | PROVIDER_SENSITIVE_KEYS:
        value = example.get(key, "")
        if not (value.startswith("<") and value.endswith(">")):
            errors.append(f"example value for {key} must remain an unusable marker")
    for key in PROVIDER_FLAG_KEYS:
        for path, values in (
            ("deploy/.env.internal.example", example),
            ("backend/.env.example", backend_example),
        ):
            if values.get(key) != "false":
                errors.append(f"{path} must default {key} to false")
    for key in THIRD_PARTY_IMAGE_KEYS | APPLICATION_IMAGE_KEYS | {"POLARIS_RELEASE_SHA"}:
        value = example.get(key, "")
        if not (value.startswith("<") or "<" in value):
            errors.append(f"example value for {key} must contain an obvious marker")

    dockerignore = read_text(repo_root / ".dockerignore")
    for pattern in (".env", "**/.env", "*.pem", "*.key"):
        if pattern not in dockerignore:
            errors.append(f".dockerignore is missing secret exclusion {pattern}")

    try:
        totals = resource_totals(services)
    except ValueError as exc:
        errors.append(str(exc))
        totals = (0, 0.0)
    validate_resource_documentation(
        readme,
        services,
        errors,
    )
    return errors, totals


def lock_blockers(repo_root: Path) -> list[str]:
    lock_path = repo_root / "backend" / "requirements.lock"
    if not lock_path.is_file():
        return ["backend/requirements.lock is absent"]
    content = read_text(lock_path)
    blockers: list[str] = []
    requirements: dict[str, list[str]] = {}
    current_name: str | None = None
    active_lines = [
        line
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    active_text = "\n".join(active_lines)
    forbidden_patterns = (
        r"(?:^|\s)(?:-e|--editable)(?:\s|$)",
        r"git\+",
        r"https?://",
        r"\s@\s",
        r"--(?:extra-)?index-url",
    )
    if any(
        re.search(pattern, active_text, flags=re.IGNORECASE)
        for pattern in forbidden_patterns
    ):
        blockers.append("backend/requirements.lock contains editable, VCS, URL, or index input")

    for line in active_lines:
        if not line[0].isspace():
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s\\]+)\s+\\", line)
            if not match:
                blockers.append(
                    "backend/requirements.lock contains a non-exact requirement"
                )
                current_name = None
                continue
            current_name = match.group(1).lower().replace("_", "-")
            requirements[current_name] = []
            continue
        hash_match = re.fullmatch(
            r"\s+--hash=sha256:([0-9a-f]{64})(?:\s+\\)?", line
        )
        if not hash_match or current_name is None:
            blockers.append(
                "backend/requirements.lock contains an unrecognized continuation"
            )
            continue
        digest = hash_match.group(1)
        if len(set(digest)) == 1:
            blockers.append(
                f"backend/requirements.lock has a sentinel hash for {current_name}"
            )
        requirements[current_name].append(digest)

    if not requirements:
        blockers.append("backend/requirements.lock has no exact requirements")
    for name, hashes in requirements.items():
        if not hashes:
            blockers.append(f"backend/requirements.lock has no sha256 hash for {name}")
    missing_runtime = sorted(REQUIRED_RUNTIME_PACKAGES - set(requirements))
    if missing_runtime:
        blockers.append(
            "backend/requirements.lock is missing runtime packages: "
            + ", ".join(missing_runtime)
        )
    return list(dict.fromkeys(blockers))


def validate_digest_reference(key: str, value: str, errors: list[str]) -> None:
    match = DIGEST_REFERENCE.fullmatch(value)
    if not match:
        errors.append(f"{key} must be an approved name@sha256 reference")
        return
    digest = match.group(1)
    if len(set(digest)) == 1:
        errors.append(f"{key} contains a sentinel digest")


def validate_deployment(
    repo_root: Path,
    env_file: Path | None,
    host_memory_mib: int | None,
    host_cpus: float | None,
    free_disk_mib: int | None,
    free_inode_percent: float | None,
    candidate_images_mib: int | None,
    paired_backup_mib: int | None,
) -> tuple[list[str], tuple[int, float], int]:
    errors, totals = validate_source(repo_root)
    errors.extend(lock_blockers(repo_root))
    required_disk = 0

    if env_file is None:
        errors.append("--env-file is required for deployment mode")
        values: dict[str, str] = {}
    else:
        try:
            values = parse_env(env_file)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read deployment environment safely: {exc}")
            values = {}

    for key in THIRD_PARTY_IMAGE_KEYS:
        validate_digest_reference(key, values.get(key, ""), errors)

    release = values.get("POLARIS_RELEASE_SHA", "")
    if not RELEASE_SHA.fullmatch(release):
        errors.append("POLARIS_RELEASE_SHA must be an approved 40-hex commit")
    for key in APPLICATION_IMAGE_KEYS:
        match = APPLICATION_REFERENCE.fullmatch(values.get(key, ""))
        if not match:
            errors.append(f"{key} must use an immutable 40-hex release tag")
        elif match.group(1) != release:
            errors.append(f"{key} tag must equal POLARIS_RELEASE_SHA")

    for key in SENSITIVE_KEYS:
        value = values.get(key, "")
        upper = value.upper()
        if not value or "<" in value or "SET_ON_SERVER" in upper or "REPLACE" in upper:
            errors.append(f"{key} is missing or still a placeholder")


    provider_flags: dict[str, bool] = {}
    for key in PROVIDER_FLAG_KEYS:
        raw_value = values.get(key, "false")
        if raw_value not in {"true", "false"}:
            errors.append(f"{key} must be true or false")
        provider_flags[key] = raw_value == "true"
    any_provider_enabled = any(provider_flags.values())

    runner = values.get("POLARIS_TASK_RUNNER", "mock")
    if runner not in ALLOWED_TASK_RUNNERS:
        errors.append("POLARIS_TASK_RUNNER must be mock or tengli")
    if any_provider_enabled and runner != "tengli":
        errors.append("Provider feature switches require POLARIS_TASK_RUNNER=tengli")

    quota_raw = values.get(PROVIDER_QUOTA_KEY, "false")
    if quota_raw not in {"true", "false"}:
        errors.append(f"{PROVIDER_QUOTA_KEY} must be true or false")
    # This deployment profile is pinned to development and may intentionally
    # disable the durable quota ledger for an unrestricted internal test account.
    # The application startup gate still rejects quota-free real Provider
    # execution whenever POLARIS_ENVIRONMENT=production.
    real_provider_requested = runner == "tengli" or any_provider_enabled

    if real_provider_requested:
        legacy_key = values.get("POLARIS_TENGLI_API_KEY", "")
        legacy_usable = bool(
            legacy_key
            and "<" not in legacy_key
            and "SET_ON_SERVER" not in legacy_key.upper()
            and "REPLACE" not in legacy_key.upper()
        )
        for key in ("POLARIS_ANALYSIS_API_KEY", "POLARIS_IMAGE_API_KEY"):
            value = values.get(key, "")
            upper = value.upper()
            if not legacy_usable and (
                not value
                or "<" in value
                or "SET_ON_SERVER" in upper
                or "REPLACE" in upper
            ):
                errors.append(f"{key} is missing or still a placeholder")
        # Legacy Tengli credentials remain accepted for rollback compatibility,
        # but are no longer required when both split-provider keys are set.
        if not legacy_usable and (
            not values.get("POLARIS_ANALYSIS_API_KEY")
            or not values.get("POLARIS_IMAGE_API_KEY")
        ):
            errors.append("split Provider API keys are required")
        if legacy_key and not legacy_usable:
            errors.append("POLARIS_TENGLI_API_KEY is missing or still a placeholder")
        endpoint = urlsplit(values.get("POLARIS_TENGLI_BASE_URL", ""))
        if endpoint.scheme != "https" or not endpoint.hostname:
            errors.append("POLARIS_TENGLI_BASE_URL must be an HTTPS Provider endpoint")
        for key in (
            "POLARIS_TENGLI_IMAGE_MODEL",
            "POLARIS_TENGLI_VISION_MODEL",
        ):
            if not values.get(key, "").strip():
                errors.append(f"{key} is required for the tengli runner")
        try:
            budget = float(values.get("POLARIS_TENGLI_SMOKE_BUDGET_USD", ""))
        except ValueError:
            budget = 0
        if not 0 < budget <= MAX_PROVIDER_ALLOWANCE:
            errors.append(
                "POLARIS_TENGLI_SMOKE_BUDGET_USD must be greater than 0 and "
                f"at most {MAX_PROVIDER_ALLOWANCE}"
            )
        try:
            daily_budget = float(values.get("POLARIS_TENGLI_DAILY_BUDGET_USD", ""))
            monthly_budget = float(
                values.get("POLARIS_TENGLI_MONTHLY_BUDGET_USD", "")
            )
        except ValueError:
            daily_budget = -1
            monthly_budget = -1
        if not 0 <= daily_budget <= MAX_PROVIDER_ALLOWANCE:
            errors.append(
                "POLARIS_TENGLI_DAILY_BUDGET_USD must be 0 (unlimited) or "
                f"at most {MAX_PROVIDER_ALLOWANCE}"
            )
        if not 0 <= monthly_budget <= MAX_PROVIDER_ALLOWANCE:
            errors.append(
                "POLARIS_TENGLI_MONTHLY_BUDGET_USD must be 0 (unlimited) or "
                f"at most {MAX_PROVIDER_ALLOWANCE}"
            )
        elif (
            daily_budget > 0
            and monthly_budget > 0
            and monthly_budget < daily_budget
        ):
            errors.append(
                "POLARIS_TENGLI_MONTHLY_BUDGET_USD must be unlimited or at "
                "least the daily budget"
            )
        try:
            daily_requests = int(
                values.get("POLARIS_TENGLI_DAILY_REQUEST_LIMIT", "")
            )
        except ValueError:
            daily_requests = -1
        if not 0 <= daily_requests <= MAX_PROVIDER_ALLOWANCE:
            errors.append(
                "POLARIS_TENGLI_DAILY_REQUEST_LIMIT must be 0 (unlimited) or "
                f"at most {MAX_PROVIDER_ALLOWANCE}"
            )
        try:
            planner_reserve = int(
                values.get("POLARIS_TENGLI_PLANNER_RESERVE_MICROS", "")
            )
        except ValueError:
            planner_reserve = 0
        if not 0 < planner_reserve <= 2_000_000:
            errors.append(
                "POLARIS_TENGLI_PLANNER_RESERVE_MICROS must be between 1 and 2000000"
            )
        concurrency = values.get("POLARIS_XINGHE_CONCURRENCY", "")
        if not concurrency.isdecimal() or not 1 <= int(concurrency) <= 3000:
            errors.append("POLARIS_XINGHE_CONCURRENCY must be from 1 through 3000 for tengli")
    total_memory, total_cpus = totals
    if host_memory_mib is None or host_memory_mib <= 0:
        errors.append("--host-memory-mib is required and must be positive")
    elif total_memory > host_memory_mib * 0.70:
        errors.append("long-running memory limits exceed 70% of host memory")
    if host_cpus is None or host_cpus <= 0:
        errors.append("--host-cpus is required and must be positive")
    elif total_cpus > host_cpus * 0.80:
        errors.append("long-running CPU limits exceed 80% of host CPUs")

    disk_inputs = (
        free_disk_mib,
        candidate_images_mib,
        paired_backup_mib,
    )
    if any(value is None or value < 0 for value in disk_inputs):
        errors.append(
            "--free-disk-mib, --candidate-images-mib and --paired-backup-mib "
            "are required and must be nonnegative"
        )
    else:
        assert free_disk_mib is not None
        assert candidate_images_mib is not None
        assert paired_backup_mib is not None
        required_disk = max(
            20 * 1024,
            2 * candidate_images_mib + paired_backup_mib + 10 * 1024,
        )
        if free_disk_mib < required_disk:
            errors.append("free Docker/backup disk is below the fail-closed reserve")

    if free_inode_percent is None or not 0 <= free_inode_percent <= 100:
        errors.append("--free-inode-percent is required in the range 0..100")
    elif free_inode_percent < 10:
        errors.append("free inode percentage is below 10%")

    return errors, totals, required_disk


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--mode", choices=("source", "deployment"), default="source")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--host-memory-mib", type=int)
    parser.add_argument("--host-cpus", type=float)
    parser.add_argument("--free-disk-mib", type=int)
    parser.add_argument("--free-inode-percent", type=float)
    parser.add_argument("--candidate-images-mib", type=int)
    parser.add_argument("--paired-backup-mib", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = (
        args.repo_root.resolve()
        if args.repo_root
        else Path(__file__).resolve().parents[1]
    )

    if args.mode == "source":
        errors, totals = validate_source(repo_root)
        errors.extend(lock_blockers(repo_root))
        if errors:
            print(f"BLOCKED source preflight ({len(errors)} issue(s))")
            for issue in errors:
                print(f"- {issue}")
            return 1
        print("PASS source preflight")
        print(
            "Long-running resource ceiling: "
            f"{totals[0]} MiB memory, {totals[1]:.2f} CPUs"
        )
        print("Requirements lock prerequisites: present")
        return 0

    errors, totals, required_disk = validate_deployment(
        repo_root=repo_root,
        env_file=args.env_file,
        host_memory_mib=args.host_memory_mib,
        host_cpus=args.host_cpus,
        free_disk_mib=args.free_disk_mib,
        free_inode_percent=args.free_inode_percent,
        candidate_images_mib=args.candidate_images_mib,
        paired_backup_mib=args.paired_backup_mib,
    )
    if errors:
        print(f"BLOCKED deployment preflight ({len(errors)} issue(s))")
        for issue in errors:
            print(f"- {issue}")
        return 1
    print("PASS deployment preflight")
    print(
        "Long-running resource ceiling: "
        f"{totals[0]} MiB memory, {totals[1]:.2f} CPUs"
    )
    print(f"Required free disk reserve: {required_disk} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
