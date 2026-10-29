# 参考图分析输入与普通失败恢复（3.7.1）

对标含拼图、长页、多栏预览或一套多张图时，先读取本协议。不要凭总览图的颜色概括整套风格。每张图的物理拆屏、逐屏读字、整套叙事分析都是规划的输入。

## 从可填写模板开始

在选定路由后执行（以下 `<run>` 替换成实际路径，每条命令成功后才继续）：

```powershell
py -3.12 scripts/entrypoint.py init-reference-analysis --workspace <run> --analysis <run>/reference-analysis.json
```

模板读取本次登记的参考 ID 和尺寸，提供八个准确的机制键。空 observation 和未判断的 is_overview **不能过闸门**。这个命令不识图、不 OCR、不自动判路由，也不填入虚构结论；已有文件不会被覆盖。

打开每一张原参考图。判断是否是一张图内包含多个完整商业页面；一个页面里的多个功能小框不自动等同于多屏。观察栏位、边界、标题及叙事衔接，决定阅读顺序。不能统一假定行优先或列优先。填写：

- `overview_detection.is_overview`：只要有一张参考图被拆成多屏就是 true；多张独立参考图并不自动等于 overview。
- `panel_count`：所有参考文件合计的物理页面数。
- `reading_order`：说明本套参考的实际阅读路径，例如先左栏自上而下、再右栏。
- `detection_notes`：说明边界识别证据、是否有缺失/截断页面和读不清区域。源文件中可见的半屏也登记，并注明截断。
- `panels`：按阅读顺序逐屏登记唯一 `panel_id`、连续 `panel_order`、登记的 `reference_input_id` 以及原图像素坐标 `bbox: [left, top, right, bottom]`。右和下边界不包含在裁图内。

例如一张两行预览（尺寸仅为协议示例，不能套用到用户素材）：

```json
{
  "is_overview": true,
  "panel_count": 2,
  "reading_order": "同一栏由上到下",
  "detection_notes": "两个完整页面由明显横向留白分隔",
  "panels": [
    {"panel_id":"B01","panel_order":1,"reference_input_id":"R01","bbox":[0,0,600,800]},
    {"panel_id":"B02","panel_order":2,"reference_input_id":"R01","bbox":[0,800,600,1600]}
  ]
}
```

然后执行：

```powershell
py -3.12 scripts/entrypoint.py crop-reference-panels --workspace <run> --analysis <run>/reference-analysis.json
```

它生成 `artifacts/analysis-panels/panel-001.png` 等真实裁图，写入每屏的 `source_crop_path`、SHA-256 和 `analysis_only: true`，并在内容序列为空时生成逐屏待填记录。输出保持 `ROUTE_CLASSIFIED`：裁完图不等于完成分析。

验证器会检查所有参考均被覆盖、坐标合法、各屏文件独立、没有大幅重叠的区域、裁图像素与原图区域一致。不允许用两个裁图路径冒充七个物理页面。脚本无法发现 agent 从未声明的未知页面，因此仍须逐图目视检查完整性。

## 逐屏读字，然后分析整套叙事

逐一查看生成的完整屏裁图；不要只看原总览缩略图。每个 `source_content_analysis.sequence` 记录只能绑定一个 `source_panel_ids`，但一屏可以有多个内容步骤。字段：

```json
{
  "id":"T01", "source_order":1,
  "source_reference_ids":["R01"], "source_panel_ids":["B01"],
  "content_role":"提出买家的当前困扰",
  "verbatim_text":["实际读到的主标题", "实际读到的副标题和其他可辨认文字"],
  "text_status":"transcribed",
  "normalized_message":"抽象这屏在说什么，不能提前写成当前产品卖点",
  "handoff":"说明本屏如何引出下一屏；末屏说明如何收束"
}
```

全部可辨认文字都需要读取，不能只取标题。无字页面使用 `no_visible_text` 和空文字列表；读不清使用 `partially_unreadable`，填写 `unreadable_regions`，记录可读的部分并描述未知部分，不臆造数字、功效和文字。填写整套 `set_summary`、`narrative_logic.opening/proof_progression/takeaway/handoff` 以及带 id/role/description/source_reference_ids 的 narrative sequence。把逐屏内容综合成整套铺陈方式，不能只逐屏列商品功能。先判断每个物理页面的功能，尤其是首屏必须填写 `opening_function`：`result_promise`（英雄/结果承诺）、`problem_awareness`（痛点识别）、`product_first`、`proof_first` 或 `other`。首屏是结果承诺时，目标详情页首屏必须继承结果承诺，不能因为产品事实或默认模板改成痛点页；只有对标首屏本身是痛点识别时，才从痛点开始。每个内容步骤还必须回答六个方法问题：它处在什么 `conversion_stage`，买家此刻在问什么 `buyer_question`，这一屏要完成什么 `persuasion_job`，用户看完后相信了什么 `belief_shift`，必须出现哪些 `evidence_obligations`，以及用哪些可见的 `expression_actions` 把证据和结论说清楚。证据可以是图表、对比、结构图、实验截图、证据说明或产品演示；没有数据时要记录“无量化证据，使用可验证的信号”，不能凭空补数字。

每条相邻边界还要记录 `semantic_handoff` 和 `next_buyer_question`。背景、颜色或引线的连续只能说明画面接得上，不能代替“为什么下一屏必须出现”。

## 风格裁图与八维 Passport

完整屏裁图保留原文案/商品，供分析阅读；不能直接当成清洗后的生图参考。另行制作小范围风格机制裁图，检查并去掉来源商品身份、品牌、包装、Logo、SKU、文案、环境语义。登记在 `units`：

```json
{
  "id":"C01", "reference_input_id":"R01",
  "source_crop_path":"<run>/crops/mechanism-01.png", "source_role":"style_mechanism_only",
  "source_semantics":["实际观察到并需要转译的来源语义"], "review_status":"visual_model",
  "source_brand_presence":false, "source_packaging_presence":false,
  "source_logo_presence":false, "source_sku_presence":false, "competitor_presence":false,
  "source_copy_presence":false, "source_environment_presence":false
}
```

false 必须来自实际看图审查，缺省不是无污染。多文件轮播图还需填写 `source_order`。每个参考文件至少有一个有效机制裁图。

八个准确键及观察重点：

| 键 | 观察对象 |
|---|---|
| typography | 字形、字重、笔画、斜切/手绘形态、大小对比 |
| material | 手绘、纸张、颗粒、网点、材质表达 |
| composition | 主体比例、布局、留白、构图节奏 |
| hierarchy | 标题、副标题、证据及标注的层级 |
| carrier | 贴纸、涂鸦、线条、功能卡片等承载形式 |
| light_depth | 光照、阴影、镜头与空间层次 |
| density | 信息数量、字号占比、视觉疏密 |
| reading_path | 视线移动、阅读方向及屏间衔接 |

每项必须有非空 `observation` 和有效的 `source_unit_ids`。不支持把 `typography` 改名成 `typography_morphology` 后期待脚本猜测；从模板保留字段名，填入当前参考的具体观察。普通风格形容词不够支撑后续视觉审查。

```powershell
py -3.12 scripts/entrypoint.py compile-reference-crops --workspace <run> --analysis <run>/reference-analysis.json
py -3.12 scripts/entrypoint.py compile-visual-passport --workspace <run> --analysis <run>/reference-analysis.json
```

## 恢复

失败时命令退出码 2，输出具体缺项和保留的状态。自行修正后重试失败命令；不要把后续命令串起来，不要改写 workflow.json，也不要改路由/改为直接单屏生图。Passport 缺项可直接补齐重试；如果改了拆屏坐标，依次重跑 crop-reference-panels、compile-reference-crops、compile-visual-passport。旧版仅有 crop_unit_ids 的记录必须补齐真实物理拆屏，不能自动当作已审查。

模型容量不足是外部运行限制，代码无法消除。恢复后执行 `resume --workspace <run>` 查看 `next_action`；已有等待 artwork 的 Pair 请求会继续使用原 token，不生成另一个请求覆盖它。

