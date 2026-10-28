import { getFeatureById } from "./feature-registry";

function route(featureId: string) {
  const feature = getFeatureById(featureId);
  if (!feature) throw new Error(`Missing primary creation feature: ${featureId}`);
  return feature.route;
}

/** Shared wayfinding for the homepage, desktop navigation and mobile navigation. */
export const PRIMARY_CREATION_ENTRIES = [
  {
    id: "one-click-replica", label: "一键复刻", href: route("one-click-replica"),
    question: "有同行参考，想做自己的套图？", description: "借鉴参考图的构图与风格，换成自己的好商品。",
    input: "商品原图 + 参考链接 / 图片", output: "自己的商品套图", action: "开始复刻",
    image: "/images/homepage-features/native-v2-webp/03-remix-primary-desktop.webp", tag: "优先体验",
  },
  {
    id: "domestic-main-image", label: "主图套图", href: route("domestic-main-image"),
    question: "准备上新，先把第一眼做好？", description: "围绕商品卖点，制作搜索主图与连续轮播图。",
    input: "商品原图 + 核心卖点", output: "1 张主图 + 5 张轮播图", action: "制作主图套图",
    image: "/images/homepage-features/native-v2-webp/05-domestic-primary-desktop.webp", tag: "上新必备",
  },
  {
    id: "detail-pack", label: "详情图套图", href: route("detail-pack"),
    question: "想把卖点和细节一次讲清？", description: "把商品信息组织成完整、连贯的详情页视觉。",
    input: "商品素材 + 商品信息", output: "整套商品详情图", action: "制作详情套图",
    image: "/images/homepage-features/native-v2-webp/04-detail-primary-desktop.webp", tag: "讲清卖点",
  },
  {
    id: "inspiration", label: "灵感专区", href: "/inspiration",
    question: "还没方向，想先看看好图？", description: "按品类找案例，查看创作指令，带着灵感做同款。",
    input: "挑选喜欢的案例", output: "参考指令 + 同款创作入口", action: "去找灵感",
    image: "/images/haiyu/fashion.webp", tag: "新手友好",
  },
] as const;

export const FEATURE_THUMBNAILS: Readonly<Record<string, string>> = {
  "one-click-replica": PRIMARY_CREATION_ENTRIES[0].image,
  "domestic-main-image": PRIMARY_CREATION_ENTRIES[1].image,
  "detail-pack": PRIMARY_CREATION_ENTRIES[2].image,
  inspiration: PRIMARY_CREATION_ENTRIES[3].image,
  "local-edit": "/images/homepage-features/native-v2-webp/01-masked-primary-desktop.webp",
  "reference-remix": "/images/homepage-features/native-v2-webp/03-remix-primary-desktop.webp",
  "amazon-visual-suite": "/images/homepage-features/native-v2-webp/06-amazon-primary-desktop.webp",
  "change-background": "/images/homepage-features/native-v2-webp/02-background-primary-desktop.webp",
  "change-color": "/images/haiyu/outdoor.webp",
  "change-scene": "/images/haiyu/outdoor.webp",
  "change-model": "/images/haiyu/fashion.webp",
  "scene-main": "/images/homepage-features/native-v2-webp/05-domestic-primary-desktop.webp",
  "white-background": "/images/homepage-features/native-v2-webp/02-input-desktop.webp",
  "model-image": "/images/haiyu/fashion.webp",
  "try-on": "/images/haiyu/outdoor.webp",
  "model-pose-transfer": "/images/model-pose-transfer/previews/female-001-960.webp",
  "creative-image": "/images/homepage-features/native-v2-webp/04-detail-primary-desktop.webp",
};
