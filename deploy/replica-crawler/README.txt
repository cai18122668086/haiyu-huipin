海宇绘品「一键复刻」商品采集接入（2026-10-29）

默认供应商为 EveryInfra，后端实现位于 backend/app/everyinfra.py。
商品拉取不自动切换到 JustOneAPI 或价格更高的淘宝 V3。
原 JustOneAPI 适配保留为历史代码，已有商品记录仍可查看和导入。

接入来源：淘宝、天猫、1688、京东、闲鱼、得物、Amazon、速卖通、Shopee、Temu、Etsy、Walmart。
来源注册于 backend/app/product_sources.py。按供应商目录选用商品接口；部分接口名为
profile、item_detail 或 product_summary，不把搜索结果、店铺和供应商资料当作指定商品。
eBay、Craigslist、ThomasNet 当前只有搜索或供应商能力，不承诺在本流程支持其单品取图。

京东聚合 product_detail、description_images 和 skus。SKU 按返回条数计费，使用同步模式；
其他接口异步提交并查询 job_id。只将实际图片分类为主图、详情图和 SKU 图。
前端仅展示存在图片的分组，不显示缺图提示。
国际来源与闲鱼/得物会映射到受支持的生成目标平台。

入口：POST /api/v1/workspaces/{workspace_id}/replica/products。
来源列表：GET /api/v1/workspaces/{workspace_id}/replica/product-platforms。
缓存按工作区、来源、商品、地区站点和北京时间日期隔离。
付费提交前保存意图，job_id 持久化；重试恢复原任务，避免重复提交。
提交结果不明时保留核对状态，不自动重试或切换付费供应商。
商品图片只允许受信任 CDN，沿用现有大小限制和图像校验。

本地密钥只放在被 Git 忽略的 backend/.env：
POLARIS_EVERYINFRA_API_KEY=<本项目自己的密钥>
本目录 .env.local 保存部署用同名变量；.env.everyinfra.local 用于隔离验收。
真实 env、认证头和带凭据的配置输出不得提交到 Git。

compose.override.yml 只给 api 注入 EveryInfra 密钥。发布新代码需构建包含新模块的
后端镜像，保留当前全部支付、并发和队列叠加配置。
之前服务器上仅注入 JustOneAPI 密钥的预检已过期，不能据此启用新版本。
用户要求暂不切换正式网站，本次只做本地实现、隔离验收和配置准备。
