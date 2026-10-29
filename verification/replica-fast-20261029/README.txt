一键复刻快速版正式发布验收（2026-10-29）

应用构建：80b2ad06996ae1decd307d7c4778597d66079e57。
后端：huipin-replica-fast/backend:20261029-v2。
前端：huipin-replica-fast/frontend:20261029-v2。
基线：已部署 segmentation-v16 后端及 preview 前端；原版当前 v16、历史 v15 保留。

build.json：363 个后端 Python 源文件与前端受控源文件校验。
test.json / postgres-regression.log：候选镜像隔离 PostgreSQL 446 通过、1 项既有容量测试排除。
runtime.json：原版默认、快速枚举、单套 20、全站 3000、异步运行及 v16/v15 兼容性。
activation.json：备份、切换、配置/资源/其他服务保持及数据库迁移不变。
public-probe.json：公网登录、支付目录、权限和合成账号清理；未调用生图、采集或真实短信。

真实浏览器：原版默认选中；可选快速；切换风格后仍选中快速；一比一模仿也可选快速。
未提交素材或开始真实付费生成，留给用户在正式站测试。

第一版候选继承了跨境 Skill 缺少 .env.example 的基础镜像清单问题，测试未通过且未切换。
最终候选只补回 Git 中受控、包含占位 Key 的公开模板，未包含任何真实环境配置。
另一窗口已上线的 v16 修复先合入本发布，其他窗口未发布的本地修改没有打包。
私有 Compose、容器完整 inspect、数据库备份和 QA 凭据仅在服务器受限目录保存，不入库。
