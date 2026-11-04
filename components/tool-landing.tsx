"use client";

import Link from "@/components/seo-link";
import { ArrowRight, Check, Upload } from "lucide-react";
import { AppShell } from "./app-shell";
import { DisplayImage } from "./display-image";
import { StructuredData } from "./seo-structured-data";
import { getFeatureById } from "@/lib/feature-registry";
import { getFeatureCaseArt } from "@/lib/feature-case-art";
import { getToolSeoPage, SEO_PAGES, SITE_ORIGIN } from "@/lib/seo/pages";
import { requestLogin } from "@/lib/auth/session-events";
import { currentReturnTo } from "@/lib/auth/return-to";
import styles from "./tool-landing.module.css";

export function ToolLanding({ pathname }: { pathname: string }) {
  const page = getToolSeoPage(pathname);
  if (!page) return null;
  const feature = getFeatureById(page.featureId);
  const example = getFeatureCaseArt(page.featureId);
  const related = SEO_PAGES.filter(item => item.tool && item.path !== pathname).slice(0, 4);
  const start = () => requestLogin(currentReturnTo());
  return <AppShell sidebar={false}>
    <article className={styles.page} data-public-tool={page.path}>
      <StructuredData data={{
        "@context": "https://schema.org", "@type": "WebApplication",
        name: page.h1, description: page.description, url: SITE_ORIGIN + page.path,
        applicationCategory: "DesignApplication", operatingSystem: "Web", inLanguage: "zh-CN",
        provider: { "@type": "Organization", name: "海宇数科（广东横琴）科技有限公司", url: "https://www.haiyushuke.com/" },
      }} />
      <nav className={styles.breadcrumb} aria-label="面包屑"><Link href="/">首页</Link><span>/</span><Link href="/tools">全部工具</Link><span>/</span><span>{page.name}</span></nav>
      <header className={styles.hero}>
        <div className={styles.intro}>
          <p className={styles.eyebrow}>海宇绘品 · 电商图片创作</p>
          <h1>{page.h1}</h1><p className={styles.description}>{page.description}</p>
          <div className={styles.actions}>
            <button type="button" className="primary-button" onClick={start}><Upload size={18} />上传图片</button>
            <button type="button" className="secondary-button" onClick={start}>开始制作<ArrowRight size={17} /></button>
          </div>
          <p className={styles.note}>先了解效果，登录后上传自己的商品开始创作。</p>
        </div>
        {example && <section className={styles.example} aria-label={example.outputs.length ? "原图与效果对比" : "上传素材示例"}>
          <div className={styles.comparison}>
            <figure><span>{example.inputLabel}</span><div className={styles.inputImages}>{example.inputs.map(input => <DisplayImage key={input.src} src={input.src} alt={page.h1 + "：" + input.label} width={600} height={600} />)}</div><figcaption>清晰、完整的商品素材</figcaption></figure>
            {example.outputs.length ? <figure><span>{example.outputLabel}</span><DisplayImage src={example.outputs[0]} alt={page.h1 + "效果示例"} width={600} height={600} /><figcaption>示例效果，实际结果取决于素材与要求</figcaption></figure> : <div className={styles.goal}><Check size={26} /><h2>创作目标</h2><p>{feature?.description}</p><p>{feature?.guidance}</p></div>}
          </div>
        </section>}
      </header>
      <section className={styles.section} aria-labelledby="tool-introduction">
        <h2 id="tool-introduction">用{page.name}完成你的商品视觉</h2>
        <div className={styles.benefits}>
          <div><h3>准备素材</h3><p>{feature?.inputHint ?? "准备自己的商品实拍图，以及有权使用的参考链接或图片。"}</p></div>
          <div><h3>说明要求</h3><p>{feature?.guidance ?? "说明商品卖点、目标平台与希望保留的细节，结合参考图确定构图和视觉方向。"}</p></div>
          <div><h3>查看与下载</h3><p>{feature?.outputHint ?? "查看生成的商品套图"}。核对商品、文字与规格信息后，再用于上架或推广。</p></div>
        </div>
      </section>
      <section className={styles.section} aria-labelledby="tool-steps"><h2 id="tool-steps">如何开始使用</h2>
        <ol className={styles.steps}>
          <li><span>01</span><div><h3>上传商品素材</h3><p>点击上传图片，登录后进入工作区。选择主体清晰、细节完整且有权使用的素材。</p></div></li>
          <li><span>02</span><div><h3>填写制作要求</h3><p>补充商品信息、目标效果与参考素材，按工具提示选择尺寸和制作方式。</p></div></li>
          <li><span>03</span><div><h3>检查结果并下载</h3><p>查看生成图片，核对商品外观和文案；必要时调整要求，再下载需要的成果。</p></div></li>
        </ol>
      </section>
      <section className={styles.section} aria-labelledby="tool-faq"><h2 id="tool-faq">常见问题</h2>
        <details><summary>需要先登录才能了解功能吗？</summary><p>不需要。你可以直接查看本页的功能介绍和示例，点击上传或开始制作后再登录。</p></details>
        <details><summary>怎样准备适合{page.name}的素材？</summary><p>{feature?.inputHint ?? "商品原图与参考图分开提供"}。尽量使用清晰原图，并说明需要保留的商品结构、颜色和文字信息。</p></details>
        <details><summary>生成图片后还需要检查吗？</summary><p>需要。AI结果可能出现细节或文字偏差，请检查商品一致性、参数与平台要求，再决定是否发布。具体费用以工作区展示为准。</p></details>
      </section>
      <section className={styles.section}><h2>继续探索电商图片工具</h2><div className={styles.related}>{related.map(item => <Link key={item.path} href={item.path} title={item.h1}>{item.name}<ArrowRight size={15} /></Link>)}<Link href="/help">查看使用指南<ArrowRight size={15} /></Link></div></section>
    </article>
  </AppShell>;
}
