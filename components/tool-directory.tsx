"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { requestLogin } from "@/lib/auth/session-events";
import { useAuthSession } from "@/features/auth/api/session";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, Flame, Search, Sparkles, type LucideIcon } from "lucide-react";

import { HomeAiToolbox } from "@/components/home-ai-toolbox";
import { HaiyuHero, HaiyuHomeFooter, HaiyuHomeReveal, HaiyuProductShowcase, HaiyuCreativeVoices, HaiyuReplicaCardArt } from "@/components/haiyu-home";
import {
  resolveRuntimeCapability,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";
import { FEATURE_REGISTRY } from "@/lib/feature-registry";
import { searchFeatures } from "@/lib/feature-search";
import {
  HOME_FEATURE_VISUALS,
  type HomeFeatureResponsiveImage,
} from "@/lib/home-feature-visuals";
import { HOT_FEATURE_IDS } from "@/lib/feature-priorities";
import { HaiyuStartHub } from "@/components/haiyu-start-hub";
import { FeatureCaseArt } from "./feature-case-art";
import { getFeatureCaseArt } from "@/lib/feature-case-art";

const FEATURE_PRESENTATION_OVERRIDES: Record<
  string,
  { title?: string; description?: string }
> = {
  "one-click-replica": {
    title: "一键复刻同行套图",
    description: "参考同行构图与视觉风格，生成自己的商品套图。",
  },
  "local-edit": {
    description: "用一句话完成局部精修，让你的出图更加精美",
  },

  "detail-pack": {
    description: "一键生成国内、国际全平台详情图",
  },

};

const searchableTools = FEATURE_REGISTRY.map((feature) => {
  const presentation = FEATURE_PRESENTATION_OVERRIDES[feature.id];
  return {
    id: feature.id,
    title: presentation?.title ?? feature.title,
    description: presentation?.description ?? feature.description,
    href: feature.route,
    icon: feature.icon,
    className:
      feature.workspaceTemplate === "masked_edit"
        ? "home-feature--edit"
        : "home-feature--pack",
    keywords: feature.searchKeywords,
    runtimeCapability: feature.runtimeCapability,
    featured: feature.featured,
  };
});

const featuredTools = [
  "local-edit", "reference-remix", "amazon-visual-suite",
].flatMap(id => {
  const tool = searchableTools.find(item => item.id === id && item.featured);
  return tool ? [tool] : [];
});

const RELATED_SCENARIO_RECOMMENDATIONS = [
  {
    pattern: /亚马逊|amazon|跨境|a\+/iu,
    featureIds: ["amazon-visual-suite", "detail-pack", "white-background"],
  },
  {
    pattern: /主图|淘宝|天猫|京东|轮播/iu,
    featureIds: ["domestic-main-image", "scene-main", "white-background"],
  },
  {
    pattern: /背景|场景|环境|光影/iu,
    featureIds: ["change-background", "change-scene", "scene-main"],
  },
  {
    pattern: /模特|服装|穿搭|姿势|试穿/iu,
    featureIds: ["model-pose-transfer", "try-on", "change-model"],
  },
  {
    pattern: /颜色|换色|配色/iu,
    featureIds: ["change-color", "local-edit", "creative-image"],
  },
  {
    pattern: /详情|套图|整套/iu,
    featureIds: ["detail-pack", "domestic-main-image", "amazon-visual-suite"],
  },
] as const;

function getRelatedScenarioTools(query: string) {
  const recommendation = RELATED_SCENARIO_RECOMMENDATIONS.find(({ pattern }) =>
    pattern.test(query),
  );
  const featureIds = recommendation?.featureIds ?? [
    "local-edit",
    "change-background",
    "detail-pack",
  ];

  return featureIds.flatMap((featureId) => {
    const tool = searchableTools.find((item) => item.id === featureId);
    return tool ? [tool] : [];
  });
}

function FeatureVisualImage({
  image,
  className,
  eager = false,
}: {
  image: HomeFeatureResponsiveImage;
  className: string;
  eager?: boolean;
}) {
  return (
    <picture className={className}>
      <source media="(max-width: 720px)" srcSet={image.mobile.src} />
      <source media="(max-width: 1199px)" srcSet={image.tablet.src} />
      <img
        src={image.desktop.src}
        width={image.desktop.width}
        height={image.desktop.height}
        className="home-feature-card__picture-image"
        alt=""
        loading={eager ? "eager" : "lazy"}
        decoding="async"
        draggable={false}
      />
    </picture>
  );
}

function HomeFeatureCardArt({
  featureId,
  FallbackIcon,
}: {
  featureId: string;
  FallbackIcon: LucideIcon;
}) {
  const visual = HOME_FEATURE_VISUALS[featureId];
  const [previewState, setPreviewState] = useState<"idle" | "armed" | "playing" | "done">("idle");
  const previewTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => () => {
    if (previewTimerRef.current !== undefined) window.clearTimeout(previewTimerRef.current);
  }, []);

  if (getFeatureCaseArt(featureId)) {
    return <span className="home-feature-card__art case-art-shell"><FeatureCaseArt featureId={featureId} portrait={featureId !== "local-edit"} /></span>;
  }

  if (!visual) {
    if (featureId === "one-click-replica") return <HaiyuReplicaCardArt />;
    return (
      <span
        className="home-feature-card__art home-feature-card__art--fallback"
        aria-hidden="true"
      >
        <FallbackIcon size={68} />
      </span>
    );
  }

  const replayPreview = () => {
    if (previewTimerRef.current !== undefined) window.clearTimeout(previewTimerRef.current);
    setPreviewState("armed");
    previewTimerRef.current = window.setTimeout(() => {
      setPreviewState("playing");
      previewTimerRef.current = window.setTimeout(() => setPreviewState("done"), 5600);
    }, 220);
  };

  const stopPreview = () => {
    if (previewTimerRef.current !== undefined) window.clearTimeout(previewTimerRef.current);
    setPreviewState("idle");
  };

  return (
    <span
      className={`home-feature-card__art home-feature-card__art--${visual.kind}${previewState !== "idle" ? ` is-preview-${previewState}` : ""}`}
      aria-hidden="true"
      onMouseEnter={replayPreview}
      onFocusCapture={replayPreview}
      onTouchStart={replayPreview}
      onMouseLeave={stopPreview}
      onBlurCapture={stopPreview}
    >
      <span className="home-feature-card__visual-stage">
        <FeatureVisualImage
          image={visual.primary}
          className="home-feature-card__image home-feature-card__image--primary"
          eager
        />
        <FeatureVisualImage
          image={visual.input}
          className="home-feature-card__image home-feature-card__image--input"
          eager
        />
        <FeatureVisualImage
          image={visual.primary}
          className="home-feature-card__image home-feature-card__image--result"
          eager
        />
      </span>
    </span>
  );
}

export function ToolDirectory() {
  const router = useRouter();
  const authSession = useAuthSession();
  const [query, setQuery] = useState("");
  const [activeSearchIndex, setActiveSearchIndex] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const featureGridRef = useRef<HTMLDivElement>(null);
  const runtime = useRuntimeStatus();
  const keyword = query.trim();
  const searchResults = keyword
    ? searchFeatures(keyword, FEATURE_REGISTRY)
        .flatMap((match) => {
          const tool = searchableTools.find((item) => item.id === match.feature.id);
          return tool ? [{ ...match, ...tool }] : [];
        })
        .slice(0, 8)
    : [];
  const relatedScenarioTools = keyword && !searchResults.length
    ? getRelatedScenarioTools(keyword)
    : [];

  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  useEffect(() => {
    const grid = featureGridRef.current;
    if (
      !grid ||
      typeof IntersectionObserver === "undefined" ||
      !window.matchMedia("(hover: none), (pointer: coarse)").matches
    ) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-mobile-played");
          observer.unobserve(entry.target);
        }
      },
      { threshold: 0.45 },
    );

    const cards = grid.querySelectorAll<HTMLElement>(
      ".home-feature-card[data-feature-visual]",
    );
    cards.forEach((card) => observer.observe(card));

    return () => observer.disconnect();
  }, []);


  const openSearchResult = (index: number) => {
    const result = searchResults[index];
    if (!result) return;
    const capability = resolveRuntimeCapability(runtime.data, result.runtimeCapability);
    if (capability !== undefined && capability.availability !== "available") return;
    if (authSession.data) router.push(result.href);
    else requestLogin(result.href);
  };

  return (
    <section className="focus-home">
      <HaiyuHero />
      <HaiyuStartHub />

      <div className="focus-home__content">
        <div className="home-command home-command--primary" id="tools">
          <span className="haiyu-eyebrow">FIND YOUR CREATIVE TOOL</span>
          <h2>更多创作能力，都在这里。</h2>
          <p>
            {FEATURE_REGISTRY.length} 项创作功能，搜索你的需求，或直接选下面的工具
          </p>
          <label>
            <span className="home-command__icon">
              <Sparkles size={18} />
            </span>
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveSearchIndex(0);
              }}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown" && searchResults.length) {
                  event.preventDefault();
                  setActiveSearchIndex((current) =>
                    Math.min(current + 1, searchResults.length - 1),
                  );
                } else if (event.key === "ArrowUp" && searchResults.length) {
                  event.preventDefault();
                  setActiveSearchIndex((current) => Math.max(current - 1, 0));
                } else if (event.key === "Enter" && searchResults.length) {
                  event.preventDefault();
                  openSearchResult(activeSearchIndex);
                } else if (event.key === "Escape") {
                  setQuery("");
                }
              }}
              placeholder="试试：商品换背景、耳机局部精修、护肤品详情套图"
              aria-label="搜索创作任务"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={Boolean(keyword)}
              aria-controls="home-search-results"
              aria-activedescendant={
                searchResults[activeSearchIndex]
                  ? `home-search-result-${searchResults[activeSearchIndex].id}`
                  : undefined
              }
            />
            <kbd>⌘ K</kbd>
            <button
              type="button"
              aria-label="打开首个搜索结果"
              onClick={() => openSearchResult(activeSearchIndex)}
              disabled={!searchResults.length}
            >
              <ArrowRight size={18} />
            </button>
          </label>
          {keyword ? (
            <div
              className="home-search-results"
              id="home-search-results"
              role={searchResults.length ? "listbox" : "region"}
              aria-label={searchResults.length ? "功能搜索结果" : "相近场景推荐"}
            >
              {searchResults.length ? (
                searchResults.map((tool, index) => {
                  const capability = resolveRuntimeCapability(
                    runtime.data,
                    tool.runtimeCapability,
                  );
                  const unavailable =
                    capability !== undefined &&
                    capability.availability !== "available";
                  const Icon = tool.icon;
                  return (
                    <Link
                      id={`home-search-result-${tool.id}`}
                      href={tool.href}
                      role="option"
                      aria-selected={index === activeSearchIndex}
                      aria-disabled={unavailable || undefined}
                      className={`home-search-result${
                        index === activeSearchIndex ? " is-active" : ""
                      }${unavailable ? " is-unavailable" : ""}`}
                      onMouseEnter={() => setActiveSearchIndex(index)}
                      onClick={
                        unavailable
                          ? (event) => {
                              event.preventDefault();
                            }
                          : undefined
                      }
                      key={tool.id}
                    >
                      <span className="home-search-result__icon" aria-hidden="true">
                        <Icon size={19} />
                      </span>
                      <span>
                        <strong>{tool.title}</strong>
                        <small className="home-search-result__description">{tool.description}</small>
                        <small className="home-search-result__reason">{tool.matchReason}</small>
                      </span>
                      {unavailable ? <em>暂不可用</em> : <ArrowRight size={16} />}
                    </Link>
                  );
                })
              ) : (
                <div className="home-search-empty">
                  <div className="home-search-empty__head">
                    <span className="home-search-empty__icon" aria-hidden="true">
                      <Search size={20} />
                    </span>
                    <div>
                      <strong>没有找到精确匹配</strong>
                      <p>试试下面这些相近场景</p>
                    </div>
                    <button type="button" onClick={() => setQuery("")}>
                      清除搜索
                    </button>
                  </div>
                  <div className="home-search-recommendations">
                    {relatedScenarioTools.map((tool) => {
                      const capability = resolveRuntimeCapability(
                        runtime.data,
                        tool.runtimeCapability,
                      );
                      const unavailable =
                        capability !== undefined &&
                        capability.availability !== "available";
                      const Icon = tool.icon;

                      return (
                        <Link
                          href={tool.href}
                          className={`home-search-recommendation${
                            unavailable ? " is-unavailable" : ""
                          }`}
                          aria-disabled={unavailable || undefined}
                          onClick={
                            unavailable
                              ? (event) => {
                                  event.preventDefault();
                                }
                              : undefined
                          }
                          key={tool.id}
                        >
                          <span aria-hidden="true">
                            <Icon size={18} />
                          </span>
                          <span>
                            <strong>{tool.title}</strong>
                            <small>{tool.description}</small>
                          </span>
                          {unavailable ? <em>暂不可用</em> : <ArrowRight size={15} />}
                        </Link>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          ) : null}
          <div className="home-command__promises">
            <span>
              <Check size={13} /> 商品主图
            </span>
            <span>
              <Check size={13} /> 详情套图
            </span>
            <span>
              <Check size={13} /> 模特与场景
            </span>
          </div>
        </div>

        <header className="focus-home__section-head home-outcome-heading" id="outcomes">
          <h2>精修、模仿与跨境套图</h2>
          <p>主力工作台之外，这些工具也能帮你把图做好。</p>
        </header>

        <div className="home-feature-grid home-outcome-grid" ref={featureGridRef}>
          {featuredTools.map((tool) => {
              const capability = resolveRuntimeCapability(
                runtime.data,
                tool.runtimeCapability,
              );
              const unavailable =
                capability !== undefined &&
                capability.availability !== "available";
              const Icon = tool.icon;

              return (
                <Link
                  href={tool.href}
                  className={`home-feature-card ${tool.className}${
                    unavailable ? " is-unavailable" : ""
                  }`}
                  data-feature-id={tool.id}
                  data-feature-visual={HOME_FEATURE_VISUALS[tool.id]?.kind}
                  data-runtime-capability={tool.runtimeCapability}
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
                  key={tool.id}
                >
                  <HomeFeatureCardArt
                    featureId={tool.id}
                    FallbackIcon={Icon}
                  />
                  {HOT_FEATURE_IDS.has(tool.id) ? (
                    <span
                      className="home-feature-card__hot"
                      aria-label="热门功能"
                      title="热门功能"
                    >
                      <Flame size={16} fill="currentColor" />
                    </span>
                  ) : null}
                  <div>
                    <strong>{tool.title}</strong>
                    <span>{tool.description}</span>
                    <ArrowRight size={18} />
                  </div>
                </Link>
              );
          })}
        </div>

        <HomeAiToolbox />
      </div>
      <HaiyuProductShowcase />
      <HaiyuCreativeVoices />
      <HaiyuHomeFooter />
      <HaiyuHomeReveal />
    </section>
  );
}
