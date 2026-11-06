# 一键复刻注册活动预览与详情说明修正

注册赠送与新人包已于 2026-11-06 12:55 正式上线。以下保留界面迭代记录，当前商业实现和发布状态见 [正式活动记录](replica-welcome-20261106.md)。

用户要求保留现有广告营销表达，核心宣传快速一键复刻；本任务范围为绘品网站与海宇数科站内日更，站外由SEO同事负责。

## 已正式发布

应用提交 `4e07ae17ae6ea860b871bdc07843586a8908edb0` 仅替换 `lib/feature-registry.ts` 的详情套图素材提示：

> 先选择国内或国际市场，上传清晰的多角度商品图；如有版式或风格要求，可补充参考图和文案说明。

前端镜像为 `huipin-seo/frontend:4e07ae17ae6ea860b871bdc07843586a8908edb0`，发布目录为 `/srv/huipin-copy-20261106-4e07ae17ae6e`。沿用既有SEO发布器，候选生产构建、源码哈希和20个公开页面等检查通过。正式公开页已确认旧句消失、新句存在，原营销文案保留。仅更换前端镜像，后端、数据库、支付、网关及其他容器不变。前一镜像及配置快照保留于发布目录，脱敏结果见 `verification/detail-guidance-20261106/activation.json`。

## 活动界面本地预览记录（已结束）

开发模式访问 `/?replica_offer_preview=1`，首页显示“注册就送一整套”“全网最牛一键复刻”活动条；点击“免费领取”打开带相同权益提示的注册弹窗。没有发送短信或执行支付。预览受开发模式与查询参数双重限制，生产不展示。活动界面源代码尚未部署。

根据用户反馈，活动条保持紧凑，与首页主内容左右边界一致；注册提示与输入框等宽。已删除页面中的预览和未开放提示，用户界面不显示内部交付状态。首轮紧凑布局记录见 `verification/detail-guidance-20261106/compact-offer-browser.json`，最新字号与动效调整见下节及 `verification/detail-guidance-20261106/animated-offer-browser.json`。用户界面长期要求已保存至 `.cursor/rules/customer-facing-ui.mdc`。

建议商业规则是一次快速复刻体验券，最多10张参考图、7天内使用；新人9.9元160积分、限购一次、30天有效。均为方案，现有生产赠送和支付目录未修改。快速复刻按唯一参考图数量封顶计费，额外拆屏不增加费用，不能将参考图数与实际输出数混为一谈。

## 字号与动效调整

按用户反馈研究开源项目的公开示例与源码后，主标题调整为桌面28px，赠送权益32px，辅助卖点17px，按钮16px。横幅高度76px；移动端按宽度分级调整文字，保留左右对齐。最终采用与品牌协调的深紫底、白色标题、淡紫权益高光及浅紫按钮；黄红方案已取消，图标标签与分隔线已精简，注册弹窗不再展示次数、参考图数量或拆屏计费等细则。文字扫光、按钮柔光与扫光、低频背景流光使用本地CSS实现，悬停时按钮轻抬与箭头位移，减少动态效果偏好会停止循环和位移。未引入新的组件库依赖。

设计参考：

- [Flowbite Banner](https://github.com/themesberg/flowbite/blob/main/content/components/banner.md)：营销信息与明确CTA组合、响应式容器和对齐。
- [Magic UI Shimmer Button](https://github.com/magicuidesign/magicui/blob/main/apps/www/registry/magicui/shimmer-button.tsx) 与 [Animated Shiny Text](https://magicui.design/docs/components/animated-shiny-text)：扫光突出行动入口与关键词。
- [Motion Primitives Glow Effect](https://github.com/ibelick/motion-primitives/blob/main/components/core/glow-effect.tsx)：柔化光晕、呼吸节奏与层次。
- [React Bits Shiny Text](https://github.com/DavidHDev/react-bits/blob/main/src/content/TextAnimations/ShinyText/ShinyText.jsx)：局部文字高光、不同触发与动态效果偏好处理。

以上作为结构和视觉机制参考，本次样式为项目内独立实现。浏览器验证见 `verification/detail-guidance-20261106/animated-offer-browser.json`。

预览阶段已结束：服务端资格、唯一领取、任务绑定与有效期、失败恢复、限购并发保护及支付到账已接通，复用现有退款处理，当前已启用真实领取入口。正式状态与验证记录见上述正式活动记录。
