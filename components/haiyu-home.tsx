"use client";

import { DisplayImage } from "./display-image";

import Link from "next/link";
import { ArrowDown, ArrowRight, ArrowUpRight, Check, MoveUpRight, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { BrandMark } from "./brand-mark";
import { HOME_FEATURE_VISUALS } from "@/lib/home-feature-visuals";
import { FeatureCaseArt } from "./feature-case-art";

export function HaiyuHero() {
  const [paused, setPaused] = useState(false);
  const heroRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const hero = heroRef.current;
    if (!hero || paused || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    const current = { x: hero.clientWidth * .72, y: hero.clientHeight * .68 };
    let target = { ...current };
    const draw = () => {
      current.x += (target.x - current.x) * .095;
      current.y += (target.y - current.y) * .095;
      hero.style.setProperty("--wash-x", `${current.x.toFixed(1)}px`);
      hero.style.setProperty("--wash-y", `${current.y.toFixed(1)}px`);
      if (Math.abs(target.x - current.x) + Math.abs(target.y - current.y) > .3) {
        frame = requestAnimationFrame(draw);
      } else frame = 0;
    };
    const move = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      const rect = hero.getBoundingClientRect();
      target = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      hero.classList.add("is-pointer-active");
      if (!frame) frame = requestAnimationFrame(draw);
    };
    const leave = () => {
      target = { x: hero.clientWidth * .72, y: hero.clientHeight * .68 };
      hero.classList.remove("is-pointer-active");
      if (!frame) frame = requestAnimationFrame(draw);
    };
    hero.addEventListener("pointermove", move, { passive: true });
    hero.addEventListener("pointerleave", leave);
    return () => {
      cancelAnimationFrame(frame);
      hero.removeEventListener("pointermove", move);
      hero.removeEventListener("pointerleave", leave);
      hero.classList.remove("is-pointer-active");
    };
  }, [paused]);

  return (
    <section ref={heroRef} className={`haiyu-hero${paused ? " is-paused" : ""}`} aria-labelledby="haiyu-hero-title">
      <div className="haiyu-color-wash" aria-hidden="true"><i /><i /><i /></div>
      <div className="haiyu-hero__wordmark" aria-hidden="true">HAIYU</div>
      <div className="haiyu-hero__inner">
        <div className="haiyu-hero__copy">
          <p className="haiyu-eyebrow"><span /> 为电商而生的 AI 创作空间</p>
          <h1 id="haiyu-hero-title">让好商品，<br />一眼<span className="haiyu-selection">出彩<i /><i /><i /><i /></span>。</h1>
          <p className="haiyu-hero__description">参考同行做套图，上传商品做主图与详情。<br />从灵感到上架视觉，找到适合你的创作方式。</p>
          <div className="haiyu-hero__actions">
            <Link className="haiyu-button" href="/style-replica"><Sparkles size={18} /> 开始一键复刻 <ArrowUpRight size={20} /></Link>
            <a className="haiyu-text-link" href="#start">选一个创作目标 <ArrowDown size={16} /></a>
          </div>
          <p className="haiyu-hero__note">一键复刻 · 主图套图 · 详情图套图 · 灵感专区</p>
        </div>
        <div className="haiyu-collage" aria-label="商品视觉创作">
          <div className="haiyu-collage__shape" aria-hidden="true" />
          <span className="haiyu-collage__spark" aria-hidden="true">✳</span>
          <Link href="/style-replica" className="haiyu-photo haiyu-photo--fashion">
            <div className="haiyu-photo__bar"><span /><span /><span /><small>LOOKBOOK / 01</small></div>
            <DisplayImage src="/images/cases/olay-20261030/replica-07.png" alt="OLAY 身体乳复刻任务中的人物场景成图" width={1055} height={1491} fetchPriority="high" sizes="(max-width: 640px) 48vw, 320px" />
            <span className="haiyu-photo__caption">从实拍，到品牌视觉 <ArrowUpRight size={18} /></span>
          </Link>
          <Link href="/workspace/product-image/change-background" className="haiyu-photo haiyu-photo--product">
            <DisplayImage src="/images/cases/platform-v3/background-result.png" sizes="(max-width: 640px) 42vw, 300px" alt="耳机更换背景任务的真实成图" width={768} height={1024} />
            <span className="haiyu-photo__caption">给商品一个新场景 <MoveUpRight size={17} /></span>
          </Link>
          <Link href="/workspace/domestic-main-image" className="haiyu-photo haiyu-photo--commerce">
            <span className="haiyu-photo__tag">PRODUCT VISUAL</span>
            <DisplayImage src="/images/cases/history-20261030/asset_a47a47f308464d7ea2e928e8cc9f5c0c.png" sizes="(max-width: 640px) 48vw, 320px" alt="猫粮国内主图任务的成图" width={1254} height={1254} />
            <span className="haiyu-photo__caption">商品主图，一眼心动 <ArrowUpRight size={16} /></span>
          </Link>
          <span className="haiyu-collage__sticker"><Sparkles size={15} /> 好创意，即刻成图</span>
          <span className="haiyu-collage__cursor" aria-hidden="true"><svg viewBox="0 0 32 36"><path d="M3 2v28l8-8 6 12 6-3-6-12h12Z" /></svg><b>海宇绘品</b></span>
        </div>
      </div>
      <div className="haiyu-hero__bottom"><span>让商品的每一面，都值得被看见。</span><span>SCROLL TO CREATE <ArrowDown size={13} /></span><button type="button" onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? "播放动效" : "暂停动效"}</button></div>
    </section>
  );
}

/** Content stays visible when scripting or reduced-motion animations are disabled. */
export function HaiyuHomeReveal() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const nodes = document.querySelectorAll<HTMLElement>(".home-feature-card, .home-ai-toolbox__item, .haiyu-closing, .haiyu-showcase__head, .haiyu-comparison, .haiyu-value__copy");
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-revealed");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08 });
    nodes.forEach((node, index) => {
      node.style.setProperty("--reveal-delay", `${(index % 3) * 65}ms`);
      node.classList.add("haiyu-reveal");
      observer.observe(node);
    });
    return () => {
      observer.disconnect();
      nodes.forEach((node) => node.classList.remove("haiyu-reveal"));
    };
  }, []);
  return null;
}

const SHOWCASE_TOOLS = [
  { id: "change-background", label: "更换背景", title: "同一件商品，不止一种好场景。", description: "告别单调的展示背景，让光影、材质与空间一起衬托商品。", href: "/workspace/product-image/change-background" },
  { id: "local-edit", label: "一句话改图", title: "小小的改变，也能让质感出彩。", description: "圈出要修改的局部，用自然语言表达想法，让商品细节更贴近你的创意。", href: "/workspace/local-edit" },
  { id: "reference-remix", label: "一键模仿", title: "好灵感，变成自己的商品视觉。", description: "借鉴参考图的构图与氛围，为自己的商品找到新的表达方式。", href: "/workspace/reference-remix" },
] as const;

// The latest homepage promotes replica instead of background editing. Keep this
// demonstration independent of the featured-card selection.
const BACKGROUND_VISUAL = {
  primary: { desktop: { src: "/images/cases/platform-v3/background-result.png" } },
  input: { desktop: { src: "/images/cases/platform-v3/02-headphones-messy.png" } },
};

const GALLERY_IMAGES = [
  [
    "/images/cases/olay-20261030/replica-07.png",
    "OLAY 身体乳场景复刻"
  ],
  [
    "/images/cases/history-20261030/asset_6f416209332b45b285e07c3f91a6124b.png",
    "猫粮详情图"
  ],
  [
    "/images/cases/platform-v3/background-result.png",
    "耳机场景"
  ],
  [
    "/images/cases/platform-v2/try-on-result.png",
    "针织开衫试穿"
  ],
  [
    "/images/cases/platform-v3/color-result.png",
    "皮革手提包换色"
  ],
  [
    "/images/cases/olay-20261030/replica-06.png",
    "身体乳横向视觉"
  ]
] as const;

export function HaiyuProductShowcase() {
  const [active, setActive] = useState(0);
  const [split, setSplit] = useState(50);
  const [galleryPaused, setGalleryPaused] = useState(false);
  const tool = SHOWCASE_TOOLS[active];
  const visual = tool.id === "change-background" ? BACKGROUND_VISUAL : HOME_FEATURE_VISUALS[tool.id];
  return (
    <div className="haiyu-showcase">
      <section className="haiyu-showcase__section" aria-labelledby="haiyu-showcase-title">
        <header className="haiyu-showcase__head"><span className="haiyu-eyebrow">A LITTLE AI. A LOT OF POSSIBILITIES.</span><h2 id="haiyu-showcase-title">原图很普通？<br />下一张，可以很惊艳。</h2><p>从商品本身出发，让好想法变成看得见的好效果。</p></header>
        <div className="haiyu-showcase__tabs" role="tablist" aria-label="创作效果">
          {SHOWCASE_TOOLS.map((item, index) => <button key={item.id} id={`showcase-tab-${item.id}`} type="button" role="tab" aria-selected={active === index} aria-controls="haiyu-showcase-panel" tabIndex={active === index ? 0 : -1} onClick={() => { setActive(index); setSplit(50); }} onKeyDown={(event) => {
            const next = event.key === "ArrowRight" ? (active + 1) % SHOWCASE_TOOLS.length : event.key === "ArrowLeft" ? (active + SHOWCASE_TOOLS.length - 1) % SHOWCASE_TOOLS.length : event.key === "Home" ? 0 : event.key === "End" ? SHOWCASE_TOOLS.length - 1 : undefined;
            if (next !== undefined) { event.preventDefault(); setActive(next); setSplit(50); document.getElementById(`showcase-tab-${SHOWCASE_TOOLS[next].id}`)?.focus(); }
          }}>{item.label}<ArrowUpRight size={14} /></button>)}
        </div>
        <div className="haiyu-showcase__panel" role="tabpanel" id="haiyu-showcase-panel" aria-labelledby={`showcase-tab-${tool.id}`}>
          <div className="haiyu-comparison">
            <div className="haiyu-comparison__images" key={tool.id}>
              <DisplayImage sizes="(max-width: 640px) 90vw, 640px" className="haiyu-comparison__after" src={visual.primary.desktop.src} alt={`${tool.label}创作效果`} width={1374} height={1145} loading="lazy" />
              <DisplayImage sizes="(max-width: 640px) 90vw, 640px" className="haiyu-comparison__before" src={visual.input.desktop.src} style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }} alt={`${tool.label}商品原图`} width={1374} height={1145} loading="lazy" />
              <span className="haiyu-comparison__label haiyu-comparison__label--before">商品原图</span><span className="haiyu-comparison__label haiyu-comparison__label--after"><Sparkles size={12} /> 创作效果</span>
              <div className="haiyu-comparison__divider" style={{ left: `${split}%` }} aria-hidden="true"><span>‹ ›</span></div>
              <input type="range" min={0} max={100} value={split} onChange={(event) => setSplit(Number(event.target.value))} aria-label="拖动查看原图与效果图" aria-valuetext={`原图显示 ${split}%，效果图显示 ${100 - split}%`} />
            </div>
            <span className="haiyu-comparison__hint">左右拖动，看看变化 <span>↔</span></span>
          </div>
          <div className="haiyu-showcase__copy" key={tool.id}><span className="haiyu-eyebrow">0{active + 1} / PRODUCT TRANSFORMATION</span><h3>{tool.title}</h3><p>{tool.description}</p><Link className="haiyu-button" href={tool.href}>试试{tool.label} <ArrowUpRight size={18} /></Link></div>
        </div>
      </section>
      <section className={`haiyu-gallery${galleryPaused ? " is-paused" : ""}`} aria-labelledby="haiyu-gallery-title">
        <div className="haiyu-gallery__head"><div><span className="haiyu-eyebrow">MORE THAN ONE GREAT SHOT</span><h2 id="haiyu-gallery-title">每个品类，都有自己的高光。</h2></div><button type="button" onClick={() => setGalleryPaused(!galleryPaused)} aria-pressed={galleryPaused}>{galleryPaused ? "播放案例墙" : "暂停案例墙"}</button></div>
        <div className="haiyu-gallery__window"><div className="haiyu-gallery__track">{[0, 1].map((copy) => <div className="haiyu-gallery__set" key={copy} aria-hidden={copy === 1 || undefined}>{GALLERY_IMAGES.map(([src, label], index) => <Link href="/inspiration" tabIndex={copy === 1 ? -1 : undefined} className={`haiyu-gallery__image haiyu-gallery__image--${index % 3}`} key={src}><DisplayImage src={src} alt={copy ? "" : `${label}创作效果`} loading="lazy" width={380} height={430} /><span>{label}<ArrowUpRight size={16} /></span></Link>)}</div>)}</div></div>
        <Link className="haiyu-text-link" href="/inspiration">去灵感专区看看 <ArrowRight size={16} /></Link>
      </section>
      <section className="haiyu-value">
        <div className="haiyu-value__art" aria-label="商品从单张主图到详情套图的创作效果"><div className="haiyu-value__sheet"><span>01 / MAIN VISUAL</span><DisplayImage src="/images/cases/history-20261030/asset_a47a47f308464d7ea2e928e8cc9f5c0c.png" alt="猫粮搜索主图成图" width={1374} height={1145} loading="lazy" /></div><div className="haiyu-value__sheet"><span>02 / PRODUCT DETAILS</span><DisplayImage src="/images/cases/history-20261030/asset_6f416209332b45b285e07c3f91a6124b.png" alt="猫粮详情页成图" width={1374} height={1145} loading="lazy" /></div><span className="haiyu-value__seal"><Sparkles size={19} /> 从好图<br />到好生意</span></div>
        <div className="haiyu-value__copy"><span className="haiyu-eyebrow">CREATE MORE. MAKE IT YOURS.</span><h2>少一点反复，<br />多一点生意的可能。</h2><p>把精力留给选品、运营和品牌。让海宇绘品，陪你把每一次上新的视觉做好。</p><ul><li><Check size={16} /><span><strong>让上新更从容</strong>主图、详情与场景素材，在同一处开始创作。</span></li><li><Check size={16} /><span><strong>让灵感多走一步</strong>从参考构图到商品细节，快速尝试不同表达。</span></li><li><Check size={16} /><span><strong>让每一份素材更有价值</strong>同一件商品，探索适合不同展示场景的视觉。</span></li></ul><Link className="haiyu-text-link" href="/workspace/detail-pack">开始制作商品详情图 <ArrowRight size={17} /></Link></div>
      </section>
    </div>
  );
}

const CREATIVE_ROLES = [
  { role: "店铺运营", avatar: "operations", title: "我的每次上新，都想有好视觉", text: "我最在意商品能不能第一眼抓住人。主图把质感拍出来，详情再把卖点讲清楚，整套看起来统一，上新才更有底气。", tag: "主图 · 换背景" },
  { role: "服饰商家", avatar: "fashion", title: "我想把穿搭灵感，直接展示出来", text: "同一件衣服，我总想多试几种模特和场景。不只是让买家看清款式，更希望他们能想象，穿在自己身上是什么感觉。", tag: "模特 · 商品试穿" },
  { role: "品牌主理人", avatar: "brand", title: "我的商品，要有自己的表达", text: "看到好构图，我也会心动。但我更想保留自己商品的特点，把喜欢的氛围变成品牌自己的视觉，而不是千篇一律的套版。", tag: "参考图 · 一键模仿" },
  { role: "跨境卖家", avatar: "crossborder", title: "我想用一整套图，把商品讲明白", text: "我的主图要吸引注意，副图要说明细节，A+ 还要讲好使用场景。把这些内容放在一起规划，才更像是在完整地介绍一个商品。", tag: "亚马逊套图" },
  { role: "电商设计师", avatar: "designer", title: "我想把时间，留给更好的创意", text: "有了方向，我就想多试几步。换个背景、调整一个细节、参考一种光影，让 AI 帮我展开思路，我再把注意力放回商品和表达。", tag: "一句话改图" },
  { role: "家居商家", avatar: "home", title: "我卖的不只是物件，也是一种生活", text: "我希望商品走进合适的居家场景。自然的光、舒服的空间，让买家一眼就能想到，它放在自己家里会是什么样子。", tag: "场景主图" },
] as const;

export function HaiyuCreativeVoices() {
  const [paused, setPaused] = useState(false);
  return (
    <section className={`haiyu-reviews${paused ? " is-paused" : ""}`} aria-labelledby="haiyu-reviews-title">
      <header><span className="haiyu-eyebrow">MADE FOR EVERY CREATIVE SELLER</span><h2 id="haiyu-reviews-title">不同的生意，<br />同样期待下一张好图。</h2><p>我的商品，我的灵感，我期待的好呈现。</p></header>
      <div className="haiyu-reviews__window"><div className="haiyu-reviews__track">
        {[0, 1].map((copy) => <div className="haiyu-reviews__set" key={copy} aria-hidden={copy === 1 || undefined}>
          {CREATIVE_ROLES.map((review) => <article key={review.role}>
            <div className="haiyu-reviews__stars"><Sparkles size={14} /> CREATE YOUR NEXT</div>
            <h3>{review.title}</h3><p className="haiyu-reviews__text">“{review.text}”</p>
            <div className="haiyu-reviews__person"><DisplayImage src={`/images/haiyu/avatars/${review.avatar}.webp`} alt="" width={42} height={42} loading="lazy" /><div><strong>{review.role}</strong><small>{review.tag}</small></div></div>
          </article>)}
        </div>)}
      </div></div>
      <div className="haiyu-reviews__foot"><button type="button" onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? "播放创作故事" : "暂停创作故事"}</button></div>
    </section>
  );
}

export function HaiyuReplicaCardArt() {
  return <span className="home-feature-card__art case-art-shell"><FeatureCaseArt featureId="one-click-replica" /></span>;
}

export function HaiyuHomeFooter() {
  return (
    <footer className="haiyu-home-footer">
      <section className="haiyu-closing">
        <span className="haiyu-eyebrow">YOUR NEXT GREAT VISUAL</span>
        <h2>下一张好图，<br />从你的商品开始。</h2>
        <Link className="haiyu-button" href="/tools">找到你的创作工具 <ArrowRight size={19} /></Link>
        <span className="haiyu-closing__flower" aria-hidden="true">✳</span>
      </section>
      <div className="haiyu-home-footer__row"><Link href="/" aria-label="海宇绘品首页"><BrandMark /></Link><p>让好商品，一眼出彩。</p><nav aria-label="页脚导航"><Link href="/help">帮助中心</Link><Link href="/legal/privacy">隐私政策</Link><Link href="/legal/terms">服务条款</Link></nav></div>
    </footer>
  );
}
