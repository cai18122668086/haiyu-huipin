# 国内详情发布与回退

发布前必须通过真实隔离成图、逐屏审查、长图交付和最终 CI。当前仅准备配置，未部署。

新增 detail-api 与 detail-worker。原 api、默认 worker、抓取 worker、浏览器会话和
Windows Worker 继续使用原版本。详情 worker 只订阅 domestic_detail_v431；dispatcher
升级为识别新版本标记的版本，普通任务和历史任务仍分发到原有队列。

Caddy 仅将任务集合、任务子资源和 /api/v1/runtime 交给 detail-api。认证、素材、
抓取与本机 Worker 接口继续走原 api。新 API 按任务版本将历史国内详情请求转发至原
api，保留原始正文、会话、幂等键和返回流。新版任务始终交给新版运行时。

新 API 配置 POLARIS_ECOMMERCE_STARTUP_RECOVERY_ENABLED=false，关闭抓取启动恢复，
不挂载原浏览器会话卷。该开关默认 true，原服务行为不变。新版原生 Harness 使用
现有持久卷；不迁移、修改或重新执行历史商品任务。

prepare_domestic_detail_release.py 从服务器当前 Compose 的解析 JSON 生成私有叠加
配置，只接受不可变本机镜像 SHA256 ID、完整 Git 版本、该版本内已审查的 Caddyfile
和通过隔离验收的分析配置。输出继承生产私密值，必须留在服务器 Git 目录之外，
权限 0600。脚本只生成文件，不启动、部署或重启任何服务。

生成时先确认当前原 api、默认 worker、Caddy 数据卷和现有环境，再备份当前配置与
镜像标识。使用现有迁移服务执行必要的单调结构迁移；不回退字段宽度，不修改任务行。
先启动新增 API 与专用详情 worker，再升级 dispatcher、前端及 Caddy 配置。
生产验收只做健康和路由检查，禁止提交测试生成、消费旧生产队列或重跑用户任务。

回退新任务创建时，重新生成叠加配置并带 --disable-new-native-creation，使
POLARIS_DOMESTIC_DETAIL_NATIVE_CREATION_ENABLED=false。国内详情新建请求原样交给旧
api；已有原生任务继续由 detail-api 和专用 worker 提供处理及交付。此阶段不能将
所有任务请求整体退回旧 API，也不能停止正在服务已有原生任务的专用运行时。

独立验收使用的本机定向代理、私有生产生图密钥副本和预算设置不进入生产发布配置。
生产原有费用上限保留；新详情分析配置单独提供，不改变国内主图供应商与运行逻辑。
