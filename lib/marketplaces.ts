import type { DetailPackJobCreate } from "@/lib/api/types";

export type MarketRegion = "domestic" | "international";
export type MarketplaceImageSize = DetailPackJobCreate["configuration"]["size"];

export type MarketplaceSizePreset = {
  value: MarketplaceImageSize;
  label: string;
};

export type Marketplace = {
  id: string;
  name: string;
  shortName: string;
  region: MarketRegion;
  format: string;
  mainImage: string;
  detailImage: string;
  note: string;
  sizePresets?: MarketplaceSizePreset[];
};

export const marketplaceGroups: Record<MarketRegion, Marketplace[]> = {
  domestic: [
    {
      id: "tmall",
      name: "天猫",
      shortName: "天",
      region: "domestic",
      format: "详情页 750px 宽",
      mainImage: "800 × 800（1:1）",
      detailImage: "750px 宽 · 建议分段输出",
      note: "适合品牌旗舰店与完整卖点叙事",
    },
    {
      id: "jd",
      name: "京东",
      shortName: "京",
      region: "domestic",
      format: "详情页 750px 宽",
      mainImage: "800 × 800（1:1）",
      detailImage: "750px 宽 · 建议分段输出",
      note: "突出参数、性能与购买决策信息",
    },
    {
      id: "taobao",
      name: "淘宝",
      shortName: "淘",
      region: "domestic",
      format: "详情页 750px 宽",
      mainImage: "800 × 800（1:1）",
      detailImage: "750px 宽 · 单段 800–1500px",
      note: "适合灵活的商品卖点和场景表达",
    },
    {
      id: "pdd",
      name: "拼多多",
      shortName: "拼",
      region: "domestic",
      format: "详情页 750px 宽",
      mainImage: "750 × 750（1:1）",
      detailImage: "750px 宽 · 转化优先",
      note: "强调价格利益点与核心功能",
    },
    {
      id: "douyin",
      name: "抖音电商",
      shortName: "抖",
      region: "domestic",
      format: "移动端竖版优先",
      mainImage: "800 × 800 / 750 × 1000",
      detailImage: "750px 宽 · 3:4 卖点图",
      note: "适合短节奏、强视觉的卖点呈现",
    },
    {
      id: "xiaohongshu",
      name: "小红书",
      shortName: "红",
      region: "domestic",
      format: "3:4 种草内容",
      mainImage: "1242 × 1660（3:4）",
      detailImage: "1080 × 1440 · 分页内容",
      note: "强调生活方式、体验与口碑表达",
    },
  ],
  international: [
    {
      id: "amazon",
      name: "Amazon",
      shortName: "A",
      region: "international",
      format: "Listing + A+",
      mainImage: "1600 × 1600（1:1）",
      detailImage: "副图同尺寸 · A+ 按模块尺寸",
      note: "主图与副图使用 1600 × 1600；A+ 不预设固定尺寸",
      sizePresets: [{ value: "1600x1600", label: "1600 × 1600" }],
    },
    {
      id: "ebay",
      name: "eBay",
      shortName: "e",
      region: "international",
      format: "Listing 商品图",
      mainImage: "1600 × 1600（推荐）",
      detailImage: "副图同尺寸",
      note: "主图与副图使用 1600 × 1600",
      sizePresets: [{ value: "1600x1600", label: "1600 × 1600" }],
    },
    {
      id: "walmart",
      name: "Walmart",
      shortName: "W",
      region: "international",
      format: "1:1 商品图",
      mainImage: "2200 × 2200（推荐）",
      detailImage: "副图同尺寸",
      note: "主图与副图使用 2200 × 2200",
      sizePresets: [{ value: "2200x2200", label: "2200 × 2200" }],
    },
    {
      id: "etsy",
      name: "Etsy",
      shortName: "E",
      region: "international",
      format: "1:1 商品图",
      mainImage: "2000 × 2000（推荐）",
      detailImage: "副图同尺寸",
      note: "主图与副图使用 2000 × 2000",
      sizePresets: [{ value: "2000x2000", label: "2000 × 2000" }],
    },
    {
      id: "tiktok-shop",
      name: "TikTok Shop",
      shortName: "T",
      region: "international",
      format: "1:1 社交电商商品图",
      mainImage: "1000 × 1000（最低 600 × 600）",
      detailImage: "副图同尺寸",
      note: "推荐 1000 × 1000；最低尺寸为 600 × 600",
      sizePresets: [{ value: "1000x1000", label: "1000 × 1000" }],
    },
    {
      id: "shopee",
      name: "Shopee",
      shortName: "S",
      region: "international",
      format: "1:1 商品图",
      mainImage: "1024 × 1024（1:1）",
      detailImage: "副图同尺寸",
      note: "主图与副图使用 1024 × 1024",
      sizePresets: [{ value: "1024x1024", label: "1024 × 1024" }],
    },
    {
      id: "shopify",
      name: "Shopify",
      shortName: "S",
      region: "international",
      format: "品牌独立站商品图",
      mainImage: "2048 × 2048（推荐）",
      detailImage: "产品图同尺寸",
      note: "产品图使用 2048 × 2048",
      sizePresets: [{ value: "2048x2048", label: "2048 × 2048" }],
    },
    {
      id: "mercado-libre",
      name: "Mercado Libre",
      shortName: "M",
      region: "international",
      format: "1:1 商品图",
      mainImage: "1200 × 1200（推荐）",
      detailImage: "副图同尺寸",
      note: "主图与副图使用 1200 × 1200",
      sizePresets: [{ value: "1200x1200", label: "1200 × 1200" }],
    },
    {
      id: "aliexpress",
      name: "AliExpress",
      shortName: "A",
      region: "international",
      format: "1:1 商品图 · 可选竖图",
      mainImage: "1000 × 1000（推荐）",
      detailImage: "可补充 750 × 1000 竖图",
      note: "默认 1000 × 1000，可按需选择 750 × 1000 竖图",
      sizePresets: [
        { value: "1000x1000", label: "1000 × 1000" },
        { value: "750x1000", label: "750 × 1000" },
      ],
    },
  ],
};
