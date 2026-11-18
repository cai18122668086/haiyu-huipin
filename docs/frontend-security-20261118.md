# 前端依赖安全修复

已于 2026-11-18 13:47（北京时间）发布。应用提交 `d82a5aa3b5963bf9ac26e4e1897d574145acf595`，PR [#18](https://github.com/cai18122668086/haiyu-huipin/pull/18)，前端镜像 `huipin-frontend-security/frontend:d82a5aa3b5963bf9ac26e4e1897d574145acf595`。

原前端安全审计有 22 项依赖发现（1 critical、14 high、7 moderate），使 Frontend quality 失败。升级 React / React DOM / React Server DOM Webpack 至 19.2.9，Next.js / eslint-config-next 至 16.4.6，并更新受影响的 TypeScript ESLint、minimatch 等间接依赖。npm 注册表审计结果为 0 项；原高危审计门槛保持。

[picomatch 公告 GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) 截至修复时没有官方修补版本。采用明确标注的本地 MIT 分支，保留原版本及来源哈希，针对默认配置下会生成嵌套无界重复的 wildcard extglob 使用现有字面量回退。普通文件 glob 和安全固定 extglob 保持；含不受限通配的重复 extglob 则有意作为字面量处理。此补丁不宣称任意原始正则或显式关闭防护选项的安全性。详见 [vendor 说明](../vendor/README.md)。

依赖锁定到本仓库不可变提交中的两份 tarball；Docker 构建先校验 SHA-512 并验证离线解包，再执行 npm ci。审计门禁验证已安装来源哈希、实际 micromatch / tinyglobby / Vite 消费者、恶意输入子进程时限，以及真实上游版本的公告。仅在这些检查成功后认可准确匹配的本地已修补公告；新公告或公告范围、标题、级别变化会失败。

验证：全新 npm ci、10 项依赖安全自检、272 项完整前端回归、lint、typecheck、生产构建、115 项部署检查及源码 preflight 通过。[最终应用提交 CI](https://github.com/cai18122668086/haiyu-huipin/actions/runs/44244122173) 的 14 项检查全部成功。服务器实际候选镜像再次通过审计、安全自检、272 项前端回归（0 失败、0 跳过）、7 条路由与 402 份源码校验。Chromium 及 63 份仓库测试素材只存在于隔离测试环境，生产镜像保留 Node 24.22.1。

隔离测试浏览器使用固定摘要的微软官方 Playwright Python 1.64.1 Noble 镜像，复制候选镜像中的应用和相同 Node 基础镜像中的运行时；构建无需浏览器下载。服务器 SSH 转发策略阻止本机浏览器直连候选代理，因此人工浏览器验收使用源码清单一致的本机生产构建，检查首页视觉、效果切换、工具跳转和帮助搜索；正式发布后复核公网首页、工具与帮助页。

仅切换 frontend.image；304 份业务页面与组件源码保持。上线后 7 条公开路由、6 份原图哈希及四个应用服务健康检查通过。后端继续使用先前的规划重试修复 `8c4537e09656dd7d9202b44129889aac9d5c4af6`；其他服务容器、运行环境、启动参数、挂载、资源预算和受保护配置保持。没有数据库迁移。

发布目录 `/srv/huipin-frontend-security-20261118-d82a5aa3b596`。旧前端镜像及私有回滚配置已保留，激活脚本在校验失败时自动恢复原镜像。构建、测试、CI、激活、健康及浏览器回执见 [verification](../verification/frontend-security-20261118/)。
