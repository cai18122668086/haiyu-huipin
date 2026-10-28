# Phase 1.1 前端视觉基线

保存时间：2026-07-25  
代码基线：`2cfdb73`（`phase-1.1-baseline`）  
分支：`codex/frontend-ui-refresh`

## 覆盖范围

下列 7 个现有路由均已保存 1440 × 1000 桌面端与 390 × 844 移动端全页截图：

| 页面 | 路由 | 桌面端 | 移动端 |
| --- | --- | --- | --- |
| AI 功能大全 | `/` | [desktop/home.png](desktop/home.png) | [mobile/home.png](mobile/home.png) |
| 个人控制台 | `/dashboard` | [desktop/dashboard.png](desktop/dashboard.png) | [mobile/dashboard.png](mobile/dashboard.png) |
| 个人素材库 | `/assets` | [desktop/assets.png](desktop/assets.png) | [mobile/assets.png](mobile/assets.png) |
| 生成记录 | `/jobs` | [desktop/jobs.png](desktop/jobs.png) | [mobile/jobs.png](mobile/jobs.png) |
| 使用帮助 | `/help` | [desktop/help.png](desktop/help.png) | [mobile/help.png](mobile/help.png) |
| 局部修改 | `/workspace/local-edit` | [desktop/local-edit.png](desktop/local-edit.png) | [mobile/local-edit.png](mobile/local-edit.png) |
| 商品详情图 | `/workspace/detail-pack` | [desktop/detail-pack.png](desktop/detail-pack.png) | [mobile/detail-pack.png](mobile/detail-pack.png) |

## 基线审计结论

### 保留

- 两条核心工作流已经具备清楚的任务阶段与示例数据。
- 素材、任务、工作台之间已有交叉入口。
- `draft / queued / planning / processing / partial / completed / failed / cancelled`
  状态语义完整，继续沿用，不增加前端自定义任务状态。
- 商品详情图已有“先确认规划、再开始生成”的结构。

### 需要改进

- 顶部导航、左侧导航和页面内标签同时承担“找功能”的职责，层级重复。
- 工作台在桌面端同时出现左栏、主区、表单栏、助手栏，信息密度过高。
- Dashboard、素材库和任务列表的卡片、筛选条、状态表达缺少统一节奏。
- 部分按钮只有图标，移动端的操作优先级不够明确。
- 390px 视口下 7 个页面均检测到约 2px 的横向溢出。
- 全页截图中粘性顶部栏会重复出现，说明长页面滚动上下文需要更明确。
- 页面 metadata 标题不完全一致。

## 说明

此目录仅用于封存 Phase 1.1 的视觉现状，不参与运行时构建。后续改版截图应存入新的版本目录，避免覆盖本基线。
