# Polaris Commerce Studio 1.2 第二修复独立离线验收报告

## 结论

**离线候选通过；真实链路仍未通过/未复测**

严重级别计数：

- P0：0
- P1：0
- P2：0

本结论只适用于第二修复候选的独立离线复验。它不是新的真实 Tengli
结果，也不得解释为真实 Provider 链路已经通过。

## 提交身份与范围

第二修复候选：

`16273677fce36371e14b003b0f2cf11609b5f491`

核验结果：

- 提交对象存在。
- 唯一父为
  `fba5313e9b901c05373fd8fa0c726c336c69575f`。
- 候选仅修改：
  - `backend/app/xinghe_agent.py`
  - `backend/tests/test_xinghe_agent.py`
- 开始复验前工作树为 detached HEAD 且干净。
- 验证窗口没有修改产品代码、配置、迁移或前端。

独立机器证据提交：

`3cecacb9786d963d867a8d354416b848073a4767`

该提交唯一父为第二修复候选
`16273677fce36371e14b003b0f2cf11609b5f491`，仅包含：

- `docs/verification/tools/test_1627367_planner_json_boundary.py`
- `docs/verification/1627367-planner-json-boundary-independent-evidence.json`

## 实现审计

候选实现满足冻结的解析边界：

- 先对剥离外部空白后的整串内容执行完整 `json.loads`。
- 完整 JSON object 可接受；scalar 与 array 由严格 schema gate 拒绝。
- fenced 内容必须完整匹配唯一 fence。
- fence 外只允许被外层 `strip` 消费的空白。
- fence 前后非空文本均被拒绝。
- 第二个 fence 或多个完整对象均被拒绝。
- 不抓取第一个 `{...}` 片段。
- 不存在静态或模板 fallback。
- 支持无标签 fence。
- `json` 标签大小写不敏感。
- 支持 opening fence 后的空格、tab，以及 LF/CRLF。
- 坏 fence、未闭合 fence、缺字段与非精确六页均返回
  `PLANNER_SCHEMA_INVALID`。

核心 fence 约束为：

```text
\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z
```

## 隔离与运行环境

复验复用了主项目现有隔离环境：

`C:\Users\Administrator\Documents\初步网站建立\.venv`

环境信息：

- Python：3.13.14
- pytest：9.1.1
- Ruff：0.16.0
- Mypy：1.20.2
- 依赖声明依据：`backend/pyproject.toml`
- 本轮未下载或安装依赖，因此没有依赖安装命令。
- 运行时设置 `PYTHONDONTWRITEBYTECODE=1`，避免修改被复用环境。

独立夹具的安全约束：

- `Settings(_env_file=None, ...)`
- 每个 case 使用临时 SQLite 数据库。
- 每个 case 使用临时本地存储。
- 注入 in-process 离线 transport。
- 外部 socket 连接被阻断。
- 仅允许 TestClient/asyncio 所需的本机 loopback socketpair。
- 未执行计划确认。
- 图片 transport 若被调用会立即触发断言失败。

没有读取或复制 `.env`、密钥、真实响应体或敏感 header。没有访问真实
Provider，也没有执行真实烟测。

## 独立边界测试

独立测试路径：

`docs/verification/tools/test_1627367_planner_json_boundary.py`

最终结果：

- 22 passed
- 0 failed
- 0 errors
- 0 skipped

四个合法 case 全部通过：

1. 带外部空白的完整 JSON object。
2. LF 的 `json` fence。
3. 混合大小写 `JsOn` 标签、水平空白及 CRLF。
4. 无标签 fence、水平空白、LF 及外部空白。

四个合法 case 均：

- 停在 `awaiting_confirmation`。
- 请求页数为 6。
- 生成六页有效 plan。
- planner 请求为 1。
- image 请求为 0。
- reported usage 为 true。
- total tokens 为 150。
- planner actual cost 为 null。
- job actual cost 为 null。
- budget limit 为 200,000,000 micros，即 USD 200。
- 未执行确认，因此有效确认前 image 仍为 0。

十八个拒绝 case 全部通过：

1. 普通非 JSON 文本。
2. 完整 JSON scalar。
3. 完整 JSON array。
4. 两个完整 JSON object。
5. 合法 object fence 后追加第二个 scalar fence。
6. 合法 fence 前追加非空文本。
7. 合法 fence 后追加非空文本。
8. 两个合法 object fence。
9. 未闭合 fence。
10. 坏 closing fence。
11. 缺少必需字段。
12. 非精确六页。
13. fenced JSON scalar。
14. fenced JSON array。
15. 不支持的 fence language tag。
16. opening fence 与 body 位于同一行。
17. 完整 object 后追加 scalar。
18. 完整 object 前添加 scalar。

十八个拒绝 case 均：

- 状态为 `failed`。
- 错误码为 `PLANNER_SCHEMA_INVALID`。
- 请求页数与 expected item count 均保持 6。
- 没有形成可确认页面。
- planner 请求为 1。
- image 请求为 0。
- reported usage 为 true。
- total tokens 为 150。
- planner actual cost 与 job actual cost 均为 null。
- 配置预算保持 USD 200。
- 拒绝终态 job API request count 为 1。
- 拒绝终态 usage completeness 为 `complete`。

## v5 三个精确回归

以下三个 v5 失败场景均已独立重验并通过：

1. 合法 object fence 后追加第二个 scalar fence。
2. fence 前非空文本。
3. fence 后非空文本。

结果：3 passed、0 failed。

三者实际共同结果：

```text
status: failed
error_code: PLANNER_SCHEMA_INVALID
requested_page_count: 6
planned_page_count: 0
planner_request_count: 1
image_request_count: 0
usage_reported: true
total_tokens: 150
actual_cost_micros: null
configured_budget_limit_micros: 200000000
job_api_request_count: 1
```

三者不再进入 `awaiting_confirmation`。

## API 请求计数时点

独立复验明确核对了两个不同状态的计数语义：

- 合法规划停在确认前时，plan payload 已记录 planner request count 为 1；
  顶层 job API request count 仍为 0，usage completeness 为 `none`。
- 非法规划进入拒绝终态时，plan payload 的 planner request count 为 1；
  顶层 job API request count 为 1，usage completeness 为 `complete`。

这与候选现有状态机的持久化时点一致。两条路径的 image request count 均为
0，且 actual cost 均保持未知值 null。

## 候选测试与完整模块

候选定向边界测试：

- 14 passed
- 0 failed
- 0 errors
- 0 skipped

范围：

```text
strict_planner_content_reaches_confirmation_gate
or unsafe_planner_content_fails_closed_before_image
```

完整 `backend/tests/test_xinghe_agent.py`：

- 42 passed
- 0 failed
- 0 errors
- 2 skipped

两个 skip 均为既有 PostgreSQL 条件测试，需要显式
`POLARIS_TEST_DATABASE_URL`。本轮没有读取环境文件或数据库 URL，也没有将
skip 计为候选失败。

按规划要求，没有重跑既有独立 106 项。

## 静态与结构检查

- Ruff：PASS
  - `backend/app/xinghe_agent.py`
  - `backend/tests/test_xinghe_agent.py`
  - 独立测试文件
- Mypy：PASS，21 个 `backend/app` 源文件。
- 候选提交 `git diff --check`：PASS。
- 证据提交前工作树 `git diff --check`：PASS。
- 证据 staged diff check：PASS。
- 独立测试与机器证据常见密钥字面量扫描：PASS，0 个候选。

## 复验过程说明

系统默认 Python 缺少 pytest，这被正确识别为环境问题而非候选失败。随后
找到主项目已有隔离 `.venv` 并复用，无需下载。

独立夹具校准期间出现过三类验证器问题：

1. 初始 socket 护栏误拦截 Windows asyncio 的 loopback socketpair，应用尚未
   执行。
2. 首次进入状态机后，合法确认前路径暴露了验证器对顶层 API 计数时点的错误
   预期。
3. 一次期望值编辑临时同时改动两个 accounting block，随后在最终运行前纠正。

这些均为独立测试夹具或期望问题，没有作为候选代码失败。最终权威独立运行是
22 passed。

## 历史真实烟测边界

唯一一次真实 Tengli 烟测仍是产品提交
`33cd90ec289d985c027fd78805e3631127640370` 上的 P1 失败：

- HTTP 200。
- planner 内容不是所需 object。
- 产品错误码 `PLANNER_SCHEMA_INVALID`。
- 1 planner / 0 image。
- 没有重跑。

本轮没有执行新的真实 Tengli 复测。第二修复候选仅完成离线验收。

## 发布边界

- 未请求或读取任何密钥。
- 未访问真实 Tengli 或其他 Provider。
- 未执行真实烟测。
- 未推送。
- 未合并。
- 未部署。

最终结论：

**离线候选通过；真实链路仍未通过/未复测**
