"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { HaiyuQuickStartLinks } from "@/components/haiyu-start-hub";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  MessageCircle,
  MousePointer2,
  Search,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";

type Faq = {
  question: string;
  answer: string;
  keywords: string[];
};

const faqs: Faq[] = [
  {
    question: "如何上传一句话改图的原始图片？",
    answer: "进入一句话改图后，点击上传区域或把图片拖进页面即可。建议上传主体完整、清晰的 JPG 或 PNG 图片；缩略图加载完成后，再绘制需要修改的授权选区。",
    keywords: ["上传", "原始图片", "一句话改图", "拖拽", "文件"],
  },
  {
    question: "上传图片支持哪些格式和大小？",
    answer: "支持 JPG/JPEG、PNG、WebP；单张不超过 20MB、4000 万像素。建议图片主体清晰、不过度压缩。上传失败时，可先压缩文件体积、确认网络稳定后重新上传。",
    keywords: ["上传", "格式", "大小", "JPG", "PNG", "图片"],
  },
  {
    question: "上传后图片没有显示怎么办？",
    answer: "请先等待缩略图加载完成。如果长时间没有反应，请刷新页面后重新上传，并检查网络与文件格式；已成功提交的任务不会因刷新重复创建。",
    keywords: ["上传", "没有显示", "加载", "失败", "刷新", "网络"],
  },
  {
    question: "为什么上传时提示文件不符合要求？",
    answer: "通常是文件格式、文件体积或图片内容不符合当前工具要求。请优先换用清晰的 JPG 或 PNG 文件，并避免上传损坏、加密或过度压缩的图片。",
    keywords: ["上传", "文件", "不符合", "格式", "体积", "报错"],
  },
  {
    question: "一句话改图为什么需要确认授权选区？",
    answer: "选区是本次任务允许修改的边界。请尽量准确圈选需要调整的位置；选区外内容会被保留，因此不希望变化的元素不要包含在选区中。",
    keywords: ["选区", "授权", "一句话改图", "修改", "圈选"],
  },
  {
    question: "选区应该画多大才合适？",
    answer: "选区应完整覆盖需要改变的对象，并在边缘多留约 8–16px 的余量，方便自然融合。选得过大可能影响不相关内容，选得过小则可能留下明显边缘。",
    keywords: ["选区", "画多大", "边缘", "融合", "修改范围"],
  },
  {
    question: "提示词应该怎么写，结果才更接近预期？",
    answer: "先写清楚要改变什么，再说明要保留什么。例如“将背景改为浅灰色，保留人物脸部、发型和衣服纹理”。避免只写“改好看一点”这类笼统描述。",
    keywords: ["提示词", "描述", "怎么写", "预期", "一句话改图"],
  },
  {
    question: "上传多角度商品图时应该如何分组？",
    answer: "同一商品的正面、45 度和侧面照片请放在同一个分组。不同款式、颜色或规格要分开上传，避免系统把不同商品误判为同一组。",
    keywords: ["上传", "多角度", "商品图", "分组", "素材"],
  },
  {
    question: "多角度商品图应该如何拍摄？",
    answer: "建议保持相近的光线、焦段和背景，至少包含正面、45 度和侧面。不要在同一个商品分组中混入不同款式或规格。",
    keywords: ["多角度", "商品图", "拍摄", "素材", "角度"],
  },
  {
    question: "提交任务后在哪里查看进度？",
    answer: "提交后可前往任务中心查看排队、处理中和已完成状态。请勿因等待而重复点击提交；先确认列表中是否已有对应任务。",
    keywords: ["提交", "任务", "进度", "任务中心", "处理中", "排队"],
  },
  {
    question: "任务失败后会重复创建全部项目吗？",
    answer: "不会。已经完成的项目和结果会保留。你可以按照失败页面的提示，仅对失败的页面或任务单独重试。",
    keywords: ["任务", "失败", "重试", "详情图", "重复"],
  },
  {
    question: "生成完成后如何下载图片？",
    answer: "当任务显示已完成后，打开对应任务即可预览与下载结果。下载前请确认图片内容和尺寸是否符合你的使用场景。",
    keywords: ["下载", "生成", "完成", "结果", "图片"],
  },
  {
    question: "图片结果会被公开或用于展示吗？",
    answer: "当前工作台中的图片默认只在你的素材与任务中使用。对外展示、协作或分享功能会提供独立授权设置。",
    keywords: ["隐私", "公开", "图片", "数据", "展示"],
  },
  {
    question: "为什么任务一直在排队？",
    answer: "高峰期可能需要等待资源调度。请在任务中心查看最新状态，不要重复提交同一任务；如长时间没有变化，可通过在线联系提供任务状态以便协助查询。",
    keywords: ["排队", "任务", "等待", "处理中", "一直"],
  },
];

const guideFlows = [
  {
    icon: MousePointer2,
    label: "只改图片的一部分",
    shortLabel: "局部修图",
    badge: "唯一需要涂选",
    title: "一句话改图",
    description: "只修改你圈选的地方，选区外的内容会尽量保持不变。",
    steps: [
      { title: "上传原图", body: "上传 1 张清晰图片，等预览出现后再继续。" },
      { title: "圈出要改的位置", body: "只涂选希望发生变化的区域，选区外不需要额外处理。" },
      { title: "说清楚怎么改", body: "写明要改什么、要保留什么；提交后到任务中心查看结果。" },
    ],
    tip: "选区要盖住完整对象，边缘可稍微多留一点余量。",
  },
  {
    icon: Sparkles,
    label: "做一张商品图",
    shortLabel: "单张商品图",
    badge: "不需要手动涂选",
    title: "按效果选择商品图工具",
    description: "换色、换背景、白底图、试穿等功能，都会在页面提示需要上传哪些素材。",
    steps: [
      { title: "先选你想要的效果", body: "例如改颜色、换背景、做白底图、商品试穿或模特换姿势。" },
      { title: "按页面提示上传素材", body: "不同功能需要的图片不同；看到参考图入口时，再按需补充即可。" },
      { title: "填写要求并提交", body: "说明目标效果和要保留的细节，完成后到任务中心预览和下载。" },
    ],
    tools: ["商品换色", "更换背景", "电商白底图", "商品试穿", "模特换姿势"],
  },
  {
    icon: UploadCloud,
    label: "做一整套电商图",
    shortLabel: "电商套图",
    badge: "详情图 / 亚马逊套图",
    title: "一次完成一组电商素材",
    description: "适合商品详情图或亚马逊套图，需要先准备完整的商品和参考素材。",
    steps: [
      { title: "选择套图类型", body: "商品详情图会按页面规划逐页生成；亚马逊套图会生成固定的完整视觉套图。" },
      { title: "准备并上传完整素材", body: "按页面要求上传多角度商品图和必要参考素材，不要混入不同商品。" },
      { title: "确认规划后开始生成", body: "补充市场、卖点和品牌要求；随后在任务中心查看进度、预览和下载。" },
    ],
    tools: ["商品详情图", "亚马逊套图"],
  },
];
const featuredFaqCount = 6;

export default function HelpPage() {
  const [openFaq, setOpenFaq] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const [showAllFaqs, setShowAllFaqs] = useState(false);
  const [pendingScrollIndex, setPendingScrollIndex] = useState<number | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const [guideFlowIndex, setGuideFlowIndex] = useState<number | null>(null);
  const [contactOpen, setContactOpen] = useState(false);
  useBodyScrollLock(guideOpen || contactOpen);
  const faqRefs = useRef<Array<HTMLElement | null>>([]);

  const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
  const filteredFaqs = normalizedQuery
    ? faqs
        .map((item, index) => ({ item, index }))
        .filter(({ item }) => [item.question, item.answer, ...item.keywords].join(" ").toLocaleLowerCase().includes(normalizedQuery))
    : [];
  const visibleFaqs = showAllFaqs ? faqs : faqs.slice(0, featuredFaqCount);
  const activeGuideFlow = guideFlowIndex === null ? null : guideFlows[guideFlowIndex];

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.querySelector<HTMLInputElement>(".help-search input")?.focus();
      }
      if (event.key === "Escape") {
        setGuideOpen(false);
        setContactOpen(false);
        setSearchFocused(false);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (pendingScrollIndex === null) return;
    const frame = requestAnimationFrame(() => {
      faqRefs.current[pendingScrollIndex]?.scrollIntoView({ behavior: "smooth", block: "center" });
      setPendingScrollIndex(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [pendingScrollIndex, showAllFaqs]);

  const selectSearchResult = (index: number) => {
    setOpenFaq(index);
    setShowAllFaqs(true);
    setSearchQuery("");
    setSearchFocused(false);
    setPendingScrollIndex(index);
  };

  return (
    <AppShell>
      <div className="help-page">
        <section className="help-hero">
          <span><BookOpen size={18} /> 海宇绘品帮助中心</span>
          <h1>需要帮助？从这里开始</h1>
          <p>输入关键词查找问题；不确定从哪里开始，就打开使用指南按功能选择。</p>
          <div className="help-search-wrap">
            <label className="help-search">
              <Search size={21} aria-hidden="true" />
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                onFocus={() => setSearchFocused(true)}
                placeholder="搜索问题，例如：上传、选区、任务失败"
                aria-label="搜索帮助问题"
                aria-controls="help-search-results"
              />
              <kbd>Ctrl K</kbd>
            </label>
            {searchFocused && normalizedQuery ? (
              <div className="help-search-results" id="help-search-results" role="listbox" aria-label="帮助搜索结果">
                {filteredFaqs.length > 0 ? (
                  <>
                    <div className="help-search-results__summary">找到 {filteredFaqs.length} 个相关问题</div>
                    <div className="help-search-results__list">
                      {filteredFaqs.map(({ item, index }) => (
                        <button key={item.question} onMouseDown={() => selectSearchResult(index)} role="option" aria-selected="false">
                          <CircleHelp size={18} />
                          <span>
                            <strong>{item.question}</strong>
                            <small>{item.answer}</small>
                          </span>
                          <ArrowRight size={17} />
                        </button>
                      ))}
                    </div>
                  </>
                ) : (
                  <div className="help-search-empty">
                    <Search size={20} />
                    <div><strong>暂时没有找到相关问题</strong><span>换个说法试试，例如“上传”“选区”或“任务失败”。</span></div>
                  </div>
                )}
              </div>
            ) : null}
          </div>
        </section>

        <div className="help-page__content">
          <HaiyuQuickStartLinks />
          <section className="guide-entry-section" aria-labelledby="guide-entry-title">
            <div className="section-heading">
              <div>
                <span>使用指南</span>
                <h2 id="guide-entry-title">第一次使用？先选你要做的事</h2>
                <p>不同功能的操作不一样，打开指南后按你的任务类型查看对应步骤。</p>
              </div>
            </div>
            <button className="guide-launcher" onClick={() => { setGuideFlowIndex(null); setGuideOpen(true); }} aria-haspopup="dialog">
              <span className="guide-launcher__icon"><BookOpen size={26} /></span>
              <span className="guide-launcher__copy">
                <strong>打开使用指南</strong>
                <small>按“局部修改、单张生成、成套生成”选择操作路径</small>
              </span>
              <span className="guide-launcher__action">查看步骤 <ArrowRight size={18} /></span>
            </button>
          </section>

          <section className="faq-section" aria-labelledby="faq-title">
            <div className="section-heading">
              <div>
                <span>常见问题</span>
                <h2 id="faq-title">你可能正在寻找</h2>
                <p>点击问题查看答案。更多问题可以在上方搜索框中直接查找。</p>
              </div>
            </div>
            <div className="faq-layout">
              <div>
                <div className="faq-list">
                  {visibleFaqs.map((item, visibleIndex) => {
                    const index = showAllFaqs ? visibleIndex : visibleIndex;
                    return (
                      <article
                        key={item.question}
                        ref={(element) => { faqRefs.current[index] = element; }}
                        className={openFaq === index ? "open" : ""}
                        id={`faq-${index + 1}`}
                      >
                        <button onClick={() => setOpenFaq(openFaq === index ? -1 : index)} aria-expanded={openFaq === index}>
                          <strong>{item.question}</strong>
                          <ChevronDown size={20} />
                        </button>
                        <div><p>{item.answer}</p></div>
                      </article>
                    );
                  })}
                </div>
                {!showAllFaqs ? (
                  <button className="help-show-all" onClick={() => setShowAllFaqs(true)}>
                    查看全部 {faqs.length} 个常见问题 <ArrowRight size={18} />
                  </button>
                ) : (
                  <button className="help-show-all is-compact" onClick={() => setShowAllFaqs(false)}>
                    收起为精选问题 <ChevronDown size={18} />
                  </button>
                )}
              </div>
              <aside className="support-card">
                <span><MessageCircle size={25} /></span>
                <h3>仍然没有解决？</h3>
                <p>在线联系工作人员。添加企业微信后，请说明你的账号、任务状态和已经操作过的步骤。</p>
                <button className="primary-button" onClick={() => setContactOpen(true)} aria-haspopup="dialog">
                  在线联系
                  <ArrowRight size={17} />
                </button>
                <small>工作日 09:30–18:30 · 添加后会尽快回复</small>
              </aside>
            </div>
          </section>
        </div>
      </div>

      {guideOpen ? (
        <div className="help-modal-backdrop" role="presentation" onMouseDown={() => setGuideOpen(false)}>
          <section className="help-modal guide-modal" role="dialog" aria-modal="true" aria-labelledby="guide-modal-title" onMouseDown={(event) => event.stopPropagation()}>
            <header className="help-modal__header">
              <div>
                <span><BookOpen size={18} /> 使用指南</span>
                <h2 id="guide-modal-title">按功能完成创作</h2>
                <p>先选你要完成的任务类型，只看和你有关的 3 个步骤。</p>
              </div>
              <button className="help-modal__close" onClick={() => setGuideOpen(false)} aria-label="关闭使用指南"><X size={20} /></button>
            </header>
            <div className="guide-modal__body">
              {activeGuideFlow ? (
                <div className="guide-selected-flow">
                  <div className="guide-selected-flow__controls">
                    <button className="guide-back-button" onClick={() => setGuideFlowIndex(null)}>
                      <ArrowLeft size={17} /> 重新选择
                    </button>
                    <div className="guide-switcher" role="tablist" aria-label="切换任务类型">
                      {guideFlows.map((flow, flowIndex) => (
                        <button
                          key={flow.title}
                          className={guideFlowIndex === flowIndex ? "is-active" : ""}
                          onClick={() => setGuideFlowIndex(flowIndex)}
                          role="tab"
                          aria-selected={guideFlowIndex === flowIndex}
                        >
                          {flow.shortLabel}
                        </button>
                      ))}
                    </div>
                  </div>
                  <article className="guide-selected-flow__card" role="tabpanel">
                    <span className="guide-selected-flow__badge">{activeGuideFlow.badge}</span>
                    <h3>{activeGuideFlow.title}</h3>
                    <p>{activeGuideFlow.description}</p>
                    <div className="guide-stepper">
                      {activeGuideFlow.steps.map((step, stepIndex) => (
                        <div className="guide-step" key={step.title}>
                          <span className="guide-step__number">{stepIndex + 1}</span>
                          <div>
                            <strong>{step.title}</strong>
                            <p>{step.body}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                    {activeGuideFlow.tools ? (
                      <div className="guide-tool-chips" aria-label="相关功能">
                        {activeGuideFlow.tools.map((tool) => <span key={tool}>{tool}</span>)}
                      </div>
                    ) : null}
                    {activeGuideFlow.tip ? (
                      <div className="guide-flow-tip"><CheckCircle2 size={17} /> <span>{activeGuideFlow.tip}</span></div>
                    ) : null}
                  </article>
                </div>
              ) : (
                <div className="guide-picker">
                  {guideFlows.map((flow, flowIndex) => {
                    const Icon = flow.icon;
                    return (
                      <button className="guide-choice" key={flow.title} onClick={() => setGuideFlowIndex(flowIndex)}>
                        <span className="guide-choice__icon"><Icon size={25} /></span>
                        <strong>{flow.label}</strong>
                        <small>{flow.shortLabel}</small>
                        <p>{flow.badge}</p>
                        <span className="guide-choice__action">查看步骤 <ArrowRight size={17} /></span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <footer className="help-modal__footer">
              <span>遇到问题？可以回到常见问题中搜索关键词。</span>
              <button className="secondary-button" onClick={() => setGuideOpen(false)}>知道了</button>
            </footer>
          </section>
        </div>
      ) : null}

      {contactOpen ? (
        <div className="help-modal-backdrop" role="presentation" onMouseDown={() => setContactOpen(false)}>
          <section className="help-modal contact-modal" role="dialog" aria-modal="true" aria-labelledby="contact-modal-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className="help-modal__close contact-modal__close" onClick={() => setContactOpen(false)} aria-label="关闭在线联系"><X size={20} /></button>
            <div className="contact-modal__icon"><MessageCircle size={26} /></div>
            <h2 id="contact-modal-title">在线联系</h2>
            <p><span>请扫描二维码，添加海宇数科微信客服。</span><span>添加时可以备注你的账号和遇到的问题。</span></p>
            <div className="contact-qr"><Image src="/haiyu-contact-qr.jpg" alt="海宇数科微信客服二维码" width={330} height={330} unoptimized /></div>
            <strong>扫码添加微信客服</strong>
            <a href="https://work.weixin.qq.com/kfid/kfc1f8fc41acaca0895" target="_blank" rel="noopener noreferrer">直接联系在线客服</a>
            <small>售后咨询请提供订单号及问题说明，我们会尽快回复。</small>
          </section>
        </div>
      ) : null}
    </AppShell>
  );
}
