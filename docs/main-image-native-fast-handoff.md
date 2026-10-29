# 国内主图：原生精简 B

发布更新：2026-10-30已随PR #1统一合并部署，实际状态与备份见[统一发布记录](release-suite-fast-20261030.md)。下文保留开发验收时的历史记录。

2026-10-29。根据用户新提供的《国内电商主图六图生成Skill-v2.0-最新版-20260820.zip》
精简执行流程并新增独立快速入口，原版仍默认。按用户最终选择，仅保留B，A实现已删除。
本轮统一发布还包括详情和亚马逊快速版，部署结果见verification/suite-fast-20261029。

## 原生编译策略

B（`native_fast`）只用一次原版身份检查，随后使用 ZIP 内原生本地编译器产生
Campaign Style、六图职责、显示文案、构图、视觉表现、证据、搜索封面蓝图和完整提示词。
网站直接发送原生编译器的完整生图提示词。

用户ZIP SHA-256为
`0c5d3daa3889d0ff5964114996a49c428dce9b200b3591d9b690991170869d3c`。
ZIP中24文件与原有 bundled v20逐文件字节完全相同。新副本为
`aurora-domestic-ecommerce-main-image-native-fast`；compiler.py、style.py、identity.py及
全部参考资料保留原字节，共19个未修改文件。只修改SKILL.md、harness.py、core.py、
session.py和session schema，以删除执行门槛。来源证明保存在
`backend/source_manifests/aurora-domestic-ecommerce-main-image-native-fast-source.json`。
ZIP中的旧审计要求属于被精简的原始资料，不替代用户此次明确要求。

相同raw input与identity下，B的完整plan、Campaign Constitution、六条prompt与
原版生成前编译结果逐字节一致。原版前两张校准会重编后四张，因此不承诺B与原版
完成校准后的最终画面一致，也没有声称质量等价。

## 删除的流程与保留的约束

B删除逐图AI审计、自动视觉返工、第一张搜索校准、第二张风格校准、整套AI审计和
第二次最终人工审批。对应7个旧CLI审计/校准/修订/重启命令返回BLOCKED，不假装执行通过。

保留 `start → lock-identity → compile → authorize → generate-next` 原生入口。
六个独立页面会话共享冻结方案，各自设置真实active_page_index，不伪造其他页已通过或
style_calibrated=true。网站使用最多6张滚动并发；逐张来源顺序、edit target及完整prompt
在授权时再次与原生generation_request核对。实际图片请求直接发送原生prompt原字节。

返回原图经正常网站素材保存后，controller读取六个真实文件，按已发行token执行6次
`record-generation`，合并真实版本，再执行精简后的 `finalize`。状态为
`GENERATED_NOT_REVIEWED` / `FINALIZED`；manifest明确NOT_REVIEWED、set_audit_performed=false，
不含set_audit_passed、虚构PASS或final_user_approval。部分失败不生成完整manifest。
技术闭环无模型请求，终态重复执行不再绑定或写入。Windows技术文件采用短文件名，
避免深层工作区加64位SHA文件名超过路径限制。

保留源素材归属与SHA、独立Skill/schema/version、方案及完整prompt哈希、支付前持久回执、
未知结果不重发、成功页保留、失败项重试、clone新调用身份、原图复用、积分、额度和下载。
省略/standard仍不写速度字段，旧幂等身份不变。
三种选择均有草稿/历史恢复，B沿用原版对象身份和完整六行方案显示。

B继续请求1024×1024 / high，使用既有渠道模型，不降模型或画质。
保留供应商原字节、不放大、补白或重编码。B保留原生Harness严格正方形且短边至少1024的
要求，在provider发布前即拒绝非精确方图，避免交付后卡在原生技术收尾。
身份profile独立为 `aurora_native_fast_identity`，仍使用原版身份提示与schema；单路一次，
长度截断不能启动生图。原系统允许的profile01_semantics_read:false只作为运行时标记剔除，
true或其他非法字段仍拒绝。

## 真实对比样本

独立本地SQLite/storage，通过网站上传、建任务、outbox、provider、签名下载与ZIP链路运行。
使用用户提供的3张身体乳原图及原始卖点，未连接生产任务。

- B：1次身份检查24.468秒，input2099/output3381/total5480 tokens，其中reasoning2790。
- 原生本地编译及冻结六份请求约1.9秒，6次生图提交跨度0.078秒。
- 首图169.312秒返回、169.937秒可下载；整套259.422秒（4分19秒）。
- 最后一张单次生图231.543秒，长尾来自同一个上游图像请求，没有审计/返工等待。
- 6张原生1254×1254，签名下载6/6 HTTP200；ZIP HTTP200、6成员CRC和原图SHA全部相同。
- 网站DB冻结prompt、私有冻结文件、实际gateway prompt，6/6逐字节一致。
- 来源tengli / image-2.5-flare / tengli-configured-v1；身份请求沿用deepseek-v4.1-flash-1017。
- API计数7，正常90积分结算、预留清零，真实上游费用null；没有第二轮付费、AI审计或重绘。

A此前总耗时248.36秒，B本次259.422秒。B规划阶段明显短，但本次上游图片长尾较大，
不能声称整套更快。A与B的实际提示词不同，这是两种工作流样本比较，不是纯并发收益实验。

首轮真实付费运行时，网站六图已完整交付，但原生内部会话尚未登记返回图，仍为AUTHORIZED。
补齐技术闭环后，用真实数据库的只读备份通过当前正常网站runner恢复，零模型/外部网络调用，
六条真实record动作和finalize完成；第二次重放完全不变。原数据库、storage、原图与账单未改。
此次恢复没有重新生成图片，因此259.422秒是首轮六图可下载的观测值，不包含后来执行的
零调用技术登记耗时。证据明确区分这两个阶段。

人工观察：B更接近原生常见商业主图表达，白金色大商品、花材、乳霜和使用人物场景统一。
同时六图重复“新品上市/7天美白”等文案较多；第1张出现来自补充素材的管装和袋装，
容易被理解为套装；瓶身标签、包装色条和细小印刷仍有变化。没有人工确认每图身份与事实均
完全正确，不把技术交付称为语义审核通过。保留这些原始结果供用户自己比较，没有审美重绘。

可提交的脱敏结果在 `verification/main-image-native-fast-20261029/`。
输入、完整prompt、回执、DB与图片只在忽略目录
`backend/.data/main-image-native-fast-local/`。
另导出 `outputs/国内主图Skill-v2.0-原生精简B.zip`，含修改后的24文件、独立manifest和来源证明，
便于用户单独保存；不包含用户图片、环境文件或凭据。

## 验证与集成

专属运行时覆盖原生提示词字节一致、无审计完成六图、token/低清/非方/少页/SHA/重复调用
阻断及7条禁用命令。API覆盖自动/手动授权、六图下载和ZIP、正常积分、字段隐藏、A/B隔离、
失败页重试、clone、未知结果、六槽共享、原生技术闭环、终态零调用和身份标记兼容。
transport覆盖合法JSON但length终止的拒绝、预算、原模型和单渠道；provider还覆盖
B严格方图门槛与A既有容差相互独立。

分批回归共343个不同后端用例通过：A/B相关108项（B新增59项，A既有49项）及
原版主图/自动提交/执行可靠性/Skill/图片provider/额度/复刻快速235项。新增方图边界后
再次运行A/B provider33项全部通过。前端辅助逻辑4项、TypeScript、生产构建、定向ESLint、
Ruff和6个后端模块Mypy通过，OpenAPI与生成类型同步。
技术闭环首轮暴露Windows过长SHA临时文件名导致5项失败；改短文件名后native workflow
13/13复跑通过，随后扩展身份字段/闭环与传输测试继续通过。没有隐藏这次修复过程。

本轮以main `b6b91b8`（已含原版修复和正式发布记录）为基础，最终运行树移除A。
共享文件小幅接线包括：aurora_main_image_fast.py/router、fast_provider的variant分支、
原planner授权分支、execution原生技术收尾、routes的clone/retry清理、schema速度枚举、
services公共投影、Skill注册、quota识别和xinghe_agent身份profile；没有改原版v20 Skill
；A的Skill、manifest及模板已移除。

本分支只提交和推送到私有仓库，未推送main或自行部署。统一发布前需再次fetch并核对线上
镜像、支付、队列及共享容量。真实验证入口：
`backend/scripts/verify_main_image_fast_local.py --variant native_fast --run`，默认独立B目录，
已有run目录拒绝再次付费，去掉--run只输出安全说明。


## 发布选择

按用户要求仅保留上一轮B的原生编译、提示词和完整Skill，移除A的规划模板、Skill与入口。后续实验和复测不纳入发布。
