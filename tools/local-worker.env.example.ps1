# Copy this file to D:\Polaris\local-worker\worker.env.ps1 and fill in the
# values returned by the Worker enrollment flow. This file is intentionally not
# committed with real credentials.

$env:POLARIS_WORKER_API_URL = "https://your-control-plane.example"
# Bootstrap token from registration. The Worker refreshes it and persists the
# replacement to the token file below; do not put the refreshed token in Git.
$env:POLARIS_WORKER_SESSION_TOKEN = "<short-lived-token>"
$env:POLARIS_WORKER_ENROLLMENT_TOKEN = "<optional-one-time-recovery-token>"
$env:POLARIS_WORKER_DEVICE_ID = "polaris-local-pc"
$env:POLARIS_WORKER_SESSION_TOKEN_FILE = "D:\Polaris\local-worker\worker.session-token"
# The Worker receives and persists this recovery credential on first
# enrollment; it lets future restarts re-register after a short session token
# expires without another browser enrollment.
$env:POLARIS_WORKER_RECOVERY_TOKEN_FILE = "D:\Polaris\local-worker\worker.recovery-token"
# Leave the Worker ID empty for the first run. The one-time enrollment token
# registers the device and the returned Worker ID is persisted automatically.
$env:POLARIS_WORKER_ID = ""
$env:POLARIS_WORKER_ID_FILE = "D:\Polaris\local-worker\worker.id"
$env:POLARIS_WORKER_PROFILE_ROOT = "D:\Polaris\ecommerce-profiles"
$env:POLARIS_WORKER_CAPABILITIES = "browser.taobao,browser.tmall"
# Two independent accounts can run together; each account remains exclusive.
# Supported range: 1..4. Omission keeps the previous single-slot behavior.
$env:POLARIS_WORKER_CONCURRENCY = "2"
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\Tools\ms-playwright"
# The signed-in admin page sends a newly minted enrollment token to this
# loopback bridge automatically. Keep the bridge bound to the local machine.
$env:POLARIS_WORKER_BRIDGE_PORT = "8765"

# Optional tuning. Keep the defaults unless the control plane requires a
# different polling cadence.
# $env:POLARIS_WORKER_HEARTBEAT_INTERVAL = "20"
# $env:POLARIS_WORKER_POLL_INTERVAL = "2"
# $env:POLARIS_WORKER_LEASE_INTERVAL = "30"
# $env:POLARIS_WORKER_TOKEN_REFRESH_INTERVAL = "30"
