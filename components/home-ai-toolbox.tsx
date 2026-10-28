"use client";

import Link from "next/link";

import {
  resolveRuntimeCapability,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";
import { getFeatureById, type FeatureDefinition } from "@/lib/feature-registry";

const TOOLBOX_DEFINITIONS = [
  { id: "change-background", description: "保留商品主体，替换背景与环境" },
  { id: "change-color", description: "保留纹理，快速生成目标配色" },
  { id: "change-scene", description: "重建商品场景与光影氛围" },
  { id: "change-model", description: "快速替换符合目标人群的模特" },
  { id: "scene-main", description: "一键生成商业场景主视觉" },
  { id: "white-background", description: "生成符合平台规范的白底图" },
  { id: "model-image", description: "一键生成模特展示商品图" },
  { id: "try-on", description: "服饰商品自然适配模特" },
  { id: "model-pose-transfer", description: "保留模特服装，快速更换人物姿势" },
  { id: "creative-image", description: "批量生成卖点与活动素材" },
] as const;

type ToolboxItem = {
  feature: FeatureDefinition;
  description: string;
};

const toolboxItems: ToolboxItem[] = TOOLBOX_DEFINITIONS.flatMap((item) => {
  const feature = getFeatureById(item.id);
  return feature ? [{ feature, description: item.description }] : [];
});

export function HomeAiToolbox() {
  const runtime = useRuntimeStatus();

  return (
    <section
      className="home-ai-toolbox"
      id="ai-toolbox"
      aria-labelledby="home-ai-toolbox-title"
    >
      <header className="home-ai-toolbox__header">
        <div>
          <span className="home-ai-toolbox__eyebrow">AI 工具箱</span>
          <h2 id="home-ai-toolbox-title">高频工具，一键直达</h2>
        </div>
        <p>选择常用处理能力，快速进入对应工作台。</p>
      </header>

      <div className="home-ai-toolbox__grid">
        {toolboxItems.map(({ feature, description }) => {
          const capability = resolveRuntimeCapability(
            runtime.data,
            feature.runtimeCapability,
          );
          const unavailable =
            capability !== undefined && capability.availability !== "available";
          const Icon = feature.icon;

          return (
            <Link
              href={feature.route}
              className={`home-ai-toolbox__item home-ai-toolbox__item--${feature.tone}${
                unavailable ? " is-unavailable" : ""
              }`}
              data-feature-id={feature.id}
              data-runtime-capability={feature.runtimeCapability}
              data-capability-state={capability?.availability ?? "unknown"}
              aria-disabled={unavailable || undefined}
              tabIndex={unavailable ? -1 : undefined}
              onClick={
                unavailable
                  ? (event) => {
                      event.preventDefault();
                    }
                  : undefined
              }
              key={feature.id}
            >
              <span className="home-ai-toolbox__icon" aria-hidden="true">
                <Icon size={24} />
              </span>
              <span className="home-ai-toolbox__copy">
                <strong>{feature.title}</strong>
                <small>{description}</small>
              </span>
              {unavailable ? (
                <span className="home-ai-toolbox__status">暂不可用</span>
              ) : null}
            </Link>
          );
        })}
      </div>
    </section>
  );
}
