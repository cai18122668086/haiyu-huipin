# Polaris Commerce Studio 生产认证 Phase 1 交接

> 交接日期：2026 年 8 月 10 日（星期一）
> 仓库：`D:\Projects\polaris-commerce-studio-frontend-optimization`
> 分支：`codex/login-system`
> 当前 HEAD：`37001fbb1db09fa821713c7cd8d05ce8beb7b009`
> 目标分支：`main`
> 本文档只做规划，不授权 push、PR、合并或生产操作

---

## 1. 下一步

下一步只做 **Phase 1：推送认证分支并进入代码审查**。

顺序：

1. 复核当前分支、HEAD 和工作树；
2. 经用户授权后 push `codex/login-system`；
3. 确认远端分支 SHA 与本地一致；
4. 创建面向 `main` 的 PR；
5. 等待 CI 和 review；
6. 只修复明确阻断问题；
7. 完成后停下，等待合并授权。

本阶段不做 SMTP/DNS、生产部署、SSH、容器操作或历史 Workspace Owner 分配。

---

## 2. 当前状态

已完成三个本地提交：

```text
6fb846b feat(auth): add SuperTokens login and onboarding
77335b7 feat(deploy): add production auth stack preflight
37001fb docs(auth): add production handoff and deployment guide
```

当前完整 HEAD：

```text
37001fbb1db09fa821713c7cd8d05ce8beb7b009
```

当前事实：

- 分支尚未 push；
- 尚未创建 PR；
- staged 文件为 0；
- 应用层认证和 production 认证基础设施已完成；
- Phase 0 安全审核和提交准备已完成；
- production build 已在 Node v24.14.0 下通过；
- 没有进行生产部署。

工作树中有一个明确排除的 Billing 文档：

```text
D:\Projects\polaris-commerce-studio-frontend-optimization\docs\handoff-auth-domain-billing-production-consolidated-2026-08-08.md
```

不得把它加入认证 PR。

---

## 3. 已冻结范围

认证提交只涉及：

- SuperTokens 邮箱密码认证；
- 邮箱验证和密码重置；
- Secure Cookie Session 和 session refresh；
- JIT 本地用户和个人 Workspace；
- 前端认证页面和 AuthBoundary；
- 历史 Workspace Owner 显式分配脚本；
- production Compose、Caddy、env 生成器和 preflight；
- 对应测试、OpenAPI、生成类型和部署文档。

确认没有修改：

- `backend/skill_sources/**`；
- 支付和 Billing 实现；
- Provider 行为或配置语义；
- 生图能力；
- Amazon 协议能力。

已确认：

- External 模式忽略 `X-User-Id`；
- Token 不进入浏览器 storage；
- API client 使用初始化后的 `globalThis.fetch`；
- `returnTo` 不产生开放重定向或 `/auth/*` 循环；
- 未验证邮箱不能访问业务功能；
- JIT onboarding 幂等并处理并发冲突；
- Owner 脚本要求明确 email 和 Workspace ID，且只修改目标 membership；
- backend Dockerfile 只额外打包 Owner 脚本；
- production/internal 共享卷边界不变；
- 没有真实 Secret 或 populated production env 进入提交。

除非代码发生变化，不要重复完整 Phase 0 审计。

---

## 4. Push 前最小检查

先阅读：

1. `D:\Projects\polaris-commerce-studio-frontend-optimization\docs\handoff-production-auth-phase-1-2026-08-10.md`
2. `D:\Projects\polaris-commerce-studio-frontend-optimization\docs\handoff-production-auth-next-steps-2026-08-08.md`
3. `D:\Projects\polaris-commerce-studio-frontend-optimization\docs\production-auth-deployment-2026-08-08.md`
4. `D:\Projects\polaris-commerce-studio-frontend-optimization\deploy\README.md`

运行：

```powershell
Set-Location D:\Projects\polaris-commerce-studio-frontend-optimization

git branch --show-current
git rev-parse HEAD
git status --short
git log --oneline --decorate -4
git diff --check ac9bfb8192f190b79d94bf1a28c7d9ef47c64163..HEAD
git diff --name-status ac9bfb8192f190b79d94bf1a28c7d9ef47c64163..HEAD
```

预期：

- 分支为 `codex/login-system`；
- 当前认证 HEAD 为 `37001fbb1db09fa821713c7cd8d05ce8beb7b009`；
- 如果本文档后来被提交，记录新的 HEAD；
- 没有 staged 文件；
- Billing 汇总文档没有进入 commit；
- diff 不包含真实 env、Secret 或范围外能力。

代码不变时不重复完整测试，由远端 CI 复核。review 要求修改代码时，只重跑受影响的最小 Gate。

---

## 5. Node 要求

仓库要求 Node `>=22.13.0`。

本机 Codex bundled Node 为 v24.14.0：

```text
C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

需要本机前端验证时临时使用：

```powershell
$env:PATH = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;$env:PATH"
node --version
```

不得降低 engine 要求，也不得把本机 Codex 路径写进仓库或部署配置。

---

## 6. Push 和 PR

以下命令只有在用户明确授权后才能执行：

```powershell
git push -u origin codex/login-system
```

push 后验证：

```powershell
git rev-parse HEAD
git ls-remote --heads origin codex/login-system
git status -sb
```

本地和远端 SHA 必须一致。push 被拒绝时停止，不 force push。

PR 建议标题：

```text
feat(auth): add production SuperTokens login system
```

PR 描述应包含：

- 功能：SuperTokens、邮箱验证、密码重置、Secure Cookie Session、JIT onboarding、前端认证和 production auth infrastructure；
- 安全：External 模式不信任 `X-User-Id`、不存储 Token、未验证邮箱阻断、`returnTo` 安全、Owner 脚本限定目标；
- 非范围：支付/Billing、Provider、生图、Amazon、`backend/skill_sources/**` 和生产部署；
- 验证：backend 633 passed/32 skipped、定向测试 26 passed、deploy 68 passed、前端策略 3 passed、TypeScript/ESLint/build/OpenAPI/Secret scan 通过。

已知非阻断项：

- 全仓 Mypy 有 360 个历史基线错误，总数未增加；
- 默认 Ruff 受共享 Git exclude 无效 glob 影响，显式 `--no-respect-gitignore` 已通过；
- 本机缺少 Docker/Caddy CLI，原生配置验证留到具备工具的部署环境。

---

## 7. Review 处理原则

先读相邻实现和测试，匹配项目现有风格，只做最小修复。

允许处理：

- 认证安全缺陷；
- 测试或 OpenAPI 不一致；
- deployment preflight 明确漏检；
- CI 中与 Node engine 直接相关的问题；
- 明确文档错误。

不要顺便处理：

- 支付或 Billing；
- Provider、生图或 Amazon 功能；
- UI 重构；
- 全仓 Mypy 清理；
- 无关依赖升级；
- 仅为未来需求增加抽象；
- 实际不会发生的错误分支。

修改后创建正常修复 commit，不重写已经进入 review 的历史。

---

## 8. 完成 Gate

Phase 1 完成条件：

- [ ] 用户已授权 push；
- [ ] 分支已推送且远端 SHA 与本地一致；
- [ ] PR 已创建并指向 `main`；
- [ ] PR 未包含 Billing 汇总文档；
- [ ] CI 通过，或基线例外已明确记录；
- [ ] review 无未解决阻断问题；
- [ ] 尚未执行生产部署、SSH、容器操作或 Owner 分配。

达到以上条件后停止，等待用户决定是否合并。

合并后记录 `origin/main` 上新的精确 40 位 SHA。生产 release 只能从该远端可达 SHA 构建。

---

## 9. 后续阶段

Phase 1 完成后，才进入 SMTP/DNS、production Secret、不可变 release、备份恢复演练和生产切换。

具体步骤不在本文重复，继续使用：

- `D:\Projects\polaris-commerce-studio-frontend-optimization\docs\production-auth-deployment-2026-08-08.md`
- `D:\Projects\polaris-commerce-studio-frontend-optimization\deploy\README.md`

历史 Workspace Owner 分配必须最后单独授权，并明确 email 和 Workspace ID。

---

## 10. 下一任务提示词

```text
继续处理仓库：
D:\Projects\polaris-commerce-studio-frontend-optimization

当前分支：codex/login-system

开始前阅读：
1. docs/handoff-production-auth-phase-1-2026-08-10.md
2. docs/handoff-production-auth-next-steps-2026-08-08.md
3. docs/production-auth-deployment-2026-08-08.md
4. deploy/README.md

本任务只做 Phase 1 远端集成，不进行生产部署。

先复核分支、HEAD、工作树和提交范围。在得到明确授权后，推送 codex/login-system，验证远端 SHA，创建面向 main 的 PR，检查 CI，并只修复明确阻断问题。

不要合并；完成后停下来等待授权。

禁止 SSH、生产部署、容器操作、Owner 分配、支付/Billing 接入、Provider/生图/Amazon 语义修改，以及 backend/skill_sources/** 修改。
```

---

## 11. 当前停点

截至 2026 年 8 月 10 日：

- 认证代码和 production 基础设施已完成并提交；
- Phase 0 已完成；
- 当前分支尚未 push；
- PR 尚未创建；
- 没有执行生产操作；
- Billing 汇总文档仍被排除。

**等待用户明确授权 push 和创建 PR。**
