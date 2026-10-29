统一发布：国内主图原生快速 B、国内详情快速、亚马逊快速。
基线：main b6b91b8 / consolidated v2；不新增迁移或环境变量。
主图按用户选择保留3a6ff23的B Skill/编译器/提示词；删除A实现及本轮优化、复测证据。
其他窗口来源：国内详情ef37146，亚马逊ce13543。私人素材展示页继续仅本地保留。
先提交候选并运行package_release.py，上传source.tar.gz至/srv/huipin-suite-fast-20261029。
现有provider私密文件仅在服务器内复制为0600；不从工作站上传凭据。
依次运行release_suite_fast.py build / test / activate。
镜像以候选完整SHA命名；激活核对空闲任务、备份、完整源码/Skill、运行配置及其他服务不变。
候选回归使用隔离PostgreSQL，发布验证不新增付费生成、采集或短信请求。
