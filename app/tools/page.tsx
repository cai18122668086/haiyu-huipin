"use client";

import { ArrowRight, ArrowUpRight, Lightbulb, Search, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { FEATURE_CATEGORY_LABELS, FEATURE_REGISTRY, type FeatureCategory } from "@/lib/feature-registry";
import { FEATURE_THUMBNAILS, PRIMARY_CREATION_ENTRIES } from "@/lib/creation-entrypoints";
import styles from "./tools.module.css";
import { FeatureCaseArt } from "@/components/feature-case-art";
import { getFeatureCaseArt } from "@/lib/feature-case-art";

const categories: FeatureCategory[] = ["commerce_pack", "quick_edit", "product_image"];
const spotlightEntries = [PRIMARY_CREATION_ENTRIES[0], PRIMARY_CREATION_ENTRIES[2]];

export default function ToolsPage() {
  const [query, setQuery] = useState("");
  const [activeCategory, setCategory] = useState<FeatureCategory | "all">("all");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleFeatures = useMemo(() => FEATURE_REGISTRY.filter(feature => {
    if (activeCategory !== "all" && feature.category !== activeCategory) return false;
    const entry = PRIMARY_CREATION_ENTRIES.find(item => item.id === feature.id);
    return [entry?.label, feature.title, feature.shortTitle, feature.description, feature.inputHint, feature.outputHint, ...feature.searchKeywords].join(" ").toLocaleLowerCase().includes(normalizedQuery);
  }), [normalizedQuery, activeCategory]);

  return <AppShell>
    <div className={styles.page}>
      <Link href="/inspiration" className={styles.inspirationBanner}>
        <span><Sparkles size={17} /> 好创意，从一张喜欢的图开始</span>
        <strong>去灵感专区看看 <ArrowRight size={16} /></strong>
        <div aria-hidden="true"><img src="/images/haiyu/fashion.webp" alt="" /><img src={PRIMARY_CREATION_ENTRIES[2].image} alt="" /><img src={PRIMARY_CREATION_ENTRIES[0].image} alt="" /></div>
      </Link>
      <header className={styles.welcome}><div><p>你的电商视觉创作空间</p><h1>今天，想创作什么？</h1></div><span>{FEATURE_REGISTRY.length} 项工具，陪你的好商品出彩</span></header>
      <section className={styles.spotlights} aria-label="推荐创作功能">
        {spotlightEntries.map((entry, index) => <Link key={entry.id} href={entry.href} className={styles.spotlight} data-tone={index === 0 ? "lilac" : "lime"}>
          <div><span>{index === 0 ? "有参考，做同款" : "讲清每一个卖点"}</span><h2>{entry.label}</h2><p>{entry.description}</p><b>立即体验 <ArrowUpRight size={16} /></b></div>
          {getFeatureCaseArt(entry.id) ? <span className={styles.spotlightArt}><FeatureCaseArt featureId={entry.id} /></span> : <img src={entry.image} alt={entry.label + "视觉参考"} width={1374} height={1145} />}
        </Link>)}
      </section>
      <div className={styles.quickLinks}>
        <Link href={PRIMARY_CREATION_ENTRIES[1].href}><img src={PRIMARY_CREATION_ENTRIES[1].image} alt="" /><span><strong>准备上新？制作主图套图</strong><small>1 张搜索主图 + 5 张连续轮播</small></span><ArrowUpRight size={17} /></Link>
        <Link href="/inspiration"><span className={styles.quickIcon}><Lightbulb size={21} /></span><span><strong>还没方向？先来找灵感</strong><small>按品类看案例，带着创作指令开始</small></span><ArrowUpRight size={17} /></Link>
      </div>
      <section className={styles.directory} aria-labelledby="tools-directory-title">
        <div className={styles.directoryHead}><h2 id="tools-directory-title">找到顺手的创作工具</h2><label className={styles.searchBox}><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="试试：换背景、模特、主图…" aria-label="搜索 AI 创作功能" />{query && <button type="button" onClick={() => setQuery("")} aria-label="清空搜索"><X size={15} /></button>}</label></div>
        <div className={styles.filterBar}><div className={styles.filters} role="group" aria-label="功能分类"><button type="button" aria-pressed={activeCategory === "all"} onClick={() => setCategory("all")}>全部工具</button>{categories.map(category => <button key={category} type="button" aria-pressed={activeCategory === category} onClick={() => setCategory(category)}>{FEATURE_CATEGORY_LABELS[category]}</button>)}</div><span aria-live="polite">{visibleFeatures.length} 项功能</span></div>
        {visibleFeatures.length ? <div className={styles.grid}>
          {visibleFeatures.map(feature => {
            const entry = PRIMARY_CREATION_ENTRIES.find(item => item.id === feature.id);
            return <Link key={feature.id} href={feature.route} className={styles.toolCard}>
              <div className={styles.cardTitle}><h3>{entry?.label ?? feature.title}</h3>{entry && <span>推荐</span>}</div>
              <div className={styles.cardImage}>{getFeatureCaseArt(feature.id) ? <div className={styles.caseArt}><FeatureCaseArt featureId={feature.id} portrait /></div> : <img src={FEATURE_THUMBNAILS[feature.id]} alt={feature.title + "创作方向"} loading="lazy" decoding="async" width={480} height={400} />}<span><ArrowUpRight size={20} /></span></div>
              <p>{feature.description}</p><div className={styles.cardFooter}><span>立即制作</span><ArrowRight size={14} /></div>
            </Link>;
          })}
        </div> : <div className={styles.emptyState}><Search size={25} /><h3>没有找到匹配的工具</h3><p>换个关键词，或查看其他分类。</p><button type="button" onClick={() => { setQuery(""); setCategory("all"); }}>查看全部功能</button></div>}
      </section>
    </div>
  </AppShell>;
}
