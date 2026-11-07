# 本地修改整理与 CI 重试修复

CI #67 的 Frontend quality、API contract 和三个容器构建均成功；Backend shard 5 中三个部分交付重试用例失败，套图重试接口将已明确终态失败的 IMAGE_ASYNC_TASK_FAILED 与结果未知混为一类，返回409；通用重试接口不拦截该错误码。现由两个入口共用重试策略：精致版逐屏已知失败可显式重试；快速版的已接单异步失败仍要求核查原回执。PROVIDER_OUTCOME_UNKNOWN 与 IMAGE_RESULT_DOWNLOAD_UNCONFIRMED 继续拦截。补充快速版通用入口保护回归。

主工作区改动已保存为 b5f1168 本地快照并合并整理。素材角色指引、示例配对、积分上限及自动提交预算、长提示词无损压缩已有更完整的实现进入主线：保留服务端归属校验与可逆压缩，舍弃草稿中可由客户端设置计费上限、截断提示词正文的重复实现。任务中心 rep_ 记录继续使用现有套图入口，避免错误请求单任务接口。

补齐本地案例验收页面与历史图片档案；预览仅在开发环境 ?preview=platform-cases 时启用，正式首页内容与SEO保持。新增75份素材共69,533,080字节。独立历史备份 sms-login-isolated-20261027 不属于当前维护入口，按 PROJECT-INDEX.txt 保留原状。

本机 D 盘满导致第一次保存快照失败；将两个可重新生成的 dist 目录完整移至 C:/Users/a1726/.codex/workspace-dependencies/huipin-build-archive-20261107 后继续，没有删除代码或原始素材。构建和检查使用C盘整合工作区。

## 发布与合并结果

2026-11-07 13:20（北京时间）完成发布。PR #13 合并提交为 5cac583b61f8318d8fc6e694004a11093e290e26，部署应用为 cfaf90f34031cd18ee1d8deb93510ee8b2fa3f83，合并后的运行源码与候选完全一致。14项合并前CI全部通过；381项隔离候选回归、258项前端测试和38项重试回归通过。

四个应用服务健康，654份后端和297份前端源码及75份新增图片核对一致，另抽查2份公网新增素材的原始字节哈希通过。迁移0040、活动与运行参数保持，备份4,633,563字节且pg_restore目录校验通过，其他服务没有更换。证据见 verification/ci-local-consolidation-20261107/。

CI：[修复后的完整运行](https://github.com/cai18122668086/haiyu-huipin/actions/runs/42340148097)；合并：[PR #13](https://github.com/cai18122668086/haiyu-huipin/pull/13)。
