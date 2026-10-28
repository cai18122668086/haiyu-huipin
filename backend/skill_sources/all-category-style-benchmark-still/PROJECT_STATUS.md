# Project status — 4.0.1

## 4.0.1 Plan input robustness

The failed `D:\测试4` run reached `plan-target-page` but never created a generation request. Its analysis had a plain-string source-semantic inventory, which previously caused a Python `TypeError`; its plan also flattened `semantic_translation` into `product_facts` and `translation`, so it did not provide one translation object per source semantic. Planning now normalizes permitted string/object inventories and emits an actionable fail-closed error for the flattened shape or missing source IDs.

Validation: 126 tests passed; quick_validate passed; the reproduced run now stops with a clear plan correction message and remains `PRODUCT_FACTS_READY`. No real imagegen was run.

## 4.0.0 Architecture convergence

The workflow now has one active planning path: `PRODUCT_FACTS_READY → PLAN_AUDIT_REQUIRED → GENERATION_ALLOWED`; the unused `TARGET_PAGE_PLAN_READY` state was removed from code, fixture state reporting, schema and documentation. A persisted generation block is also reflected by `next_action`, so the user-facing next step cannot suggest generation after a failed plan audit. Typography precedence is explicit: form bans override reference-font inheritance, while allowed typographic mechanisms remain benchmark-derived. Pair source-step coverage and sanitized-crop identity exclusion remain fail-closed.

Validation: 125 tests passed; quick_validate passed; no real imagegen run.

## 3.9.7 Generation gate and crop binding hardening

The referenced cycling backpack run was rejected as an invalid generation: its `generation-block.json` recorded a plan/order mismatch while another artifact claimed `GENERATION_ALLOWED`; G01 also supplied generic mechanism crops all bound to T01, with no explicit exclusion of benchmark product pixels. The workflow now fails closed on the persisted block, requires explicit non-fixture crop identity exclusion and source-step bindings, and requires every current Pair source step to appear in the request bindings. The native prompt additionally states that benchmark people, scenes, products, brands and identifiable objects remain reference-only.

Validation: 124 tests passed; quick_validate passed; no real imagegen run.
# Project status — 3.9.6

## 3.9.6 Typography form machine gate

The supplied handwritten brush-script example is now a banned typography form. Generation requests carry `forbidden_typography_forms`; an audit that observes `forbidden_typography_form=true` fails the target and uses the existing one-time revision path. The rule changes only the font form and does not reduce required text or reference-matched density.

Validation: 122 tests passed; quick_validate passed; no real imagegen run.

# Project status — 3.9.5

## 3.9.5 Unconfirmed product mark machine gate

The known wordmark contamination issue is now enforced by code. Generation requests must carry `ban_unconfirmed_product_marks=true`; an audit that observes `unconfirmed_product_mark=true` fails the target and permits the existing one-time revision path. This is a general fact-safety guard and preserves the 10-screen, five-Pair, 3:8 detail contract and benchmark-driven ordering.

Validation: 121 tests passed; quick_validate passed; no real imagegen run.

# 项目状态 — 3.9.4

## 3.9.4 Pair 几何、密度上限与产品字样污染修复

户外骑行背包测试暴露出两个通用缺陷：3:8 Pair 被模型误排成左右双栏并在每屏自行扩写多个内容段；产品本体还出现了产品事实未确认的英文 wordmark。生成合同现在明确要求同一整幅宽度的上下两屏、唯一横向接缝，每屏只执行绑定的一个主视觉和 visible_copy，density contract 是硬上限；产品本体禁止未确认的产品名、品牌名、Logo、水印和 wordmark。审查协议同步把这类字样列为污染。

默认详情页仍为 10 屏、5 组 Pair、3:8；本次没有加入测试线程产品事实。

## 3.9.3 单屏对标顺序与方法压平修复

针对骑行背包测试线程，确认问题来自工作流合同而非单纯生图随机性：29 张单屏对标被目标计划每三张合并为一屏，密度只引用每组第一张；每个 Pair 又收到整套 29 张参考裁剪图，没有当前 Pair 的源图顺序和交接。测试分析中的说服任务、证据义务和表达动作还被重复模板化，导致目标提示无法恢复真实的页面展开。

本版本为每个生成请求建立 `target_reference_sequence`，只绑定当前目标对应的有序参考裁剪图，并把 source order、源步骤、内容职责、表达动作和交接写入 Pair 提示。非 fixture 的长序列如果把多个不同物理步骤压成同一套方法字段会在 Passport 阶段失败；真实对标重复仍可保留。分析文本出现 Unicode replacement character 也会失败，要求重新按 UTF-8 读取并记录不可读区域。默认详情页仍为 10 屏、5 组 Pair、3:8。

## 3.9.2 标签载体提示词纠偏

修复了 3.9.1 中一处提示词冲突：全局规则禁止胶囊、徽章、贴纸、标签云和重复圆角卡片等 AI 标签载体，但版式段落仍举例要求保留“胶囊标题、编号徽标”。现在只继承对标的层级、节奏、信息分组和阅读路径；遇到被禁载体时改用开放式标题、正文、数据注释、引线、规则或分栏表达，不复制载体本身。没有删减正文、规格、证据、用法或对标信息密度。

本次没有加入任何测试线程产品事实，默认详情页仍为 10 屏、5 组 Pair、3:8。

## 3.9.1 回到对标驱动的事实选择与展开

本次修订撤掉 3.9.0/早期 3.9.1 引入的强制规则：专利、证书、检测和其他事实不再自动变成必须出现或恰好出现一次的卖点；源步骤也不再被要求写不同的扩展备注或强行去重。详情页仍按对标的物理顺序、opening/content function、密度、边界、交接和可见信息展开。只有当对标该步骤需要某条事实时才选用它；对标重复就保留重复，对标区分才要求区分。

文案合规仍分为 ordinary、evidence_required、category_review、blocked 四档，但证据门只在某条高风险文案被选入画面时触发，不会因为上传过专利或证书就阻止普通详情页规划。

## 3.9.1 合规分级而非全量证据门

详情页合规现在分为 ordinary、evidence_required、category_review、blocked 四档。普通材质、颜色、质地、容量、规格和用法不要求额外检测报告；专利、认证、检测、量化、排名、比较及强安全/功效宣称需要绑定事实证据；医疗和功效敏感词按商品类别复核；明确禁止表达直接失败。该合同不改变对标密度、文字量或法定警示。

本版本只更新通用方法，没有写入任何测试线程产品事实。

本版本只更新参考拆解到目标规划的方法合同：每个物理页面必须记录转化阶段、买家疑问、说服任务、认知变化、证据义务、可见表达动作及语义交接。没有写入任何测试线程的产品事实。详情页默认仍为 10 屏、5 组 Pair、3:8。

## 3.9.0 卖点规划与证据覆盖

针对测试暴露的通用问题，详情规划现在要求多张产品输入先建立 `evidence_inventory`，把专利、证书、检测报告和产品信息图片转成 confirmed fact IDs。标记为优先证据的事实必须恰好绑定一屏；同一个源步骤若展开到多屏，后续屏必须有不同的对标表达/证据展开说明，整屏文案重复直接失败。语义审查还需检查同义重复。末屏的收束职责只保留在内部规划，不能作为画面文字。

验证：117 tests passed（`py -3.12 -m pytest -q`）；quick_validate 通过。本次未重新运行真实 imagegen。

## 3.8.9 标签形式纠偏

3.8.8 将“不要 AI 标签”误收窄成了全局极简审美，导致文字和信息密度被压低。本版本只禁止胶囊、徽章、贴纸、标签云、圆角卡片标签和重复图标短句框等载体形式；正文、说明、规格、证据、成分、用法和对标同等信息量必须保留。高级感、密度和每屏展开不再由 Skill 自定义，全部从对标页的视觉机制、文字块、可见字数、构图和阅读路径继承。

## 3.8.8 启动门修复

适用请求必须先创建或恢复持久化工作流 run，再进入规划或任何图像工具调用。读取 Skill、运行 `--help`、搜索代码或写口头计划都不算执行工作流。原生生图只能使用通过 `audit-plan`、活动生成请求和 `GENERATION_ALLOWED` 后由 `imagegen_adapter.py` 产生的 handoff；直接调用原生工具、API 或手写 prompt 均不属于合同。新增回归测试覆盖该启动门。

## 已完成

- 保留 3.7.2 的紧凑 prompt、参考叙事、产品转译、两屏连续生成及全部路由。
- 新增 `scripts/render_provenance.py`：从真实工具返回的原始文件和 run 内的原样副本读取像素尺寸、SHA-256，生成本地核验回执。该回执不是服务商签名。
- 详情 CLI 记录必须传 `--render-receipt`；低分辨率原始文件、被改动的副本、缺失原件或错误回执均拒绝记录。
- 所有非 fixture 图片审查重新核验原件；fixture 不能覆盖同一目标失败的真实审查。全局人工审查不能把逐图 fixture 的最终清单标记成真实视觉通过。
- 文字和图像仍由模型一次性共同生成，禁止后期加字和用放大充当原生高清。

## 问题证据

任务 `01a07f6e-a20d-7e40-b79d-dbaf444e4c09`（生成人体工学椅详情页），run 为 `D:/详情/runs/run-68d5269a7c2a`。G01 是 768×2048；已记录的 detail-master-upscaled.png 是 1440×3840。对 G01 作 LANCZOS 放大后与后者逐像素相同（RGB RMS 均为 0），确认是插值放大而非原生细节增加。只读证据见修复目录的 artifacts/source-evidence.json。未更改该测试任务的运行文件。

## 验证与限制

完整测试和 package validation 结果见 FINAL_REPORT.md。

本次小修新增：内部 S/G 编号只在机器层存在；G01 原生 Pair 比例作为后续组和导出切屏的比例基准，比例不合格的输出仍拒绝。本次调用真实 imagegen 0 次，人工视觉通过 0 次，视觉模型通过 0 次。测试中的 manual/real 标签均为合成回执，只验证闸门逻辑。

内置工具没有暴露 size/quality 参数，1440×3840 仍是 prompt 请求和验收门槛。代码不能提高服务端的真实输出分辨率，也不能保证中文无乱码、纹理清楚或风格达标。原始路径由 agent 根据工具返回绑定，文件比对不能独立鉴定服务商来源。真实质量仍需新任务生成与逐图视觉审查。

## 分发与回滚

- 安装目录：C:/Users/我的电脑/.codex/skills/all-category-style-benchmark-still
- 当前安装版本：3.9.1（本次 3.9.3 未安装）
- 当前 3.9.1 安装回滚备份：D:/askill/repairs/installed-3.9.0-before-3.9.1-20260911-134847
- .skill：D:/askill/all-category-style-benchmark-still-v3.9.3.skill
- 外层 ZIP：D:/askill/all-category-style-benchmark-still-v3.9.3.zip
- 完整 ZIP：D:/askill/all-category-style-benchmark-still-v3.9.3-complete.zip




## 3.8.2 节奏修复

逐物理屏密度（文字块、卡片、字数、主视觉、留白）与相邻边界方式已经进入分析、目标规划、生成提示词和真实审查。详情 Pair 使用记录的边界，不再统一套用同一类圆角卡片；其它路由保留独立画布并使用翻页承接。

## 3.8.3 当前修复

- 详情 Pair 母图改为只校验原生宽高比 3:8，不再要求 1440×3840；清晰度仍要求使用可获得的原生文件，禁止放大或锐化冒充。
- Pair 提示词增加跨缝可见锚点和参考字形硬约束，覆盖背景/光带/边框/引线/主体承接与字面宽度、字重、字距、层级和载体。
- 其它路由共享同一字体机制提示和单次图文生成规则，保留各自画布与翻页承接。
## 3.8.4 — interruption-safe generation recovery

The generation token is committed before external imagegen handoff. Workflow/request JSON writes are atomic, and an awaiting target gets `artifacts/recovery-manifest.json` containing the run identity plus request/prompt hashes. `resume` validates this lock; `resume-imagegen-request` re-emits the exact original handoff and bound input paths. Missing or changed artifacts fail closed. No real imagegen or visual review was run for this repair.

## 3.8.7 continuation — opening-function semantic regressions

The 3.8.7 opening-function implementation was rechecked on the non-fixture path. A target plan must preserve the observed `opening_function` for each mapped source step, and its source-content mappings must begin with the first observed physical step and remain in reading order. Added regression coverage for a `result_promise` opening that must reject a `problem_awareness` target, plus the corresponding valid pain-opening case. No test-thread product facts were added.

Validation for this continuation: 109 tests passed; quick validation passed. Real imagegen and visual review were not run.

## 3.8.7 continuation — detail expansion follows the benchmark

The detail route no longer makes callers author a second fixed per-screen story through `buyer_question`, `answer`, `belief_shift`, `handoff_question`, `required_visual_roles` or `evidence_bindings`. Each detail target remains bound to physical source content steps, density and transitions; missing target-side narrative labels are derived from those source steps. The native prompt states that the mapped benchmark step is the only source of each screen's expansion logic and that product facts only translate that method. Other routes retain their target contracts.

Validation for this continuation: 110 tests passed; quick validation passed. Real imagegen and visual review were not run.

## 3.8.7 continuation — global decorative-label ban and premium direction

Added a route-independent machine contract and prompt rule that forbids AI-looking decorative labels: icon-plus-short-benefit captions, repeated pills or badges, floating stickers, label clouds, filler icon rows and unsupported certification badges. Necessary product names, specifications, compliance warnings and evidence remain allowed only as naturally integrated text. Added `decorative_label_free` to shared visual audit dimensions. Premium direction is expressed through reference-bound hierarchy, whitespace, material realism, controlled light, restrained decoration and a clear focal point rather than extra UI components.

Validation for this continuation: 110 tests passed; quick validation passed. Real imagegen and visual review were not run.

