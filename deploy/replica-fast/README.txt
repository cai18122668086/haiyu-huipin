一键复刻快速版发布（2026-10-29）

后端以 huipin-integrated/backend:20261029-segmentation-v16 为基线，
前端以 huipin-integrated/frontend:20261029-preview 为基线。只打包受 Git 管理的
快速版后端模块、两个 Skill 副本、manifest、前端入口及结果绑定和测试。
不打包本地 env、测试商品、数据库、用户图片、回执或支付凭据。

package_release.py 在干净且已提交的工作区生成 source.tar.gz；与 origin/main
比较并验证运行中后端差异，不能用其他生产基线运行。

服务器目录 /srv/huipin-replica-fast-20261029。依次执行 release_fast.py build、
test、activate。test 使用隔离 PostgreSQL 和合成账户，不调用真实支付、生图或采集。
activate 复用现有发布器：确认零活动任务、备份数据库及配置、只改镜像、保留所有
业务环境变量、worker 命令和资源限制、原服务和其他站点文件；失败恢复原镜像配置。
使用既有基础 Compose 和会员支付叠加配置，不运行数据库迁移。

provider.private.env 只在服务器从既有私有文件复制并保持 0600；激活前确认与现用
容器的 EveryInfra Key 相同。它是复用发布流程的兼容文件，不改变本次任何供应商设置。
不得把服务器的私有备份、运行容器完整 inspect 或 env 输出写入 Git。

production-baseline.json 固定已部署 v16 的镜像 ID 与后端源码哈希，构建和激活前
再次核对。恢复其既有缺失的 xinghe-crossborder-detail-3/.env.example 公共模板，
该文件仅含占位 API Key，与 Git manifest 完全一致，不包含真实环境配置。

候选发布必须通过：源码与 Skill 哈希、隔离后端回归、生产构建、运行健康、
公网入口与合成认证账户验证。最后在 RELEASE-STATUS.txt 记录实际镜像及验证结果。
