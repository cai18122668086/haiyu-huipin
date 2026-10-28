# 本机电商执行代理

本机代理主动通过 HTTPS 连接 Polaris 控制面，在本机 profile 中运行商品浏览器抓取。Cookie、localStorage、浏览器 profile 和 CDP 地址不会上传服务器。

## 启动前配置

在本机 shell 中设置以下环境变量。`POLARIS_WORKER_SESSION_TOKEN` 使用后台注册接口返回的短期 session token，不要写入代码、日志或命令历史。

```powershell
$env:POLARIS_WORKER_API_URL = "https://your-control-plane.example"
$env:POLARIS_WORKER_SESSION_TOKEN = "<short-lived-token>"
$env:POLARIS_WORKER_SESSION_TOKEN_FILE = "D:\Polaris\local-worker\worker.session-token"
$env:POLARIS_WORKER_ID = "wrk_xxx"
$env:POLARIS_WORKER_PROFILE_ROOT = "D:\Polaris\ecommerce-profiles"
$env:POLARIS_WORKER_CAPABILITIES = "browser.taobao,browser.tmall"
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\Tools\ms-playwright"
```

本机 Worker 使用仓库自带的 D 盘虚拟环境；每次启动前都要保留上述浏览器路径变量：

先执行配置自检：

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m app.ecommerce_local_worker --check-config
```

自检通过后启动：

```powershell
.\.venv\Scripts\python.exe -m app.ecommerce_local_worker
```

代理会周期发送心跳、领取任务并续租；按 `Ctrl+C` 或系统终止信号退出时，会停止领取并回报离线状态。首次灰度建议只配置一个淘宝/天猫能力和一个账号 profile。

## Windows 长期运行

推荐使用仓库提供的计划任务安装脚本。它在配置的 Windows 用户登录时启动，Worker 异常退出时由 supervisor 自动重启，日志只写入 D 盘：

```powershell
Set-Location D:\Projects\polaris-commerce-studio-model-pose-entrypoints
Set-ExecutionPolicy -Scope Process Bypass
.\tools\install_local_worker_task.ps1 -Action Install
# 编辑 D:\Polaris\local-worker\worker.env.ps1，填入真实 Worker 配置
.\tools\install_local_worker_task.ps1 -Action RunNow
```

如果目录尚不存在，第一次 `Install` 会先生成模板并以退出码 2 结束；填好配置后再次执行 `RunNow` 即会完成注册并立即启动。

`worker.env.ps1` 只存放在本机，不要提交仓库；建议只授予本机管理员和实际维护账号访问权限。管理员页面点击“注册本机 Worker”后，会通过本机 `127.0.0.1:8765` 桥接端口自动把一次性令牌交给正在运行的 Worker，不需要手动复制令牌。若桥接端口不可用，页面会保留令牌并提示检查本机任务。查看状态：

Worker session token 是短期令牌。supervisor 启动的 Worker 会定时轮换，并把新令牌原子写入 `POLARIS_WORKER_SESSION_TOKEN_FILE` 指定的文件；首次注册响应中的恢复凭证会写入同目录的 `.worker-recovery-token`（也可用 `POLARIS_WORKER_RECOVERY_TOKEN_FILE` 指定）。如果控制面连续不可达超过 session token 有效期，Worker 会自动使用恢复凭证换取新的 session/recovery token，重启和长期运行都不需要再次手动注册。只有恢复凭证本身过期、被撤销或首次注册尚未完成时，才需要在管理员页面重新授权一次设备。

```powershell
Get-ScheduledTask -TaskName PolarisCommerceLocalWorker
Get-ScheduledTaskInfo -TaskName PolarisCommerceLocalWorker
Get-Content D:\Polaris\local-worker\logs\supervisor-*.log -Tail 40
```

卸载计划任务不会删除 Profile、env 或日志：

```powershell
.\tools\install_local_worker_task.ps1 -Action Uninstall
```

## 在本机完成账号登录

当前灰度阶段可以先在本机直接打开可见官方登录窗口，账号 profile 会写入 `POLARIS_WORKER_PROFILE_ROOT\accounts\<account_id>`：

```powershell
.\.venv\Scripts\python.exe -m app.ecommerce_local_worker `
  --login-account-id eac_xxx `
  --login-platform taobao `
  --login-url "https://login.taobao.com/"
```

后台账号可绑定已注册的 Worker 后，从任意电脑调用 `POST /api/ecommerce/login-jobs` 创建远程登录任务。Worker 会通过 `.../login-jobs/claim` 领取任务，在本机 Profile 中打开官方登录页，并通过 `.../frames` 上传短期二维码帧；网站使用 `GET /api/ecommerce/login-jobs/{id}` 或 `/api/ecommerce/session/preview?session_id=...` 展示状态和临时帧。扫码完成后 Worker 只回传 `ready` 状态，Cookie、密码、OTP、Profile 和 CDP 凭证始终留在本机。目标 Worker 离线时 API 返回 `LOCAL_WORKER_OFFLINE`，不会回退到服务器浏览器。

## 安全边界

- 远程控制面地址必须使用 HTTPS；仅允许 `localhost`/`127.0.0.1` 使用 HTTP 做本地测试。
- 不要把 profile 根目录放入仓库或同步盘。
- 不要在 `request_payload`、任务结果或自定义 adapter 中放入 Cookie、密码、验证码、localStorage、profile 路径或 CDP 信息。
- 登录、风控、验证码和浏览器崩溃会作为安全任务错误回报，不能伪装成成功结果。
