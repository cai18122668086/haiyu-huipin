# 淘宝 / 天猫单链接图片采集器

这是接入网站前的独立验证程序。它只处理用户主动提交的一个淘宝或天猫商品链接，读取页面中公开可见的商品标题、价格和图片 URL，并可把图片保存到本地目录。

程序不会登录、解决验证码、绕过风控、扫描搜索页或批量遍历店铺。动态页面可以用可选的 Playwright 浏览器模式读取；如果页面要求登录或被平台拦截，会在 `warnings` 中明确返回，而不是尝试绕过。

## 使用

静态 HTML 请求（先用于快速验证）：

```powershell
python tools/taobao_tmall_crawler.py "https://item.taobao.com/item.htm?id=商品ID" --out artifacts/taobao-item --json artifacts/taobao-item.json
```

动态页面：

```powershell
pip install playwright
playwright install chromium
python tools/taobao_tmall_crawler.py "https://detail.tmall.com/item.htm?id=商品ID" --browser --out artifacts/tmall-item
```

只解析、不下载图片：

```powershell
python tools/taobao_tmall_crawler.py "https://item.taobao.com/item.htm?id=商品ID" --no-download
```

批量核验（每行一个用户提供的商品链接）：

```powershell
python tools/taobao_tmall_crawler.py links.txt --batch --browser --out artifacts/batch
```

输出中的图片角色是 `main`、`sku`、`detail`，后续接入网站时可以分别映射到现有 Asset 的 `reference` 资源和商品素材选择器。当前版本还没有写入网站数据库，也没有上传对象存储。

## 网站本机 Worker 并发

在 `D:\Polaris\local-worker\worker.env.ps1` 设置
`$env:POLARIS_WORKER_CONCURRENCY = "2"` 后重启 `PolarisCommerceLocalWorker` 计划任务，
可同时执行两个不同账号的登录或抓取任务。有效范围为 1～4；不配置时保持单槽位。
同一账号的登录和抓取互斥，账号 Profile 与 Cookie 仍独立保存在本机。
浏览器按任务启动和退出，保留磁盘会话；常驻浏览器与延长平台 Cookie 有效期不属于此配置的功能。

## 调研依据

- [JeremyDong22/taobao_mcp](https://github.com/JeremyDong22/taobao_mcp)：近期的 Playwright + 持久浏览器会话方案，包含淘宝/天猫主图、SKU 图、详情图提取思路。
- [zhangjiancong/MarketSpider](https://github.com/zhangjiancong/MarketSpider)：Selenium 方案，覆盖淘宝、京东、1688，适合参考浏览器自动化和链接处理。
- [淘宝开放平台商品 API](https://developer.alibaba.com/docs/api.htm?apiId=4)：正式接入阶段应优先评估授权 API，而不是把页面抓取当成稳定公共接口。
