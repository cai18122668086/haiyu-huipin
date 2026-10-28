# 本机 Worker 独立运行环境

开发仓库用于编辑、切分支和测试。运行目录是固定版本的发布产物，包含单独的 Python 环境、源码清单和依赖锁。账号 Profile、设备身份和令牌放在单独的机器状态目录，均不进入 Git 或迁移包。

运行目录不能直接用于开发。更新运行版本需先排空旧 Worker，保留旧版本和状态路径，再启动明确的新版本；拉取 GitHub main 不会自动更新运行目录。

## 制作版本

使用经过审查的 Git SHA；安装器拒绝覆盖已存在的目标目录。`--preserve-working-bytes` 会额外核验现有文件的规范化内容等于该 SHA，然后保存原字节。依赖从已验证环境复制为独立文件，移除开发仓库的 editable 注册，固定各包版本并保存逐文件 SHA-256。不会启动 Worker。

```powershell
python tools/local_worker_runtime/build_runtime.py `
  --source-repo D:\Projects\polaris-commerce-studio-model-pose-entrypoints `
  --source-ref <reviewed-sha> `
  --seed-env D:\Projects\polaris-commerce-studio-model-pose-entrypoints\backend\.venv `
  --destination D:\Projects\polaris-local-worker-runtime `
  --portable-bundle D:\Documents\ChatGPT\worker-source-runtime.zip
```

发布清单同时记录应用源码 SHA、生命周期工具提交、Python 构建版本、依赖版本/文件哈希、运行文件哈希和机器状态目录。已有机器的实际运行版本应单独取证，不能只看仓库 HEAD 或虚拟环境名称。

## 启动与停止

先把本机配置放在独立状态目录。可参考仓库 `tools/local-worker.env.example.ps1`；实际设备身份、令牌和 Profile 不提交 Git。

```powershell
# 只更新已停止任务的启动动作；拒绝停止运行中的任务，也不会自动启动。
& D:\Projects\polaris-local-worker-runtime\bin\install_task.ps1 `
  -RuntimeRoot D:\Projects\polaris-local-worker-runtime `
  -EnvFile D:\Polaris\local-worker\worker.env.ps1 `
  -WorkerRoot D:\Polaris\local-worker

# 请求排空：停止提交新槽位，等待现有工作结束，期间保留已有任务的续租。
python D:\Projects\polaris-local-worker-runtime\bin\control_runtime.py drain `
  --worker-root D:\Polaris\local-worker --timeout 600
```

排空超时会保留进程和停止请求，不强杀任务。`runtime-control/status.json` 的 `stopped` 表示主循环已完成排空；进程与监督任务退出后才能切版本。停止请求会跨监督器重启保留。恢复时先确认旧 Worker/监督任务已退出，再移除这份明确的 `stop.request`，随后启动计划任务。不要直接执行旧安装脚本强停运行任务。

每个状态目录同时有监督器和 Worker 的跨进程锁，防止重启窗口或手动启动形成第二个消费者。状态文件只记录生命周期与心跳摘要，不记录令牌、Cookie 或任务内容。

## 换电脑与同事协作

1. 同事从 main 的明确提交建功能分支，用独立测试服务和测试任务开发。生产 Worker 不从该工作目录加载代码。
2. 换电脑时校验迁移包哈希，在新电脑安装清单记录的同版本 Windows x64 Python，再调用包内 `bin/bootstrap_runtime.ps1`，指定包路径、全新目标目录及 Python 路径。它会重新创建环境并按锁安装依赖；不会复制旧电脑虚拟环境的绝对路径。
3. 新设备单独注册，生成自己的设备身份和会话。迁移包不含设备令牌或浏览器登录资料；不得让两台机器共用同一个 Worker 身份同时领取任务。
4. 检查本机配置、浏览器冷启动和测试任务后，再安排旧设备排空、新设备上线。Profile 如确需迁移，走单独授权和安全传输，不把它放进代码仓库。

`bootstrap_runtime.ps1` 安装依赖需要正常的软件源。它验证源码清单；运行机器应另存实际安装依赖清单，新电脑安装结果需重新验收。

## 验证

```powershell
python -m unittest discover -s tools/local_worker_runtime -p test_runtime.py -v
```

测试只使用临时目录、假任务和回环 API，覆盖单/双槽排空、续租、重复实例、源码清单和完整主机启动/停止。旧监督器的一次性交接方式应在同样的进程结构中单独演练；测试通过前不能对运行实例发送控制台中断。正式切换和服务器部署是两个独立操作。
