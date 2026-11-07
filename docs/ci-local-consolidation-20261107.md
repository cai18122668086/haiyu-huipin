# 本地修改整理与 CI 重试修复

CI #67 的 Frontend quality、API contract 和三个容器构建均成功；Backend shard 5 中三个部分交付重试用例失败，套图重试接口将已明确终态失败的 IMAGE_ASYNC_TASK_FAILED 与结果未知混为一类，返回409；通用重试接口不拦截该错误码。现由两个入口共用重试策略：精致版逐屏已知失败可显式重试；快速版的已接单异步失败仍要求核查原回执。PROVIDER_OUTCOME_UNKNOWN 与 IMAGE_RESULT_DOWNLOAD_UNCONFIRMED 继续拦截。补充快速版通用入口保护回归。

主工作区改动已保存为 b5f1168 本地快照并合并整理。素材角色指引、示例配对、积分上限及自动提交预算、长提示词无损压缩已有更完整的实现进入主线：保留服务端归属校验与可逆压缩，舍弃草稿中可由客户端设置计费上限、截断提示词正文的重复实现。任务中心 rep_ 记录继续使用现有套图入口，避免错误请求单任务接口。

补齐本地案例验收页面与历史图片档案；预览仅在开发环境 ?preview=platform-cases 时启用，正式首页内容与SEO保持。新增75份素材共69,533,080字节。独立历史备份 sms-login-isolated-20261027 不属于当前维护入口，按 PROJECT-INDEX.txt 保留原状。

本机 D 盘满导致第一次保存快照失败；将两个可重新生成的 dist 目录完整移至 C:/Users/a1726/.codex/workspace-dependencies/huipin-build-archive-20261107 后继续，没有删除代码或原始素材。构建和检查使用C盘整合工作区。
