# CHANGELOG — 对标二创·快版 API版

## 1.0.0

首发版本，**默认走 api 路由**，供网站 / 服务端调用。母本 `aurora-ecommerce-reference-remix` 的简化版：
把「计划先审 + 三份 JSON + 逐维审计」整段砍掉，把「产品几何锁 + 身份约束 + 归一化坐标 + 文案白名单」
整段保留，目标 5 分钟内出一张同版式图。

- **默认 api 路由。** 出图走 `scripts/render_image.py` 调 OpenAI 兼容网关，不依赖宿主内置生图工具——
  网站 / 服务端宿主没有内置工具。网关由调用方提供（`--base-url` / `--api-key` / `--config` /
  `$IMAGE_PROVIDER_*`），**本包不含任何密钥**，`provider-config.json` 是占位模板且不会被隐式使用。
- **减法。** 无状态机、无 `input/plan/plan-audit` 三份 JSON、无计划先审、无 10+6 维逐项审计、无 token 返修额度。
- **保留。** 产品几何锁四段（轮廓/比例/结构/部件关系）、身份约束含反向排除、9 字段参考观察内联进提示词、
  归一化坐标、`APPROVED VISIBLE COPY ONLY` 文案白名单、4 项快检、逐字节复制交付。
- **自包含。** SKILL.md 内联提示词模板与全部判据，**不读母本任何文件**——这是省掉十几分钟的关键。
- **备料 / 快检工具。** `scripts/fastprep.py` 提供 `ratio` / `crop` / `board` / `wmcheck` / `zoom` 五条子命令。
- **代价（已写入文档）。** 产品一致性风险高于母本；适用于「同一对标 + 同一产品反复出新图」。
