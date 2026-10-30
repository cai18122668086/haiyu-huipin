import type { ProductImagePreset } from "./feature-registry";

export const PRODUCT_INPUT_EXAMPLES: Record<ProductImagePreset, { product: string; reference?: string }> = {
  change_background: { product: "/images/cases/platform-v3/02-headphones-messy.png" },
  change_scene: { product: "/images/homepage-features/native-v2-webp/02-input-desktop.webp" },
  change_color: { product: "/images/cases/platform-v3/01-bag-white.png" },
  change_model: { product: "/images/cases/platform-v2/04-model.png" },
  try_on: { product: "/images/cases/platform-v2/03-cardigan.png", reference: "/images/cases/platform-v2/04-model.png" },
  scene_main: { product: "/images/cases/platform-v3/01-bag-white.png" },
  white_background: { product: "/images/homepage-features/native-v2-webp/02-input-desktop.webp" },
  model_image: { product: "/images/cases/platform-v2/03-cardigan.png" },
};

/** Existing site illustrations, retained as illustrations rather than new platform-run evidence. */
export const RETAINED_WORKSPACE_EXAMPLES = [
  { id: "local-edit", title: "一句话改图", change: "修复破损标签，保留原有瓶身与背景", input: "/images/homepage-features/native-v2-webp/01-input-desktop.webp", result: "/images/homepage-features/native-v2-webp/01-masked-primary-desktop.webp", reference: undefined },
  { id: "change-background", title: "更换背景", change: "原桌面耳机 → 浅色场景；保留同一副耳机", input: "/images/homepage-features/native-v2-webp/02-input-desktop.webp", result: "/images/homepage-features/native-v2-webp/02-background-primary-desktop.webp", reference: undefined },
  { id: "reference-remix", title: "一键模仿", change: "白底商品与构图参考分开提供，保留原站暖色拱门示例作比较", input: "/images/homepage-features/native-v2-webp/03-input-desktop.webp", reference: "/images/homepage-features/card-03-remix-reference-v1.webp", result: "/images/homepage-features/card-03-remix-result-serum-v2.png" },
  { id: "detail-pack", title: "详情图套图", change: "普通榨汁杯商品图 → 带细节说明的详情封面", input: "/images/homepage-features/native-v2-webp/04-input-desktop.webp", result: "/images/homepage-features/native-v2-webp/04-detail-primary-desktop.webp", reference: undefined },
  { id: "domestic-main-image", title: "主图套图", change: "电煮锅商品照片 → 渠道主图封面", input: "/images/homepage-features/native-v2-webp/05-input-desktop.webp", result: "/images/homepage-features/native-v2-webp/05-domestic-primary-desktop.webp", reference: undefined },
  { id: "amazon-visual-suite", title: "亚马逊套图", change: "白底收纳包 → 带结构信息的展示封面", input: "/images/homepage-features/native-v2-webp/06-input-desktop.webp", result: "/images/homepage-features/native-v2-webp/06-amazon-primary-desktop.webp", reference: undefined },
] as const;
