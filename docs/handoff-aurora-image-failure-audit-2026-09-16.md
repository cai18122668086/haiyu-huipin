# 交接文档：Aurora 国内电商图片生成失败 — 全面审计与修复方案

- **审计日期**：2026-09-16
- **仓库**：`D:\Projects\polaris-commerce-studio-model-pose-entrypoints`
- **分支**：`codex/stabilize-pool-responsiveness`
- **审计时 HEAD**：`cdb02b4 fix(quota): settle legacy usage without cost`
- **生产环境**：`43.129.198.147`，docker compose 项目 `polaris-production`
- **关联会话**：Codex 线程 `01a0a3ab-4dbe-7b40-89c6-65afaa021327`

> 本文档的目标：让接手的人**不需要重新排查**就能直接动手修。所有结论都附了证据来源与复现方式。

---

## 0. 结论摘要（先看这段）

图片生成失败**不是单一原因**，而是 8 个独立问题叠加（其中 **4 个是确定性代码缺陷**）。按影响面排序：

| 编号 | 根因 | 性质 | 影响量级 | 修复难度 |
|---|---|---|---|---|
| **RC-1** | `api.ooapi.cc` 聊天通道间歇性 429/502/503，且配置的模型 `gpt-5.5` 已完全不可用 | 外部依赖 | 今天 12+12 次失败 | 改配置（立即生效） |
| **RC-2** | 图片格式校验器**硬编码 JPEG**，但请求格式已改为 PNG | **确定性代码缺陷（仍在生产）** | 详情页图片路径必然失败 | 1 行 |
| **RC-3** | `checks` 的 JSON Schema 允许返回任意子集，校验器却强制要求 29 项且顺序一致 | **确定性设计缺陷** | 契约类失败 56% 的一部分 | 中等 |
| **RC-4** | 可观测性缺失：三处关键失败点丢弃真实原因 | 工程债 | 导致本次排查极其困难 | 小 |
| **RC-5** | JSON 解析失败被包装成通用的 "pixel audit failed"，误导为图片质量问题 | 错误分类缺陷 | 2 次已确认 | 小 |
| **RC-6** | 上游 502/429 被误判为**不可重试**，已配置的重试机制完全失效 | **确定性代码缺陷** | 放大 RC-1 的全部影响 | 1~3 行 |
| **RC-7** | 部分任务回落串行路径，撞上"必须严格按序生成"检查 | 配置/路径不一致 | 4 次已确认 | 小 |
| **RC-8** | 失败率基线本来就高（早于本次 API 切换） | 长期工程债 | 决定最终天花板 | 需评估 |

**最容易被误判的一点**：`AURORA_EXECUTION_FAILED` 这个名字听起来像"图片质量不合格"，但实测其中 **56% 是模型输出不符合 JSON 契约**，**31% 是中转站 HTTP 错误**，只有一小部分是真正的审核不通过。

**已排除的原因**（重要，别往这些方向查）：

- ❌ 不是"中转站不支持 `json_schema`"——实测 `gpt-5.6-luna` / `gpt-5.6-sol` **完整支持且严格强制执行**（探针见 §7.1）
- ❌ 不是"schema 太大导致被拒"——14,280 字节的完整 schema 实测返回 200，且 `observations=22/22, checks=29`（隔离测试见 §4 RC-3）
- ❌ 不是"模型没有视觉能力"——实测 luna / sol / astra 均能正确读图（见 §5 排除项）
- ❌ 不是"并发没开"——6 个页面的 `started_at` 相差 < 0.1 秒（见 §5 排除项）

---

## 1. 问题现象（用户视角）

用户在网站提交国内电商主图 / 详情页任务后：

1. 页面提示「**国内电商主图规划失败，请重新提交任务。**」——没有任何具体原因
2. 或提示「第 N 页：…失败，请重新提交任务。」+「按原方案重新生成」
3. 或点击重新生成后报「**Observed provider usage has no conservative cost.**」（已修，见 §4.7）
4. 网站偶尔整体卡住打不开（已定位为 Worker 长时间占用，见 §4.8）

**关键用户反馈**：多次出现「规划阶段成功了，但生成图片失败」。这句话是本次审计的起点。

---

## 2. 取证方法与可复现命令

### 2.1 数据来源

| 来源 | 位置 | 说明 |
|---|---|---|
| 生产数据库 | `polaris-production-postgres-1` | `jobs` / `job_items` / `usage_events` 表 |
| Worker 日志 | `docker logs polaris-production-worker-1` | 只有 Celery 生命周期，**无异常详情** |
| 生产容器代码 | `docker exec polaris-production-api-1` | 用于核对部署版本 |
| Codex 会话记录 | `D:\CodexHome\sessions\2026\09\15\rollout-*.jsonl` | 历史排查过程 |

> 注意：`.codex` 是指向 `D:\CodexHome` 的 junction，`find` 需要直接查 `D:\CodexHome`。

### 2.2 连接生产库

```bash
ssh -i <key.pem> -o StrictHostKeyChecking=no ubuntu@43.129.198.147 \
  "docker exec -i polaris-production-postgres-1 psql -U polaris -d polaris"
```

### 2.3 读取生产环境 provider 配置（不回显密钥）

```bash
ssh -i <key.pem> ubuntu@43.129.198.147 \
  "docker exec polaris-production-api-1 env | grep -E '^POLARIS_(ANALYSIS|IMAGE)'"
```

当前生产配置：
```
POLARIS_ANALYSIS_BASE_URL=https://api.ooapi.cc
POLARIS_ANALYSIS_MODEL=gpt-5.5              ← 该模型通道已不可用（持续 502）
POLARIS_IMAGE_BASE_URL=https://api.artflow.vip
POLARIS_IMAGE_MODEL=gpt-image-2.5-flare

POLARIS_AURORA_EXECUTION_MODE=parallel_v20
POLARIS_XINGHE_CONCURRENCY=6
POLARIS_AURORA_MAX_TRANSIENT_RETRIES=2      ← 重试已配置，但见 RC-6 实际不生效
POLARIS_AURORA_RETRY_BACKOFF_BASE_SECONDS=2
POLARIS_AURORA_RETRY_BACKOFF_MAX_SECONDS=30
```

### 2.4 核对部署代码与仓库是否一致

```bash
ssh -i <key.pem> ubuntu@43.129.198.147 \
  "docker exec polaris-production-api-1 sh -c 'grep -n \"^IMAGE_FORMAT\" /app/app/xinghe_agent.py; \
   grep -n \"requested JPEG format\" /app/app/xinghe_agent.py'"
```

实测输出（**与仓库一致，说明缺陷确实部署到了生产**）：
```
163:IMAGE_FORMAT = "png"
5613:            "Provider did not return the requested JPEG format.",
```

---

## 3. 失败全貌（生产库实测数据）

### 3.1 全部失败错误码聚合（全历史）

| error_code | 次数 | 涉及 job 数 | 首次 | 最近 |
|---|---|---|---|---|
| `PLANNER_SCHEMA_INVALID` | 64 | 4 | 2026-07-29 | 2026-08-05 |
| **`AURORA_EXECUTION_FAILED`** | **55** | **22** | 2026-08-17 | **2026-09-16** |
| **`AURORA_PLANNING_FAILED`** | **54** | **9** | 2026-08-17 | 2026-09-15 |
| `AMAZON_PLAN_SCREEN_INVALID` | 42 | 2 | 2026-08-05 | 2026-08-05 |
| **`PLANNER_REQUEST_FAILED`** | **40** | **7** | 2026-07-30 | **2026-09-16** |
| **`AURORA_SET_AUDIT_FAILED`** | **32** | **11** | 2026-08-21 | **2026-09-16** |
| `HARNESS_BLOCKED` | 27 | 10 | 2026-08-20 | 2026-08-22 |
| `IMAGE_RATE_LIMITED` | 22 | 7 | 2026-08-19 | 2026-08-24 |
| `AMAZON_SUITE_COHESION_INVALID` | 21 | 1 | 2026-08-05 | 2026-08-05 |
| **`IMAGE_RESPONSE_FORMAT_INVALID`** | **6** | **3** | 2026-09-16 | 2026-09-16 |
| 其他（共 26 类） | — | — | — | — |

### 3.2 分业务类型失败率（近 30 天）

| job type | 成功 | 失败 | 总数 | **失败率** |
|---|---|---|---|---|
| **`domestic_main_image`（国内电商主图）** | 54 | 175 | 324 | **54.0%** |
| **`detail_pack`（详情页）** | 30 | 58 | 116 | **50.0%** |
| `local_edit` | 3 | 2 | 5 | 40.0% |
| `product_image` | 2 | 1 | 3 | 33.3% |
| `reference_remix` | 23 | 5 | 28 | 17.9% |
| `creative_image` | 52 | 7 | 59 | 11.9% |
| `amazon_visual_suite` | 21 | 0 | 21 | **0.0%** |

**关键观察**：重灾区是 Aurora 国内电商（主图 + 详情页），失败率约 50%。而 `amazon_visual_suite` 同样走分析 API 却 0% 失败——说明问题集中在 **Aurora v2.0 这条路径特有的契约**上，不是通用链路。

### 3.3 `AURORA_EXECUTION_FAILED`（55 次）按具体原因细分

| 具体错误信息 | 次数 | 归类 |
|---|---|---|
| `Planner request failed with HTTP 429.` | 7 | **上游** |
| `Planner request failed with HTTP 502.` | 6 | **上游** |
| `Aurora v2 decision_summary is invalid` | 6 | 契约 |
| `Aurora v2 native visual judgment is incomplete: audit_complete, image_sha256` | 6 | 契约 |
| `Aurora v2 observations must be an object` | 5 | 契约 |
| `Aurora v2 observations are incomplete: claim_takeaway_without_copy, claim_to_evidence_relation, commercial_closure_visible` | 5 | 契约 |
| `Planner request failed with HTTP 503.` | 4 | **上游** |
| `Aurora pages must be generated strictly in order` | 4 | 流程 |
| `Aurora v2 typography_style_match.material does not match v2 policy` | 4 | 契约 |
| `Aurora v2.0 pixel audit failed`（= JSON 解析失败，见 RC-5） | 2 | **解析** |
| `Aurora v2.0 style calibration cannot use failed page 0` | 1 | 级联 |
| `Aurora Harness is not authorized for generation` | 1 | 流程 |
| `Aurora v2 judgment page_index is stale` | 1 | 契约 |
| `Aurora v2 checks must contain the complete ordered native check set` | 1 | **契约（RC-3）** |
| `Aurora v2 observations are incomplete: background_competition, claim_takeaway…` | 1 | 契约 |
| `Aurora v2.0 style calibration requires an accepted page 0` | 1 | 级联 |

**归类统计**：
- 契约/结构不符：**31 次（56%）**
- 上游 HTTP 错误：**17 次（31%）**
- 流程/状态/级联：**7 次（13%）**

### 3.4 时间线（揭示两个不同的时代）

| 日期 | 主要失败码 | 说明 |
|---|---|---|
| 2026-08-21 | `observations must be an object`(5)、`decision_summary is invalid`(6)、`incomplete: audit_complete,image_sha256`(6)、`typography_style_match.material`(4)、`page_index is stale`(1)、`checks must contain complete ordered set`(1) | **旧 provider 时代**（`tengli` + `gpt-5.6-terra`）。失败形态是**结构性错误**——观测字段类型错、字段缺失、`material` 布尔值错。这类错误在严格 schema 下**不可能发生**。 |
| 2026-08-22 / 08-24 / 09-02 | `AURORA_SET_AUDIT_FAILED` | 六页集合审计不通过 |
| **2026-09-15** | `AURORA_PLANNING_FAILED`(12)、`PLANNER_REQUEST_FAILED`(6) | **API 拆分上线日**（commit `d50a83c`） |
| **2026-09-16** | `PLANNER_REQUEST_FAILED`(12)、`AURORA_EXECUTION_FAILED`(12)、`IMAGE_RESPONSE_FORMAT_INVALID`(6)、`AURORA_SET_AUDIT_FAILED`(4) | 新 provider 时代，上游大面积不可用 |

**重要推论**：契约类失败在**两个 provider 上都出现过**，但**形态不同**：
- 旧 provider：结构性错误（类型错、字段缺失、`material` 值错）→ 说明旧 provider **没有强制执行 strict schema**
- 新 provider（ooapi.cc）：`observations are incomplete: <若干具体字段>` → 说明 schema **被执行了**（否则不会只差几个字段），但上游不稳定导致响应异常

---

## 4. 逐条根因分析

### RC-1：上游通道不稳定 + 配置的模型已失效 【最高优先级，外部依赖】

#### 证据 A：模型可用性矩阵（2026-09-16 实测）

用生产环境的 base_url + key，对 6 个可用模型 × 3 种 response_format 各发一次请求：

| 模型 | 无 `response_format` | `json_object` | `json_schema` + strict |
|---|---|---|---|
| **`gpt-5.5`（当前配置）** | **502** | **502** | **502** |
| `gpt-5.6-luna` | ✅ 200 | ✅ 200 | ✅ 200 |
| `gpt-5.6-sol` | ✅ 200 | ✅ 200 | ✅ 200 |
| `gpt-5.6-terra` | 502 | 429 | 502 |
| `gpt-6-astra` | ✅ 200 | ✅ 200 | 502 |
| `codex-auto-review` | 429 | 502 | ✅ 200 |

**`gpt-5.5` 三种格式全部 502，错误信息为：**
```
{"error":{"message":"Upstream access forbidden, please contact administrator","type":"upstream_error"}}
```

#### 证据 B：同一请求在数秒内随机成功/失败

连续 6 次最小请求（`max_tokens: 8`，内容 `reply OK`）：
```
attempt 1: http=502  Upstream stream ended without a terminal response event
attempt 2: http=502  Upstream stream ended without a terminal response event
attempt 3: http=502  Upstream stream ended without a terminal response event
attempt 4: http=502  Upstream stream ended without a terminal response event
attempt 5: http=502  Upstream stream ended without a terminal response event
attempt 6: http=502  Upstream stream ended without a terminal response event
```

而同一时间换 `gpt-5.6-luna` 立刻 200。**说明不是整站挂掉，是特定模型通道坏掉。**

#### 证据 C：错误信息种类

抓到的上游错误有四种，全部来自中转站：
```
Upstream access forbidden, please contact administrator     (502)
Upstream stream ended without a terminal response event     (502)
Upstream request failed                                     (502)
Upstream rate limit exceeded, please retry later            (429)
```

#### 证据 D：中转站的 `/v1/models` 一直正常

```
http=200 time=0.65s
可用模型（仅 6 个）：
codex-auto-review, gpt-5.5, gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra, gpt-6-astra
```

**`/v1/models` 200 但 `/v1/chat/completions` 502** —— 认证与路由正常，是上游转发环节坏了。这解释了为什么早期排查会误判为"配置没问题"。

#### 证据 E：代理注入了约 4,400 tokens 的隐藏内容

```
一句话请求 "reply OK"  →  prompt_tokens = 4439
加一张 64×64 图片     →  prompt_tokens = 4449   （图片仅占 10 tokens）
加 json_schema        →  prompt_tokens = 4511
```

**这值得警惕**：代理在每次请求里注入了约 4,400 tokens 的隐藏系统提示。这可能是：
- 成本虚高的原因
- 模型不遵守网站 Skill 契约的干扰源（注入内容可能与 Aurora 契约冲突）

> ⚠️ 尚未验证注入内容的具体文本（代理不返回）。建议作为独立排查项。

#### 结论

**这是今天所有失败的直接原因。** 修复动作：
1. 立即把 `POLARIS_ANALYSIS_MODEL` 从 `gpt-5.5` 换成 `gpt-5.6-luna` 或 `gpt-5.6-sol`（两者实测全绿，且支持 strict schema + 视觉）
2. 给分析请求加**退避重试**（当前 `_planner_response_payload` 路径对 429/502/503 没有重试）
3. 考虑双模型故障转移（主 luna，备 sol）

---

### RC-2：图片格式校验器硬编码 JPEG —— 确定性缺陷，仍在生产 【最隐蔽】

#### 缺陷代码

`backend/app/xinghe_agent.py:163`
```python
IMAGE_FORMAT = "png"          # 请求 PNG
```

`backend/app/xinghe_agent.py:5612-5615`（在 `_artifact_from_bytes` 内）
```python
if image_format != "JPEG":    # ← 却要求 JPEG
    raise XingheAgentError(
        "IMAGE_RESPONSE_FORMAT_INVALID",
        "Provider did not return the requested JPEG format.",
    )
```

#### 为什么这个缺陷还在

commit `0b5ca7e fix(provider): request Artflow-compatible png output` **只改了请求格式，没改校验器**：

```diff
-IMAGE_FORMAT = "jpeg"
+# Artflow currently returns PNG artifacts even when the relay accepts a JPEG
+# request.  Request PNG explicitly so the provider response and our contract
+# agree instead of rejecting successful generations as format-invalid.
+IMAGE_FORMAT = "png"
```

**这个提交的意图没有实现**：
- 修改前：请求 JPEG → Artflow 返回 PNG → 校验器要 JPEG → 失败
- 修改后：请求 PNG → Artflow 返回 PNG → 校验器要 JPEG → **依然失败**

#### 影响范围（哪些路径会踩到）

`_artifact_from_bytes(..., preserve_provider_artifact: bool = False)` 在 `preserve_provider_artifact=True` 时**提前返回**，绕过检查。

| 调用方 | `preserve_provider_artifact` | 是否踩坑 |
|---|---|---|
| `aurora_main_image_provider.py:157, 365` | `True` | ✅ 绕过 |
| `xinghe_single_image_provider.py:509, 794` | `True` | ✅ 绕过 |
| **`xinghe_agent.py:3763`（通用图片编辑路径）** | **未设置 → False** | ❌ **必然失败** |

`xinghe_agent.py:3763` 构造 `ImageEditRequest` 时没有传 `preserve_provider_artifact`：
```python
image_request = ImageEditRequest(
    workspace_id=request.workspace_id,
    ...
    size=str(configuration["size"]),
)                      # ← 缺 preserve_provider_artifact=True
```

#### 当前状态：潜伏未触发

已确认的 6 次 `IMAGE_RESPONSE_FORMAT_INVALID` 全部发生在 **2026-09-16 01:44–01:55 UTC**，而 `0b5ca7e` 的提交时间是 **2026-09-16 01:59:08 UTC**。也就是说：
- 那 6 次是**旧代码**（`IMAGE_FORMAT="jpeg"`）踩的坑
- 部署新代码后，`detail_pack` 的任务都**卡在规划阶段**（因为 RC-1 上游挂了），根本没走到图片生成
- **所以这个缺陷现在是潜伏状态，一旦上游恢复就会立刻爆发**

#### 修复方案

```python
# backend/app/xinghe_agent.py:5612
if image_format != IMAGE_FORMAT.upper():
    raise XingheAgentError(
        "IMAGE_RESPONSE_FORMAT_INVALID",
        f"Provider did not return the requested {IMAGE_FORMAT.upper()} format.",
    )
```

同时建议：`xinghe_agent.py:3763` 补上 `preserve_provider_artifact=True`，或在格式不匹配时**自动转换**而不是直接失败（PNG→JPEG 转换是无损可做的）。

---

### RC-3：`checks` 的 Schema 与校验器不一致 —— 确定性设计缺陷

#### Schema 侧（宽松）

`backend/app/xinghe_agent.py:6334`，由 `_aurora_v20_visual_judgment_json_schema()` 生成：
```python
"checks": {"type": "array", "minItems": 1, "items": check}
```
其中 `check = {"anyOf": check_variants}`，29 个变体各带 `name: {"enum": [name]}`。

**问题**：`minItems: 1` + `anyOf` 只能表达"数组里每个元素是 29 种之一"，**无法表达"必须包含全部 29 项且顺序一致"**。所以一个**完全合规**的响应可以只返回 1 项。

#### 校验器侧（严格）

`backend/app/aurora_main_image_provider.py:956`（在 `_normalize_v20_visual_judgment_payload` 内）：
```python
if not isinstance(checks, list) or [
    item.get("name") for item in checks if isinstance(item, Mapping)
] != list(_VISUAL_CHECKS):
    raise AuroraPlanningError(
        "Aurora v2 checks must contain the complete ordered native check set"
    )
```

**要求 29 项且顺序完全一致。**

#### 实测复现

隔离测试（`gpt-5.6-luna`，2026-09-16）：

| 测试 | schema 规模 | HTTP | 结果 |
|---|---|---|---|
| A 仅 22 观察字段（无 anyOf） | 2,271 B | 429 | 限流（瞬时） |
| B 仅 29 anyOf checks | 10,776 B | 200 | `checks=29` ✅ |
| **C 观察字段 + anyOf checks** | 12,968 B | 200 | `observations=22/22`，**`checks=1`** ❌ |
| D 完整真实 schema | 14,280 B | 200 | `observations=22/22`，`checks=29` ✅ |

**C 组是关键证据**：模型返回了 1 项 check，这在 schema 下**完全合法**，但校验器会判失败。虽然实际生产里只出现了 1 次 `checks must contain the complete ordered native check set`（因为多数时候模型恰好返回全 29 项），但这是一个**随时会踩的确定性缺陷**。

#### 修复方案（二选一）

**方案 A（推荐）：收紧 schema**
用 `prefixItems` 把 29 项固定下来：
```python
"checks": {
    "type": "array",
    "minItems": 29,
    "maxItems": 29,
    "prefixItems": check_variants_in_order,   # 顺序固定
    "items": False,
}
```
> ⚠️ 需先确认 ooapi.cc 的上游是否支持 `prefixItems`（OpenAI 的 structured outputs 支持）。**必须先做兼容性探针再上线**，探针脚本见 §7。

**方案 B：放宽校验器**
把"顺序完全一致"改成"必须包含全部 29 项（顺序不限）"：
```python
names = [item.get("name") for item in checks if isinstance(item, Mapping)]
if sorted(names) != sorted(_VISUAL_CHECKS):
    raise AuroraPlanningError("Aurora v2 checks must contain the complete native check set")
```
这样既保留完整性要求，又不再依赖模型恰好按顺序输出。

**建议同时做**：A 提供硬保证，B 提供容错。并且**给缺失项自动补齐**（标记为 `passed=False` 或 `insufficient_evidence`），让一次响应异常不至于毁掉整页。

---

### RC-4：可观测性缺失 —— 三处关键失败点丢弃真实原因 【排查效率杀手】

这是本次审计**最花时间**的地方，也是为什么之前几轮排查都在猜。

#### 缺陷 4-1：规划失败原因被吞掉（影响 54 次）

`backend/app/execution.py:945-977`：
```python
def fail_domestic_main_image_planning(self, job_id: str, error: Exception) -> JobStatus | None:
    ...
    item.error_message = "国内电商主图规划失败，请重新提交任务。"
    item.error_details = {"reason": type(error).__name__}     # ← 只有类名！
    ...
    job.error_details = {"reason": type(error).__name__}
```

生产库实测：
```
job_f78d7d005df14fa891f82ee20d5db354 | page-004 | 国内电商主图规划失败，请重新提交任务。 | {"reason": "AuroraPlanningError"}
```

**54 次规划失败的真实原因全部丢失。** 调用点 `backend/app/execution.py:588`：
```python
except Exception as exc:
    if self._is_domestic_main_image_job(job_id):
        return self.fail_domestic_main_image_planning(job_id, exc)
    raise
```
没有 `logger.exception(...)`，worker 日志里也查不到（实测 `docker logs ... | grep -i AuroraPlanningError` 返回空）。

#### 缺陷 4-2：六页集合审计异常被静默吞掉（影响 32 次）

`backend/app/execution.py:661-665`：
```python
try:
    return bool(await audit(job_id))
except Exception:
    return False        # ← 异常完全静默
```

`backend/app/execution.py:686`：
```python
item.error_details = {"retryable": False, "scope": "set_audit"}   # ← 没有原因
```

**32 次 `AURORA_SET_AUDIT_FAILED` 的真实原因全部丢失。**

#### 缺陷 4-3：原始响应内容不落库

`backend/app/aurora_main_image_provider.py:1515` `_safe_planner_diagnostics()`：
```python
return {
    "provider_request_id_presence": ...,
    "http_status": ...,
    "parse_trace": ...,
    "structure_fingerprint": ...,
    "structure_profile": ...,
    "provider_envelope": ...,
    "content_utf8_bytes": ...,
    "content_sha256": ...,
    ...
}   # ← 刻意不保存原始内容
```

注释说明是**有意为之**（避免存 prompt / 密钥）：
```python
# The execution layer persists this under metrics and never includes the
# raw response body, prompt, authorization header or API key.
```

但结果是：**我们永远无法知道模型到底返回了什么**。比如 `whole_json:decode_failed` 时，只能知道"解析失败"，不知道失败在哪。

#### 修复方案

1. **加有界原始内容采样**（安全前提下）：
```python
def _safe_planner_diagnostics(result, *, content_sample: str | None = None):
    ...
    if content_sample:
        # 只存头部，且脱敏
        diagnostics["content_sample_head"] = content_sample[:512]
        diagnostics["content_sample_tail"] = content_sample[-256:]
```
> 建议加开关 `POLARIS_DIAGNOSTIC_CONTENT_SAMPLE=1`，默认关闭，排查时开启。

2. **落库异常消息**：
```python
item.error_details = {
    "reason": type(error).__name__,
    "message": str(error)[:512],        # ← 新增
    "diagnostics": getattr(error, "aurora_gateway_diagnostics", None),   # ← 新增
}
```

3. **捕获处加日志**：
```python
except Exception as exc:
    logger.exception("domestic main image planning failed: job_id=%s", job_id)
    ...
```

4. **集合审计不要吞异常**：
```python
except Exception as exc:
    logger.exception("aurora set audit failed: job_id=%s", job_id)
    _record_set_audit_error(job_id, exc)
    return False
```

---

### RC-5：JSON 解析失败被错误归类为"图片审核失败"

#### 缺陷机制

`backend/app/xinghe_agent.py:6728` `_planner_content_payload_with_trace()`：解析失败时**返回原始字符串作为 payload，且不设置 error_code**：
```python
except json.JSONDecodeError:
    match = _PLANNER_JSON_FENCE.fullmatch(stripped)
    if match is None:
        return content, ("whole_json:decode_failed", "fence:not_matched")
        #     ^^^^^^^ 返回字符串，error_code 仍为 None
```

下游 `backend/app/aurora_main_image_provider.py:603`：
```python
if result.error_code or not isinstance(result.payload, Mapping):
    raise _v20_gateway_error(
        result,
        result.error_message or "Aurora v2.0 pixel audit failed",   # ← 通用文案
    )
```
因为 `result.error_message` 是 `None`（HTTP 200），所以用了兜底文案。

#### 实测证据

`job_6b76c197497a43ada1902b66e5387ede` 的 `job_items.error_details`：

```json
{
  "retryable": false, "http_status": null, "provider_outcome": "failed",
  "aurora_gateway_diagnostics": {
    "http_status": 200,
    "parse_trace": ["whole_json:decode_failed", "fence:not_matched"],
    "content_sha256": "edec5b286d213f1d6d37bd197dac012588698effab9d3cace75318128a7f3cbd",
    "usage_presence": "present", "usage_completeness": "complete",
    "request_id_presence": "present", "provider_request_id_presence": "present",
    "provider_envelope": {"choice0_kind":"mapping","choices_kind":"list",
                          "content_kind":"string","message_kind":"mapping",
                          "choices_count":1,"envelope_kind":"mapping"},
    "content_utf8_bytes": 2774, "structure_fingerprint": "84120b69…"
  }
}
```

**关键点**：
- `http_status: 200` —— 上游正常返回
- `usage_completeness: "complete"` —— 计费数据完整，说明不是被截断
- `content_utf8_bytes: 2774` —— 远小于 `max_completion_tokens: 16000`，**没有被 token 限制截断**
- `parse_trace: decode_failed + fence:not_matched` —— 内容既不是纯 JSON，也不是 fenced JSON

**结论**：中转站返回了 200，但模型输出的内容不是合法 JSON。错误信息 `"Aurora v2.0 pixel audit failed"` 会让人误以为是**图片质量不合格**，实际是**解析失败**。

#### 修复方案

让解析失败直接产出明确的 error_code：
```python
except json.JSONDecodeError:
    ...
    if match is None:
        return content, ("whole_json:decode_failed", "fence:not_matched")
```
改为在 `plan()` 里判断解析结果类型：
```python
plan_payload, parse_trace = _planner_content_payload_with_trace(text)
if not isinstance(plan_payload, Mapping):
    return PlannerCallResult(
        payload=None,
        error_code="PLANNER_SCHEMA_INVALID",      # ← 明确归类
        error_message="Planner content was not a JSON object.",
        parse_trace=parse_trace,
        http_status=status,
        content_utf8_bytes=len(text.encode("utf-8")),
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ...
    )
```
同时把 `aurora_main_image_provider.py` 里的兜底文案改成分情况说明。

---

### RC-6：重试机制存在，但恰好在上游 502 时被绕过 【高价值发现】

#### 缺陷 6-1：`http_status` 没有从 `PlannerCallResult` 传到异常上

`backend/app/aurora_main_image_planner.py:58`：
```python
class AuroraPlanningError(RuntimeError):
    aurora_gateway_diagnostics: dict[str, Any] | None = None
    # ← 没有 http_status 属性
```

`backend/app/aurora_main_image_provider.py:1509` `_v20_gateway_error()`：
```python
def _v20_gateway_error(result: Any, message: str) -> AuroraPlanningError:
    error = AuroraPlanningError(message)
    error.aurora_gateway_diagnostics = _safe_planner_diagnostics(result)
    return error                      # ← http_status 只进了 diagnostics，没进 error
```

`backend/app/aurora_main_image_provider.py:1888` `_classify_transient_failure()`：
```python
def _classify_transient_failure(exc: Exception) -> tuple[bool, int | None]:
    status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
    http_status = status if isinstance(status, int) else None    # ← 恒为 None
    if http_status in {408, 429} or (http_status is not None and 500 <= http_status <= 599):
        return True, http_status                                  # ← 永远走不到
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True, http_status
    if isinstance(exc, OSError) and not isinstance(exc, (FileNotFoundError, PermissionError)):
        return True, http_status
    message = str(exc).casefold()
    transient_markers = (
        "temporary unavailable", "temporarily unavailable",
        "connection reset", "connection refused",
        "timed out", "timeout", "rate limit",
    )
    return any(marker in message for marker in transient_markers), http_status
```

**问题链条**：上游返回 502 时
1. `PlannerCallResult.error_message = "Planner request failed with HTTP 502."`，`http_status = 502`
2. `_v20_gateway_error` 把它包成 `AuroraPlanningError("Planner request failed with HTTP 502.")`，**没设 `error.http_status`**
3. `_classify_transient_failure` 里 `getattr(exc, "http_status", None)` → `None`
4. `None` 不在 `{408, 429}`，也不是 5xx → 跳过
5. 不是 `TimeoutError` / `ConnectionError` / `OSError` → 跳过
6. `message.casefold()` = `"planner request failed with http 502."`
   → 与 7 个 marker（`temporary unavailable` / `connection reset` / `rate limit` …）**一个都不匹配**
7. **返回 `(False, None)` → `retryable = False`**

#### 生产库证据

所有 Aurora 失败的 `error_details` 都是：
```json
{"retryable": false, "http_status": null, "provider_outcome": "failed", ...}
```

即使错误信息明确写着 `Planner request failed with HTTP 502.`，`retryable` 依然是 `false`、`http_status` 依然是 `null`。

#### 影响

生产环境**已经配置了重试**：
```
POLARIS_AURORA_MAX_TRANSIENT_RETRIES=2
POLARIS_AURORA_RETRY_BACKOFF_BASE_SECONDS=2
POLARIS_AURORA_RETRY_BACKOFF_MAX_SECONDS=30
POLARIS_AURORA_EXECUTION_MODE=parallel_v20
```

`backend/app/execution.py:1571-1607` 也有完整的重试调度（`schedule = (1.0, 2.5, 7.5)` + jitter + `RETRY_WAIT` 状态）。

但因为 `result.retryable` 恒为 `False`，**这套重试机制在最需要它的时候（上游 429/502/503）完全不会触发**。

#### 缺陷 6-2：审计异常直接冒泡，不做页内重试

`backend/app/aurora_main_image_provider.py:309` 的 `_execute_parallel_v20`：
```python
for visual_attempt in range(2):          # 只有 2 次视觉尝试
    ...
    judgment, usage, cost = await self._audit_v20_candidate(...)   # ← 异常直接冒泡
    ...
    if current.get("state") == "REPAIR_REQUIRED" and visual_attempt == 0:
        ...  # 进入修复流程
```
`_audit_v20_candidate` 抛出的 `AuroraPlanningError` **没有被捕获**，直接冒泡出循环 → 整个 page item 失败。图片生成有重试，审计没有。

#### 修复方案

**第一步（1 行，立即见效）**：把 `http_status` 挂到异常上
```python
# backend/app/aurora_main_image_provider.py:1509
def _v20_gateway_error(result: Any, message: str) -> AuroraPlanningError:
    error = AuroraPlanningError(message)
    error.aurora_gateway_diagnostics = _safe_planner_diagnostics(result)
    error.http_status = getattr(result, "http_status", None)      # ← 新增
    return error
```
同时给异常类加声明：
```python
# backend/app/aurora_main_image_planner.py:58
class AuroraPlanningError(RuntimeError):
    aurora_gateway_diagnostics: dict[str, Any] | None = None
    http_status: int | None = None                                # ← 新增
```

**第二步**：补充 message 级兜底（防止 `http_status` 仍丢失）
```python
# backend/app/aurora_main_image_provider.py:1888
transient_markers = (
    "temporary unavailable", "temporarily unavailable",
    "connection reset", "connection refused",
    "timed out", "timeout", "rate limit",
    "failed with http 408", "failed with http 429",
    "failed with http 500", "failed with http 502",
    "failed with http 503", "failed with http 504",      # ← 新增
)
```
> 更稳妥的做法是用正则 `re.search(r"failed with http (408|429|5\d\d)", message)`。

**第三步**：把审计调用纳入页内重试（只重试审计，**不重新生成图片**，避免浪费成本）
```python
for visual_attempt in range(2):
    ...
    try:
        judgment, usage, cost = await self._audit_v20_candidate(...)
    except AuroraPlanningError as exc:
        transient = getattr(exc, "http_status", None) in {408, 429, 500, 502, 503, 504}
        if transient and visual_attempt == 0:
            await asyncio.sleep(2.5)
            continue
        raise
```

---

### RC-7：部分任务回落到串行路径，撞上"必须严格按序生成"的检查（4 次）

`backend/app/aurora_main_image_provider.py:113` 的分支：
```python
async def execute(self, request: ProviderRequest) -> ProviderResult:
    if self.execution_mode == "parallel_v20" or _request_uses_parallel_v20(request):
        return await self._execute_parallel_v20(request)
    # ↓ 串行路径
    item, harness_session = self._load_authorized_context(request)
```

`backend/app/aurora_main_image_provider.py:1880` `_request_uses_parallel_v20()` 会检查 **item 级配置**：
```python
def _request_uses_parallel_v20(request: ProviderRequest) -> bool:
    ...
    item.get("aurora_execution_mode") == "parallel_v20"      # ← item 配置里必须带这个标记
```

而串行路径 `_load_authorized_context`（`aurora_main_image_provider.py:1295`）有这条检查：
```python
accepted = stored.get("accepted_page_indexes", [])
if item.ordinal != len(accepted):
    raise AuroraPlanningError("Aurora pages must be generated strictly in order")
```

**这个检查与并发设计天然冲突**：6 个页面同时启动时 `accepted_page_indexes` 长度是动态变化的，只有 ordinal 恰好等于当前已接受页数的那个才能通过。

生产环境已设置 `POLARIS_AURORA_EXECUTION_MODE=parallel_v20`（全局），所以这 4 次失败说明：**存在一批 job，其 item 配置里没有 `aurora_execution_mode: parallel_v20`**（可能是早期创建的任务、或重试任务、或某条创建路径漏写），于是回落到串行路径并被这条检查拦下。

#### 排查建议
```sql
-- 找出 item 配置里缺少 parallel 标记的国内电商主图任务
select job_id, item_key, status, error_code, error_message
from job_items
where error_code = 'AURORA_EXECUTION_FAILED'
  and error_message like '%strictly in order%';
```
然后回溯这些 job 的 `jobs.configuration` 与 `job_items` 快照，确认是哪条创建路径没有写入 `aurora_execution_mode`。

#### 修复方向
1. 统一创建路径，确保所有 Aurora 主图任务的 item 快照都带 `aurora_execution_mode: parallel_v20`
2. 或者把这条"严格按序"检查改成仅对 `sequential_v017` 模式生效（显式判断模式，而不是靠配置缺失来兜底）

---

### RC-8：历史高失败率基线

从 §3.4 时间线可见，`AURORA_EXECUTION_FAILED` 最早出现在 **2026-08-17**，`AURORA_SET_AUDIT_FAILED` 最早 **2026-08-21**——**远早于本次 API 切换（2026-09-15）**。

**这意味着**：本次 API 切换**不是**引入问题，而是**叠加**在已有的高失败率上。修完 RC-1~RC-7 之后，如果失败率仍显著高于 0%，需要继续追查 Aurora v2.0 契约本身的合理性（例如 29 项检查 / 18 项材料性阻断 / 22 个观察字段是否过于严苛）。

---

## 5. 排除项：不是这些原因（避免重复排查）

| 假设 | 实测结论 | 证据 |
|---|---|---|
| 中转站不支持 `json_schema` | **支持，且严格强制执行** | 用探针 schema 要求 `zzz_probe_field` 枚举为 `MAGIC_TOKEN_42`，模型自发产不出该值，但响应确实包含它 → 见 §7.1 |
| schema 太大被拒 | **不是**。14,280 B 完整 schema 返回 200 | 隔离测试 D 组：`observations=22/22, checks=29`（见 §4 RC-3） |
| 模型没有视觉能力 | **有**。luna/sol/astra 都能正确识别测试图颜色 | 64×64 红色方块测试，三者均返回 `{"dominant_color":"red","is_square":true}` |
| 并发没开、一张张生成 | **并发正常**。生产 `POLARIS_XINGHE_CONCURRENCY=6`，6 页 `started_at` 相差 <0.1s | 09:44:48.529 / .550 / .565 / .605 / .618 / .633 |
| 密钥/鉴权有问题 | **正常**。`/v1/models` 稳定 200 | 见 §4 RC-1 证据 D |
| 是"图片质量审核不通过" | **大部分不是**。56% 是契约不符、31% 是 HTTP 错误 | 见 §3.3 |

---

## 6. 修复方案（按优先级）

### P0 — 立即执行（恢复可用性）

| # | 动作 | 文件 / 位置 | 验收 |
|---|---|---|---|
| 1 | 把 `POLARIS_ANALYSIS_MODEL` 改为 `gpt-5.6-luna`（备选 `gpt-5.6-sol`） | 生产 `.env` + `compose.production.yml` | 连发 10 次分析请求，成功率 ≥ 90% |
| 2 | 修正格式校验器硬编码 | `backend/app/xinghe_agent.py:5612` | 详见 RC-2 |
| 3 | 让 `http_status` 传到异常上，恢复重试 | `aurora_main_image_provider.py:1509` + `aurora_main_image_planner.py:58` | 详见 RC-6 第一步 |

### P1 — 本迭代内完成（消除确定性缺陷）

| # | 动作 | 文件 / 位置 |
|---|---|---|
| 4 | 修 `checks` schema / 校验器不一致 | `xinghe_agent.py:6334` + `aurora_main_image_provider.py:956` |
| 5 | 解析失败返回明确 error_code | `xinghe_agent.py:6728` + `aurora_main_image_provider.py:603` |
| 6 | 审计异常纳入页内重试 + message 级瞬时判定兜底 | `aurora_main_image_provider.py:309` / `:1888` |
| 7 | 统一 Aurora 主图任务创建路径的 `aurora_execution_mode` 标记 | 任务创建路径 + `aurora_main_image_provider.py:1880` |

### P2 — 补齐可观测性（让下次排查不用再猜）

| # | 动作 | 文件 / 位置 |
|---|---|---|
| 7 | 落库异常 message + diagnostics | `execution.py:972`、`execution.py:686` |
| 8 | 捕获处加 `logger.exception` | `execution.py:588`、`execution.py:661` |
| 9 | 加可选原始内容采样（默认关） | `aurora_main_image_provider.py:1515` |

### P3 — 结构性改进（可选）

| # | 动作 |
|---|---|
| 10 | 分析通道双模型故障转移（luna 主 / sol 备） |
| 11 | 图片格式不匹配时自动转码，而不是直接失败 |
| 12 | 调查代理注入的 ~4,400 tokens 隐藏提示词是否干扰 Skill 契约 |
| 13 | 复盘 Aurora v2.0 契约严格度（29 检查 / 18 材料性阻断 / 22 观察字段） |

---

## 7. 附录：探针脚本（可直接复制到生产服务器运行）

### 7.0 通用准备

```bash
# 在服务器上导出分析通道环境变量（不回显）
ssh -i <key.pem> ubuntu@43.129.198.147 \
  "docker exec polaris-production-api-1 env | grep '^POLARIS_ANALYSIS' > /tmp/penv"
# 之后用：set -a; . /tmp/penv; set +a
```

### 7.1 探测代理是否真正执行 strict schema（关键探针）

原理：要求 schema 必须包含 `zzz_probe_field` 且枚举值只能是 `MAGIC_TOKEN_42`。这个值模型**不可能自发产生**，所以如果响应里出现了它，就证明是服务端在强制执行 schema。

```python
import json, os, urllib.request, urllib.error
base = os.environ["POLARIS_ANALYSIS_BASE_URL"].rstrip("/")
key  = os.environ["POLARIS_ANALYSIS_API_KEY"]
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["zzz_probe_field", "color"],
          "properties": {"zzz_probe_field": {"type": "string", "enum": ["MAGIC_TOKEN_42"]},
                         "color": {"type": "string", "enum": ["red", "green", "blue"]}}}
for model in ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.5"]:
    p = {"model": model,
         "messages": [{"role": "user", "content": "Describe a bright red apple. Reply with JSON."}],
         "response_format": {"type": "json_schema",
                             "json_schema": {"name": "probe", "strict": True, "schema": SCHEMA}},
         "max_tokens": 200}
    r = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(p).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            body = resp.read().decode()
        c = json.loads(body)["choices"][0]["message"]["content"]
        got = json.loads(c)
        print(model, "ENFORCED" if got.get("zzz_probe_field") == "MAGIC_TOKEN_42" else "NOT_ENFORCED", got)
    except urllib.error.HTTPError as e:
        print(model, "HTTP", e.code, e.read().decode()[:120])
```

实测结果（2026-09-16）：
```
gpt-5.6-luna   ENFORCED  {"color": "red", "zzz_probe_field": "MAGIC_TOKEN_42"}
gpt-5.6-sol    ENFORCED  {"color": "red", "zzz_probe_field": "MAGIC_TOKEN_42"}
gpt-5.5        HTTP 502  Upstream access forbidden, please contact administrator
```

### 7.2 模型可用性矩阵

```python
import json, os, urllib.request, urllib.error, time
base = os.environ["POLARIS_ANALYSIS_BASE_URL"].rstrip("/")
key  = os.environ["POLARIS_ANALYSIS_API_KEY"]
MODELS = ["gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra", "codex-auto-review"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok"],
          "properties": {"ok": {"type": "boolean"}}}
def call(model, rf):
    p = {"model": model, "messages": [{"role": "user", "content": "reply with ok:true"}], "max_tokens": 50}
    if rf: p["response_format"] = rf
    r = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(p).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp: return resp.status, resp.read().decode()[:100]
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:100]
    except Exception as e: return "ERR", str(e)[:100]
for m in MODELS:
    row = []
    for label, rf in [("none", None), ("json_object", {"type": "json_object"}),
                      ("json_schema", {"type": "json_schema",
                                       "json_schema": {"name": "p", "strict": True, "schema": SCHEMA}})]:
        st, body = call(m, rf)
        row.append("%s=%s" % (label, st))
    print("%-18s %s" % (m, "  ".join(row)))
```

### 7.3 生成真实 Aurora 审核 schema（用于隔离测试）

```bash
cd D:\Projects\polaris-commerce-studio-model-pose-entrypoints\backend
python -c "
import json,re,typing
src=open('app/xinghe_agent.py',encoding='utf-8').read()
ns={'Any':typing.Any,'Mapping':typing.Mapping}
for pat in [r'^_AURORA_V20_VISUAL_CHECKS = \(.*?\n\)',
            r'^_AURORA_V20_MATERIAL_CHECKS = frozenset\(.*?\n\)',
            r'^def _aurora_v20_visual_judgment_json_schema\(.*?\n(?=\ndef )']:
    exec(re.search(pat,src,re.S|re.M).group(0), ns)
sch=ns['_aurora_v20_visual_judgment_json_schema'](None)
s=json.dumps(sch,ensure_ascii=False)
print('schema bytes:', len(s))
print('observations required:', len(sch['properties']['observations']['required']))
open('real_schema.json','w',encoding='utf-8').write(s)
"
```
实测：**14,280 字节**，22 个必填 observation 字段。

### 7.4 生产库常用查询

```sql
-- 失败错误码聚合
select error_code, count(*) cnt, count(distinct job_id) jobs,
       min(finished_at)::date first_seen, max(finished_at)::date last_seen
from job_items where status='failed' group by error_code order by cnt desc;

-- 分业务类型失败率（近 30 天）
select j.type,
       count(*) filter (where ji.status='succeeded') ok,
       count(*) filter (where ji.status='failed') bad,
       count(*) total,
       round(100.0*count(*) filter (where ji.status='failed')/nullif(count(*),0),1) fail_pct
from job_items ji join jobs j on j.id=ji.job_id
where ji.created_at > now() - interval '30 days'
group by j.type order by total desc;

-- 查看某次 Aurora 失败的完整诊断
select job_id, item_key, error_code, error_message, error_details
from job_items
where status='failed' and error_details::text like '%aurora_gateway_diagnostics%'
order by finished_at desc limit 10;
```

### 7.5 `aurora_gateway_diagnostics` 字段速查

| 字段 | 含义 | 排查价值 |
|---|---|---|
| `http_status` | 上游 HTTP 状态 | **200 = 上游正常，问题在输出内容** |
| `parse_trace` | 解析轨迹 | `whole_json:decode_failed` = 内容不是合法 JSON |
| `content_utf8_bytes` | 内容字节数 | 远小于 `max_completion_tokens` 说明**未被截断** |
| `content_sha256` | 内容哈希 | 可跨请求比对是否同一响应 |
| `usage_presence` / `usage_completeness` | 计费数据完整性 | `complete` 说明响应完整 |
| `structure_fingerprint` | 结构指纹 | **相同指纹 = 相同 JSON 结构**（可用于区分"结构错"vs"值错"） |
| `provider_envelope` | 响应外层结构 | `content_kind: string` 说明 content 是字符串而非对象 |
| `request_id_presence` | 是否有请求 ID | 用于向中转站投诉时定位 |

---

## 8. 验收标准

修完后必须用**真实计费任务**验收（用户已授权真实生成测试，额度不限）：

1. **连通性**：连续 10 次分析请求成功率 ≥ 90%（当前 `gpt-5.5` 为 0%）
2. **端到端**：`domestic_main_image` 提交 3 次，至少 2 次得到 `completed` 或 `partial`（非全页失败）
3. **详情页**：`detail_pack` 提交 2 次，页面图片不再出现 `IMAGE_RESPONSE_FORMAT_INVALID`
4. **契约**：`job_items` 中不再出现 `Aurora v2 observations are incomplete` / `checks must contain the complete ordered native check set`
5. **可观测性**：任意一次失败都能从 `job_items.error_details` 读到**具体原因**（不是 `{"reason": "AuroraPlanningError"}`）
6. **失败率**：`domestic_main_image` 与 `detail_pack` 的 7 日失败率降到 **< 15%**

验收查询：
```sql
select j.type, count(*) filter (where ji.status='failed') bad, count(*) total,
       round(100.0*count(*) filter (where ji.status='failed')/nullif(count(*),0),1) fail_pct
from job_items ji join jobs j on j.id=ji.job_id
where ji.created_at > now() - interval '7 days'
group by j.type order by total desc;
```

---

## 9. 已知遗留风险

1. **中转站不可控**：`ooapi.cc` 的错误（429/502/503）在应用层无法根治，只能靠重试 + 故障转移 + 监控。建议加告警：分析通道 5 分钟成功率 < 80% 时通知。
2. **`gpt-5.5` 通道持续 502**：如果业务上必须用 `gpt-5.5`，需要联系中转站管理员。否则建议永久切到 luna/sol。
3. **代理注入 ~4,400 tokens**：未验证内容，可能影响契约遵守与成本。建议单独排查。
4. **Aurora v2.0 契约严格度**：29 项检查 / 18 项材料性阻断 / 22 个观察字段，对模型的输出稳定性要求极高。修完工程缺陷后若失败率仍高，需评估契约本身是否需要放宽。
5. **`AURORA_SET_AUDIT_FAILED`（32 次）根因未知**：当前代码吞掉了异常，必须先在 RC-4 修复后重新采集才能定位。
6. **旧任务不会自动恢复**：修复部署后，历史失败任务需要在页面上点「按原方案重新生成」才会用新逻辑重跑。

---

## 10. 相关提交记录

```
cdb02b4 fix(quota): settle legacy usage without cost          ← 审计时 HEAD
789d447 fix(quota): retain conservative cost on provider rejection
0b5ca7e fix(provider): request Artflow-compatible png output  ← ⚠️ 修复不完整，见 RC-2
8f54e9c fix(provider): use stable analysis model              ← 切到 gpt-5.5，现已失效
234ca70 fix(provider): use configured image model for masked edits
d50a83c feat(provider): split analysis and image APIs         ← 双通道拆分
```
