import { getFeatureById } from "@/lib/feature-registry";
import { PRIMARY_CREATION_ENTRIES } from "@/lib/creation-entrypoints";
import { Lightbulb } from "lucide-react";

function navigationFeature(
  featureId: string,
  options: {
    description: string;
    label?: string;
  },
) {
  const feature = getFeatureById(featureId);

  if (!feature) {
    throw new Error(`Navigation feature is missing from feature registry: ${featureId}`);
  }

  return {
    featureId: feature.id,
    href: feature.route,
    label: options.label ?? feature.title,
    description: options.description,
    icon: feature.icon,
    tone: feature.tone,
  };
}

export type NavigationFeatureItem = ReturnType<typeof navigationFeature>;

export type NavigationFeatureGroup = {
  label: string;
  items: readonly NavigationFeatureItem[];
};

export type NavigationMenuDefinition = {
  key: "all-features" | "toolbox";
  label: string;
  href: string;
  groups: readonly NavigationFeatureGroup[];
  footer?: {
    label: string;
    href: string;
  };
};

export const PRIMARY_NAVIGATION_LINKS = [
  { label: "首页", href: "/" },
  ...PRIMARY_CREATION_ENTRIES.map(({ label, href }) => ({ label, href })),
  { label: "素材中心", href: "/assets" },
] as const;

export const MOBILE_PRIMARY_NAVIGATION_LINKS = [
  ...PRIMARY_NAVIGATION_LINKS,
  { label: "帮助中心", href: "/help" },
] as const;

export const AI_FEATURES_MENU = {
  key: "all-features",
  label: "AI功能大全",
  href: "/tools",
  groups: [
    {
      label: "热门创作",
      items: [
        navigationFeature("local-edit", {
          description: "用自然语言完成局部精修",
        }),
        navigationFeature("change-background", {
          description: "快速替换商品背景，适配不同展示需求",
        }),
        navigationFeature("reference-remix", {
          description: "快速复刻参考图的构图与氛围",
        }),
        navigationFeature("one-click-replica", {
          description: "从商品链接或参考图规划并生成商品图",
        }),
      ],
    },
    {
      label: "电商套图",
      items: [
        navigationFeature("domestic-main-image", {
          description: "生成搜索主图与五张连续轮播图",
        }),
        navigationFeature("detail-pack", {
          description: "生成国内、国际全平台详情图",
        }),
        navigationFeature("amazon-visual-suite", {
          description: "生成亚马逊主图、副图与 A+ 内容",
        }),
      ],
    },
  ],
  footer: {
    label: "查看全部 AI 功能",
    href: "/tools",
  },
} as const satisfies NavigationMenuDefinition;

export const AI_TOOLBOX_MENU = {
  key: "toolbox",
  label: "AI工具箱",
  href: "/#ai-toolbox",
  groups: [
    {
      label: "常用工具",
      items: [
        navigationFeature("change-color", {
          description: "保留商品纹理，快速生成目标配色",
        }),
        navigationFeature("change-scene", {
          description: "重建商品场景与光影氛围",
        }),
        navigationFeature("change-model", {
          description: "替换符合目标人群的模特",
        }),
        navigationFeature("scene-main", {
          description: "生成商业场景主视觉",
        }),
        navigationFeature("white-background", {
          description: "生成符合平台规范的白底图",
        }),
        navigationFeature("model-image", {
          description: "生成模特展示商品图",
        }),
        navigationFeature("try-on", {
          description: "让服饰商品自然适配模特",
        }),
        navigationFeature("model-pose-transfer", {
          description: "保留模特服装，快速更换人物姿势",
        }),
        navigationFeature("creative-image", {
          description: "生成卖点、活动与营销创意素材",
        }),
      ],
    },
  ],
} as const satisfies NavigationMenuDefinition;

export const AI_NAVIGATION_MENUS = [AI_FEATURES_MENU, AI_TOOLBOX_MENU] as const;

export const ALL_TOOLS_MENU: NavigationMenuDefinition = {
  ...AI_FEATURES_MENU,
  label: "全部工具",
  groups: [
    { label: "主推创作", items: [
      navigationFeature("one-click-replica", { description: "有同行参考，做自己的商品套图" }),
      navigationFeature("domestic-main-image", { label: "主图套图", description: "搜索主图与连续轮播，一次规划" }),
      navigationFeature("detail-pack", { label: "详情图套图", description: "把卖点与细节组织成完整详情图" }),
      { featureId: "inspiration", label: "灵感专区", href: "/inspiration", description: "看案例、找方向、一键做同款", icon: Lightbulb, tone: "indigo" },
    ] },
    { label: "精修与跨境", items: AI_FEATURES_MENU.groups.flatMap(group => group.items).filter(item => !["one-click-replica", "domestic-main-image", "detail-pack"].includes(item.featureId)) },
    ...AI_TOOLBOX_MENU.groups,
  ],
};
