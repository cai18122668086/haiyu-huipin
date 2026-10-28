# Aurora Domestic E-commerce Detail Page Skill v4.3.0

This directory is a self-contained Codex Skill package.

## Install

Copy the entire directory to the Codex skills directory on the target machine:

- Windows: `%USERPROFILE%\\.codex\\skills\\aurora-domestic-ecommerce-detail-page`
- macOS/Linux: `~/.codex/skills/aurora-domestic-ecommerce-detail-page`

The Codex host should discover `SKILL.md` and `agents/openai.yaml` automatically.

## Runtime dependency

The harness uses Python 3.10+, Pillow and jsonschema. Install them if the host does not provide them:

```bash
python -m pip install -r requirements.txt
```

The Skill itself does not depend on Windows-specific paths. Run commands from this directory or pass absolute paths to the session and plan files.

## Current contract

- Version: `4.3.0`
- Compact plan: `COMPACT_V1_3`
- Information contract: `information_contract_version=4.1`
- Expression contract: `expression_contract_version=4.3`
- Planning mode: `LIGHTWEIGHT_V41`
- Continuous pair canvas: `3:8`
- Decomposition window: `3:4`

The v4.3 layer retains v4.2 integrity controls and adds deterministic formula/ingredient routing, per-screen visual casts and content units, campaign subject/form diversity budgets, strict complete-product presence roles, and actual-pixel long-page anti-posterization audits. The underlying information contract remains `4.1`.

## Verification

From this directory:

```bash
python tests/test_regressions.py
python -m compileall scripts
```

The package intentionally excludes Python bytecode caches such as `__pycache__`.
