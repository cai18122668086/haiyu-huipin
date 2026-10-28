# Polaris 代码基线整理结果（2026-10-17）

> 后续整理更新：主工作目录已切回干净 main（开发起点 `0e4a5b0`），本机 Worker 与开发目录已分开。运行环境工具及协作说明见 [PR #75](https://github.com/cai18122668086/polaris-commerce-studio/pull/75)；用户已批准本轮工具、测试与文档接入 main，服务器部署仍单独审批。设备授权与运行验收证据仅本机保留。下文第 1–7 节是整理过程快照，包含已完成的待办；当前目录与版本关系以 [发布记录](release-ledger.md) 为准。网站生产服务未重新部署，详情业务修复尚未开始。

> 合并后更新：[PR #73](https://github.com/cai18122668086/polaris-commerce-studio/pull/73) 已合入 main，集成提交 `ad9f588a7c75124c6570063224968324898e120a`。固定候选 `bef9866` 的六项 CI 全部成功，集成提交与候选的树相同。未执行生产发布。下文保留合并前的整理、审查与授权快照；其中“候选待审/待合并”已由本条更新，现行主线见 [发布记录](release-ledger.md)。

本轮授权：只读核验、完整保留、本地分支整理、候选基线验证；正式 main 合并、运行中 Worker 切换和生产发布分别等待用户确认。国内详情业务修复尚未开始。

## 1 当前结论与未完成边界

- 主目录 `D:\Projects\polaris-commerce-studio-model-pose-entrypoints` 已从 85 个已跟踪修改、154 个未跟踪文件整理为干净 Git 状态；业务源码字节未改变。
- 主目录分支为 `codex/workspace-preserved-20261017`，用途是过渡归档，不能作为后续开发或发布基线。有效工作已经单独留在 9 条保留分支。
- 干净开发候选位于 `D:\Codex\worktrees\polaris-generation-recovery`，分支 `codex/main-reconciliation-20261017`。后续开发使用此候选的已批准版本。
- 主目录正在支撑本机电商 Worker 的启动环境；相对导入边界包括 config/domain/models，切换候选会改变这三个依赖。未在运行中替换、停止或重启它。完成受控运行目录迁移后，才能把主目录切到获批 main。
- GitHub 默认分支已是 main；本轮准备的是指向 main 的审查候选，尚未合并。生产各服务没有更新。

“Git 状态干净”只表示内容已归档，不代表主目录内所有历史功能都已验收，也不代表工作目录迁到 main 的最后一步已经完成。

## 2 保存与分类

原始 239 个差异文件共 12,575,597 字节，保存为 `D:\Documents\ChatGPT\polaris-baseline-20261017\workspace-delta-original.zip`（7,922,869 字节），压缩包及逐文件 SHA-256 已验证。原始 HEAD、索引、refs、reflog、补丁、PR 与远端分支快照均已保存。

另有 415 个原有忽略源码、配置或排查文件进入仅本机保留的 `ignored-source-and-config-original.zip`。该包含配置，禁止上传 GitHub。依赖环境、运行数据、用户素材、浏览器资料和大型缓存留在原处，未当成无用文件删除。

239 项中，93 项与生产提交 812a1f7 的 Git 内容一致；其余 146 项不能按“未提交”判定是否有效。其中 5 项调试图片/方案已原样移动到证据目录 quarantine，其他 141 项按用途保留，验收状态保守标注为未确认。没有声称已逐项确定作者或商业有效性。

| 保存组 | 文件数 | 独立保留分支 | 保存提交 |
|---|---:|---|---|
| `deployed-content` | 93 | `codex/preserve-deployed-content-20261017` | `1fa0186` |
| `detail-contract-wip` | 3 | `codex/preserve-detail-contract-wip-20261017` | `7c9c88a` |
| `replica-reference-wip` | 68 | `codex/preserve-replica-reference-wip-20261017` | `84a9eb2` |
| `main-image-wip` | 6 | `codex/preserve-main-image-wip-20261017` | `46eb7dd` |
| `image-providers-wip` | 8 | `codex/preserve-image-providers-wip-20261017` | `2d2bd07` |
| `shared-contracts-wip` | 28 | `codex/preserve-shared-contracts-wip-20261017` | `90929e2` |
| `frontend-wip` | 12 | `codex/preserve-frontend-wip-20261017` | `2c85927` |
| `deployment-wip` | 14 | `codex/preserve-deployment-wip-20261017` | `88e7dd4` |
| `handoff-original` | 2 | `codex/preserve-handoff-original-20261017` | `40a3153` |

每条保留分支都从原始 2ef3c96 建立，只含该组文件差异；它们是来源片段，可能依赖其他组，不是可直接运行或整支合并的功能版本。主目录另有按相同分组形成的 9 个归档提交，便于完整恢复原目录。Git 连通性检查还发现 29 个历史游离提交、103 棵树和 9 个 blob，已通过仅本地 refs/archive/polaris-local-20261017 引用防止将来被回收；归属待确认，没有上传或执行 GC。

已删本地旧分支：`codex/release-detail-v43`（492a0be）、`codex/fix-set-audit-derived-verdict`（91387f7）。二者均被候选历史完整包含，远端引用和原始 refs 清单保留；没有强推或改写提交。

待确认内容逐项见证据 `file-classification-final.json`；主要是复刻/参考图接入、详情契约实验、公共执行与配额改动、前端展示、部署策略。后续按实际差异移植，不能把旧文件整体盖到更新的生产基线。

## 3 本地、GitHub 与生产关系

GitHub main 为 `d19fd33a41311e34370978ccfafdcf37a9453eb9`。它相对生产 812a1f7 有 1 个独有合并提交；生产有 40 个未进入 main 的提交。共同祖先为 `c869435`，main 的那个合并提交没有带来额外的生产缺失内容。

`git merge-tree` 无冲突合并两条历史，结果树与 812a1f7 完全相同。候选提交 `7c7fd93` 保留 main 与生产两条父链，没有复制整份脏目录、重写历史或修改 main。

在其后单独补齐两个可审查的打包记录问题：`8dd26c4` 保存原目录已有且与旧 manifest 锁一致的 rpds 二进制（原 `.gitignore` 的 `*.py[cod]` 连 `.pyd` 也忽略）；`b894030` 把三个清单哈希对齐到早已提交且在线上存在的源码。三个源码文件及 active sequential_v20 源码没有改变，校验器没有放宽，gitignore 没有新增规则。这两个提交尚未发布。

main 到生产的差异为 238 个文件，并非可以免审的小修补。50 条删除路径包括旧审核/传输模块、测试和首页资产；5 个旧后端模块的删除来自 `6b27a55` 的 sequential_v20 切换。清单保存在证据 `main-to-production-name-status.txt`。这些是已发生的生产历史，保留来源和待审范围，不能以主图当前正常推定所有删除均已充分验收。

仍打开的历史 PR：#3、#4、#69、#70、#72。#71 已合并到发布支线。#72 的目标仍是 `codex/aurora-stability-fix`。本轮不擅自关闭、重定向或合并这些 PR；统一到 main 的候选独立审查，原链保留作来源证据。

## 4 新鲜生产取证

取证时间 `2026-10-17T12:04:50.180706+08:00`，仅通过 SSH 读取容器、文件哈希和只读数据库事务。

| 服务 | 镜像标签 | 对应源码 | 核对范围 |
|---|---|---|---|
| API | polaris/api:generation-recovery-812a1f7 | 812a1f7 | 562 个源码/Skill/迁移等文件；应用 Python 76 个 |
| Worker / Dispatcher | polaris/backend:generation-recovery-812a1f7 | 812a1f7 | 每个 563 个文件；应用 Python 各 76 个 |
| Frontend | polaris/frontend:generation-recovery-812a1f7 | 812a1f7 | 439 个保留的源码/资产/配置文件 |
| Crawl worker / maintenance | polaris/crawl-worker:c5eb50eb48a57cb43987f9e03fa8938574316f24 | c5eb50e | 每个 562 个文件 |

已核对的文件没有语义差异；非 Skill 文本存在 CRLF/LF 差异，原始与规范化哈希分别保留。Skill 逐字节核对。预期目录中的文件完整性也已检查。API/爬虫镜像没有保留 requirements.lock，不能把它写成已校验该锁文件；其实际安装依赖另有清单。Worker 和 API 存在 28 项依赖版本/安装项差异，如 FastAPI、SQLAlchemy、Starlette，说明同源码版本不等于同运行环境。

未发现应用源码覆盖挂载，`docker diff` 没有应用/Skill 源码变化。/app 下挂载是 Aurora 工作数据和电商资料卷。镜像 ID、挂载路径、Compose 路径/哈希、配置白名单和实际依赖清单写入 [生产版本记录](releases/2026-10-17-observed-production.json)。没有修改生产环境变量、任务、资产、队列或服务。

数据库只读结果：Alembic 为 `20260930_0025`，`jobs.stage`、`job_items.stage`、`crawl_tasks.stage` 实际均为 varchar(64)。提交 `b8cd247` 说明生产先执行过扩列 DDL，但该提交只改模型。原发布提交缺少版本化迁移。候选已在独立提交 60784da 补入 20261017_0026：先检查三个字段，再扩 32 至 64；已有 64 不重复改动，未知类型在任何 DDL 前拒绝，数据库自动缩列回滚也被拒绝。Ruff 和离线 PostgreSQL SQL 生成已验证；独立 CI 的四个真实 PostgreSQL 场景验证两种初态、重复升级、未知类型与长状态回滚保护。本轮未改生产 schema。

生产实际使用的桶名含 mock 字样（`polaris-mock-assets`），仍是真实用户资产桶。隔离与否必须检查实际端点、账号、桶和队列，不能依靠名称判断。

## 5 验证与限制

本轮隔离验证使用证据目录下专用 SQLite、内存/inline 队列和本地存储，网络连接被限制为 Windows asyncio 所需的本机临时 socketpair。没有启动生产消费者，也没有调用真实生成服务。

- 123 个不同后端用例最终通过，覆盖恢复、主图、详情契约和审核边界。首轮 119 通过、4 项因稀疏检出遗漏 public/og.png 失败；从已有 Git 对象补回后，这 4 项单独复验通过。
- 12 个 Skill manifest 最终完整验证通过；修正前的失败、缺失依赖和哈希差异证据保留。
- 3 个前端恢复展示用例通过，Node v24.19.0；这不替代 CI 的 Node 22 完整构建。
- D 盘空间不足以安装/构建完整隔离栈，本轮未完成全套 PostgreSQL/Redis/MinIO、本地完整前端构建、真实生成和视觉验收。提交候选后使用 GitHub CI 校验其现有检查，仍须等待实际结果。

测试证据在 `D:\Documents\ChatGPT\polaris-baseline-20261017`。测试通过不代表详情图质量合格，已知 DEFERRED 缺陷仍存在。

## 6 工作树与磁盘整理

8 个在活跃 Git common-dir 注册的工作树已保存状态。若干历史目录的 .git 指针失效；4 个目录呈现“索引全部删除、源文件未跟踪”的异常，无法确认修改意图，未删除或掩盖。`D:\Temp\rel1-wt` 虽然 Git 干净，仍含详情任务产物和 node_modules 连接，不能按干净状态删除。详见证据 `all-local-repositories-and-worktrees.json` 与 `obsolete-worktree-precheck.json`。

经用户追加授权，uv 和 npm 自身清理命令已清掉约 16 MiB 缓存；pip 无可清理内容。批量直接删除其他缓存和旧安装包的命令被自动批准审查拒绝（只返回 blocked by policy），未执行。已提供缓存占用清单，未绕过限制，也未删除项目、用户资料、录音、已安装运行时或恢复证据。

## 7 接下来需确认的具体操作

1. 审查指向 main 的候选与 CI。确认后才合并；合并不触发本轮生产更新。候选含既有生产历史与两项打包完整性修正，国内详情业务修复另起分支。
2. 将本机 Worker 的代码/解释器启动路径迁到固定运行目录，保留原浏览器资料、设备标识和令牌位置，验证无重复消费者与任务交接后切换。当前启动脚本为主目录 tools/run_local_worker.ps1，私有环境脚本为 D:\Polaris\local-worker\worker.env.ps1，API origin 已核实为生产站点。这两者的哈希保存在证据 local-worker-runtime-reference.json。该操作会调整正在工作的本机运行组件，须单独批准；主目录随后切到获批 main。
3. 完成基线整理并展示 [国内详情修复方案](domestic-detail-repair-plan-2026-10-17.md) 后，按步骤开始业务修复。生产发布另行准备候选镜像、真实隔离证据、兼容与回滚方案，等待再次批准。

恢复依据：原始 raw ZIP + 逐文件哈希 + 原始 2ef3c96 历史 + 原始索引/refs + 独立保留分支。恢复到新临时工作树验证，禁止直接覆盖当前生产依赖目录。生产未改变，因此本轮无需执行生产回滚；未来回滚须固定当前镜像 ID 和现存工作数据/schema，不可把“回退到 32 字符字段”当作代码回滚的一部分。
