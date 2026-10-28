import { Eraser, FolderClock, Images, LibraryBig, ScanLine } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  FEATURE_GROUPS,
  type FeatureJobType,
} from "@/lib/feature-registry";

export type ToolItem = {
  title: string;
  description: string;
  href: string;
  icon: LucideIcon;
  tone: string;
  badge?: string;
  jobType?: FeatureJobType;
};

export type ToolGroup = {
  label: string;
  items: ToolItem[];
};

const featureToolGroups: ToolGroup[] = FEATURE_GROUPS.map((group) => ({
  label: group.label,
  items: group.features.map((feature) => ({
    title: feature.title,
    description: feature.description,
    href: feature.route,
    icon: feature.icon,
    tone: feature.tone,
    badge: "badge" in feature ? feature.badge : undefined,
    jobType: feature.jobType,
  })),
}));

export const toolGroups: ToolGroup[] = [
  ...featureToolGroups,
  {
    label: "资产管理",
    items: [
      {
        title: "个人控制台",
        description: "用量与最近创作",
        href: "/dashboard",
        icon: LibraryBig,
        tone: "indigo",
      },
      {
        title: "生成记录",
        description: "查看全部任务",
        href: "/jobs",
        icon: FolderClock,
        tone: "blue",
      },
      {
        title: "个人素材库",
        description: "管理创作资产",
        href: "/assets",
        icon: Images,
        tone: "cyan",
      },
    ],
  },
];

export const futureTools = [
  { label: "智能抠图", icon: ScanLine },
  { label: "瑕疵消除", icon: Eraser },
];
