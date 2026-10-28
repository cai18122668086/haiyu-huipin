---
name: aurora-remix-fast
description: 对标二创·快版（API 版）—— 一张对标图 + 你的产品图，5 分钟内出一张同版式电商图（单屏 / 少量屏）。默认走 api 路由（自有网关），一次成图 + 4 项快检，无状态机、无计划先审。触发词：快版对标、5分钟出图、快速二创、对标快出图、照着这张快速出一张、简化版对标。要多屏套图 / 交付级审计记录 / 返修额度 → 用 aurora-ecommerce-reference-remix（母本）。
agent_created: true
---

# 对标二创 · 快版 API版

**目标：从「拿到两张图」到「出成图」≤ 5 分钟。本版默认走 api 路由，供网站 / 服务端调用。**

## 路由（本版默认 api）

**默认路由 = `api`。** 出图走 `scripts/render_image.py` 调 OpenAI 兼容网关，**不依赖宿主内置生图工具**——
网站 / 服务端宿主没有内置工具，这是本版存在的理由。

- 网关由**调用方提供**：`--base-url` + `--api-key`，或 `--config <配置文件>`，
  或环境变量 `$IMAGE_PROVIDER_BASE_URL` / `$IMAGE_PROVIDER_API_KEY` / `$IMAGE_PROVIDER_CONFIG`。
- 本包 `provider-config.json` 里是**占位值**，**不会被隐式使用**；没给网关时 api 路由直接拒绝执行。
- 宿主有内置生图工具、且只是本机手动出图时，可用**备用路由 A**（见下），但它不是本版默认。

## 何时用（先看这张表）

| | 母本 `aurora-ecommerce-reference-remix` | 本 skill（快版） |
|---|---|---|
| 状态机 / 三份 JSON（input·plan·plan-audit） | 有 | **无** |
| 计划先审 | 强制 | **跳过** |
| 成图审计 | 10 维逐项 + 6 维身份 | **4 项快检** |
| 返修额度 | 1 初稿 + 1 返修，带 token | **手改重出，无额度** |
| 多屏套图一致性 | 有专门守卫 | 只做单屏；多屏请逐屏跑 |
| 交付 | 带 audit 记录的交付包 | 成图 + 一段口径说明 |

⚠️ **代价（必须当面告诉用户）**：快版省掉的是「计划先审 + 成图逐维审计」，**产品一致性风险高于母本**。它适合「同一对标 + 同一产品反复出新图 / 快速试版式」。要严格保真、要可追溯的审计记录 → 用母本。

## 三条输入（缺了先问，一次问清，不要逐项追问）

1. **对标图**：要复刻的那张（本地路径或用户上传）。
2. **产品身份图**：能看清产品形状 / 材质的那张原图（**必填**，不是可选）。
3. **产品事实**：几何（轮廓 / 比例 / 结构 / 部件关系）+ 不许变的特征（颜色、纹样、朝向、数量）。

## 时间预算（超了就砍下面标注的项）

| 步骤 | 预算 | 超时的第一个牺牲品 |
|---|---|---|
| 0 备料 | 40s | 形态板（产品无方向性特征时本来就该省） |
| 1 写提示词 | 90s | `REFERENCE FACTS` 只写会改变画面的字段 |
| 2 出图 | 120–300s | —（api 路由单图 150–290s，不可砍） |
| 3 快检交付 | 60s | `zoom` 局部核对降到 1 处 |

**实测标定**：
- **api 路由**：单图 150–290s（含排队与重试），端到端约 **4–6 分**。
- 同素材走宿主内置路由时实测 3 分 17 秒（备料 9s ＋ 看图写提示词 35s ＋ 出图 113s ＋ 快检交付 27s）——**仅作参考，不是本版默认**。
- 同素材走母本（含计划先审与逐维审计）是 **23.5 分**。

---

## 第 0 步 · 备料（≤40 秒）

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
FAST="<本 Skill 根目录>/scripts/fastprep.py"

# ① 读对标图尺寸 → 直接拿到建议画布
"$PY" "$FAST" ratio "<对标图>"

# ② 对标图是多屏长图 → 按屏切开；单屏则跳过这步
"$PY" "$FAST" crop "<对标图>" --out "<run>/units" --spec "0,0,1,0.5;0,0.5,1,1"

# ③ 产品有方向性特征（条纹 / 格纹 / 朝向 / 左右不对称）→ 拼形态板；否则跳过
"$PY" "$FAST" board --out "<run>/board.png" "<原图A>" "<原图B>"
```

- run 目录建在素材旁边：`<素材目录>/fast_work_<YYYYMMDD-HHMM>/`
- **画布只跟对标图走**，不要拿产品图的尺寸当画布。
- ⛔ **形态板铁律：每格朝向必须一致。** 有「侧转 90° 拍」的原图一律**不进板**——那等于教模型「条纹可以是竖的」（母本踩过：身份锁写成「竖条纹」导致整版产品错）。

## 第 1 步 · 写提示词（≤90 秒）

用 Read **真看图**：对标屏 ×1、产品身份图 ×1。然后把观察结果按下面模板填进 `<run>/prompt.txt`。

**模板（逐字照抄骨架，只替换 `<>`）：**

```
Create ONE complete Chinese ecommerce visual for the current token only.
The explicitly bound product identity image defines the current product; only the reference image defines style/composition.
PRODUCT GEOMETRY LOCK (binding, must be reproduced exactly, do not substitute a generic product): 轮廓：<>；比例：<>；结构：<>；部件关系：<>
PRODUCT IDENTITY CONSTRAINTS (binding): <不许变的特征，逐条；含"不是XX"的反向排除>
REFERENCE FACTS (observed one-to-one from the benchmark image): {"subject_and_scale": "<>", "layout_and_reading_path": "<>", "carrier_morphology": "<>", "commercial_function": "<>", "camera_light_depth": "<>", "typography_structure": "<>", "brand_and_human_presence": "<>", "meaningful_absences": "<>", "cross_screen_role": "<>"}
PAGE DUTY: <这一屏的任务>
BUYER CONCLUSION: <看完这一屏，买家该得出的一句话>
GENERATION BRIEF: <逐块几何复刻 + 换成本品内容；每块给归一化坐标>
APPROVED VISIBLE COPY ONLY: [<逐字文案，逗号分隔>]
Reproduce subject scale, layout, carrier morphology, commercial function, camera/light/depth, typography silhouette, presence/absence, and cross-screen role one-to-one.
Do not turn continuous evidence, comparison, pain-solution, report, or exploded structures into generic repeated cards.
Do not add a standalone brand when the observed reference has none. Never copy benchmark branding, watermark, celebrity identity, exact copy, or unsupported claims.
Source semantic leakage guard: source brand, packaging, logo, SKU, watermark, exact wording and literal source-world props must not appear in the target.
Target canvas <W>×<H> with the reference crop aspect ratio.
```

**决定成败的 6 条（母本用血换的）：**

1. **几何锁写满四段**（轮廓 / 比例 / 结构 / 部件关系），每段给**可量化**的数：宽高比、占比、第几段缝合。只有形容词 → 模型自由发挥。
2. **身份约束里必须有反向排除**，且**方向性特征写死朝向**。例：「横向、与腰头平行（**不是竖条纹、不是斜条纹**）」。
   ⛔ 一个形态被 3 次以上具体名词正面描述时，**光加否定句堵不住**——必须改正面措辞本身，并补可量化上限（如「条纹块宽 ≤ 画宽 60%」）。
3. **`GENERATION BRIEF` 用归一化坐标**（x / y 用 0–1 画幅比例，如 `x 0.575–0.925、y 0.022–0.375`）。「左上 / 右下」这种方位词在长画布上没有基准，模型会算错。
   ⛔ **留白也要给坐标。** 只给元素坐标、不写边距，模型会把元素顶到画布边缘（实测：对标卡片上沿 y=0.022，成图直接顶到 y=0）。对标四边有留白时，补一句「卡片上沿留白约 2% 画幅、右侧留白约 7%」。
4. **`APPROVED VISIBLE COPY ONLY` 是白名单**：不在这个列表里的字，模型就不该画。对标图上的**价格 / 销量 / 功效等级 / 检测报告 / 品牌字标** → 换成可核实属性，或整块去掉，并写进 `GENERATION BRIEF` 的排除句。
5. ⛔ **不要在提示词里写否定句去禁一个你自己写进去的词**——会被泄漏扫描自触发。要禁就直接不提那个词，用中性词替换（例：不写「不得出现抗菌等级」，改写「不出现功效等级」）。
6. ⛔ **文案不许吹画面撑不住的**。能说「有什么」，不能说「多到溢出 / 满手 / 爆浆」。

⛔ **`Target canvas <W>×<H>` 这一行不只是说明**：部分网关会**丢弃请求里的 `size` 字段、从提示词文本里读像素尺寸**。这行的数字必须是你想要的画幅。

## 第 2 步 · 出图

### 路由 B · api（**本版默认**）

参考图顺序：**身份图 → 形态板（可省）→ 对标屏**。单图 150–290s，一律后台跑。

```bash
env -u IMAGE_PROVIDER_BASE_URL -u IMAGE_PROVIDER_API_KEY "$PY" \
  "<本 Skill 根目录>/scripts/render_image.py" \
  --prompt-file "<run>/prompt.txt" --out "<run>/artwork.png" \
  --reference "<产品身份图>" --reference "<对标屏>" \
  --size <W>x<H> --quality high \
  --base-url "<网关地址>/v1" --api-key "<key>" \
  --timeout 900 --overall-timeout 1800
```

- 也可以把网关写进一份配置文件，用 `--config <文件>` 传（结构见 `provider-config.example.json`）。
- ⛔ 必须用 `env -u IMAGE_PROVIDER_BASE_URL -u IMAGE_PROVIDER_API_KEY` 包裹，否则用户级环境变量会**静默劫持** `--config`（优先级 `CLI > env > config`）。
- ⛔ 出图后**核对 `<out>.receipt.json` 的 `base_url`** 是不是你给的那个；不符即本次作废。
- 网关地址**必须带 `/v1`**；少了会拿到前端 HTML，报 `provider returned no image data`。
- 图像端点不接受模型名 `auto`，要用具体模型名（如 `gpt-image-2.5-flare`）。
- 失败时**先看请求有没有送出去**：stdout 里有 `upstream_task_id` / `provider_request_id` = 已提交，回去查状态，**不要重试**（那是再买一次）。

**网站 / 服务端接法**：把网关地址与密钥放在服务端环境变量或一份配置文件里，每次调用传 `--config`。
本 skill 不持有任何密钥，密钥由调用方管。

### 路由 A · 内置 ImageGen（**备用**）

只有「宿主有内置生图工具、且是人工在本机出图」时才用。参考图最多 3 张，顺序同上。

```
ImageGen(
  prompt  = <prompt.txt 全文>,
  size    = "<ratio 输出的 builtin_size>",     # 例 "1024x1360"，须 16 整除且比例偏差 ≤1%
  quality = "high",
  image1  = <产品身份图>,
  image2  = <形态板>          # 没有形态板就跳过
  image3  = <对标屏>
)
```

⛔ **宿主返回的文件名 ≠ 你要的路径**（文件名取提示词首行）。必须**手动 `cp` 到目标路径**再往下走，否则你登记 / 交付的是旧图。
⛔ 内置路由**可能烧入 `AI生成 WORKBUDDY` 水印**（实测约 10 次 1 次）→ 第 3 步必须查；带水印的图**不得交付、不得本地修掉**，同路由重出。

## 第 3 步 · 快检 + 交付（≤60 秒）

```bash
"$PY" "$FAST" wmcheck "<run>/artwork.png" --out "<run>/_wm.png"      # 右下角 30%×12% 放大 2x
"$PY" "$FAST" zoom   "<run>/artwork.png" --box <x0,y0,x1,y1> --out "<run>/_zoom.png" --scale 3
```

四项，逐项**真的用 Read 看过图**再下结论：

1. **尺寸 / 比例**：走 api 路由时，部分网关会丢弃 `size`、按提示词文本重算像素数（常见是归一化到固定面积），那就只判**比例**不判绝对像素；比例与对标屏偏差 ≤1%。
2. **水印**：读 `_wm.png`，右下角有没有 `AI生成 WORKBUDDY` 之类的字标。有 → 作废重出，**不许本地抹掉**。
3. **泄漏**：画面里有没有出现对标世界的品牌字标 / 价格 / 销量 / 功效等级 / 尺码表 / 检测报告。
4. **关键特征**：`zoom` 放大 1–3 处**产品身份的关键区**（方向性纹样朝向、腰头、底裆、接口…），逐条对着 `PRODUCT GEOMETRY LOCK` 与身份约束核。**这一步是快版唯一的产品保真防线，不许省。**

交付：把成图 `shutil.copy2` **逐字节**复制到交付目录（母本约定：`生成图\<商品>最终图\<skill中文名>\`，本 skill 中文名 **「极光对标二创·快版」**），另附一段口径说明，写清：
- 用哪张对标图、哪张产品图；
- 逐字文案清单（对照 `APPROVED VISIBLE COPY ONLY`）；
- **所有与对标的差异**（哪怕很小）与**未解决项** —— 如实写，不要用漂亮话盖过去。

最后 `present_files` 把成图放第一个。

## 与母本共用的事实（不要重新发明）

- 权威顺序：`USER_REQUEST > USER_FACT > CURRENT_PRODUCT_PIXELS > BENCHMARK_VISUAL_GRAMMAR > AGENT_INFERENCE`。用户说的 > 你从图里推的。
- 身份图是 `PRODUCT_IDENTITY_AUTHORITY`；对标图是 `BENCHMARK_COMPOSITION_ONLY`（只给版式，不给产品）。
- 对标页含**不含产品实物**的屏（纯文字 / 纯场景）→ 那屏必然撞「商品身份必须可见」这条硬规则，**出图前先摆给用户定口径**，别闷头出。

## 目录结构

- `SKILL.md` — 本文档，唯一的行为权威
- `agents/openai.yaml` — 展示名与默认提示词
- `scripts/fastprep.py` — 备料 / 快检工具（ratio / crop / board / wmcheck / zoom）
- `scripts/render_image.py` — api 路由出图桥（OpenAI 兼容）
- `provider-config.json` / `provider-config.example.json` — 网关配置**模板**（占位密钥，不会被隐式使用）
- `requirements.txt` / `VERSION` / `CHANGELOG.md` / `安装与使用.md`

## 踩过的坑（跨项目，已复现过）

| 现象 | 根因 | 处置 |
|---|---|---|
| 出图 5 分钟以上 | 把母本的规范文档 / 800 行守卫全读了一遍 | 快版**不读母本任何文件**，本文档已自包含 |
| 提示词写了「竖条纹」，成图也是竖的 | 身份锁写错，模型只是忠实执行 | 读**原图**核朝向，不看自己的旧笔记 |
| 形态板里混进侧转 90° 的原图 | 板内朝向不一致 = 教模型纹样可以转 | 拼板前逐张确认朝向，不一致的剔除 |
| 长画布上「左上」画到了别处 | 方位词无基准 | 一律给归一化坐标 |
| 元素顶到画布边缘、丢了留白 | 只给了元素坐标，没给边距 | 留白也写归一化坐标 |
| 文案里的否定句被判泄漏 | 禁的那个词自己也写进了 prompt | 直接不提，用中性词替换 |
| 交付图字节和成图不一样 | 用 PIL 重存过 | 只允许 `shutil.copy2` 逐字节复制 |
| `--out` 给了但文件还是旧的 | 桥脚本不负责把宿主返回值复制过去 | 手动 `cp`，再回填 |
| receipt 里 base_url 是别的网关 | `IMAGE_PROVIDER_BASE_URL` 用户级持久变量劫持（`CLI > env > config`） | 用 `env -u ...` 包裹调用 |
| `provider returned no image data` | 网关地址丢了 `/v1` | 补回 `/v1` |
| `HTTP 400: images endpoint requires an image model, got "auto"` | 网关图像端点不接受 `auto` | 用具体模型名 |
| 出图比例总是差一点 | 某些网关丢弃 `size`、按提示词文本重算像素数 | 判**比例**不判绝对像素；`Target canvas` 行写对 |
| 前台卡住 | 单图 150–290s，超默认超时 | 一律 `run_in_background: true` |
| Bash 里 `$PWD` / `/tmp` 传给 Python 解析成 `D:\d\...`、`d:\tmp\...` | MSYS 路径 | 跨工具传路径**只用 Windows 绝对路径** |
