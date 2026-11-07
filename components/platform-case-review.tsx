"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowUpRight, CheckCircle2, Clock3, CircleAlert } from "lucide-react";
import { PLATFORM_CASE_REVIEW } from "@/lib/platform-case-review";
import styles from "./platform-case-review.module.css";
import { WorkspaceInputReview } from "./workspace-input-review";

const labels = { completed: "已取得真实输出", failed: "生成失败", processing: "处理中", partial: "部分交付 · 20 / 21" };

export default function PlatformCaseReviewPage() {
  const [active, setActive] = useState(0);
  const item = PLATFORM_CASE_REVIEW[active];
  return <main className={styles.page}>
    <header className={styles.top}><Link href="/" aria-label="返回本地首页"><img src="/brand/haiyu-icon-folded-h.png" alt="海宇绘品" width={48} height={48} /><strong>海宇绘品</strong></Link><span>本地案例验收 · 尚未替换正式站</span></header>
    <section className={styles.intro}><p>平台真实任务案例 / 2026.10.30</p><h1>从商品原图，<br />到商品展示成图。</h1><div>本轮展示 OLAY 身体乳真实复刻与已完成的品牌包装案例。原图、参考图和生成结果来自对应平台任务，点击图片可查看原始文件。</div></section>
    <WorkspaceInputReview />
    <div className={styles.tabs} role="tablist" aria-label="平台功能案例">
      {PLATFORM_CASE_REVIEW.map((entry, index) => <button type="button" key={entry.id} role="tab" id={`review-tab-${entry.id}`} aria-controls="review-panel" aria-selected={active === index} onClick={() => setActive(index)}><span>0{index + 1}</span><strong>{entry.title}</strong><small data-status={entry.status}>{entry.status === "completed" ? entry.results ? `${entry.results.length} 张` : "已出图" : entry.status === "failed" ? "失败" : entry.status === "partial" ? `${entry.results?.length} / 21` : "处理中"}</small></button>)}
    </div>
    <section className={styles.panel} role="tabpanel" id="review-panel" aria-labelledby={`review-tab-${item.id}`}>
      <div className={styles.panelHead}><div><span>{item.product}</span><h2>{item.title}</h2></div><span className={styles.status} data-status={item.status}>{item.status === "completed" ? <CheckCircle2 size={16} /> : item.status === "failed" ? <CircleAlert size={16} /> : <Clock3 size={16} />}{labels[item.status]}</span></div>
      <p className={styles.benefit}>{item.benefit}</p>
      {item.historyDate && <p className={styles.historyOrigin}>任务创建时间：{item.historyDate} · 已完成 {item.results?.length} / {item.results?.length} 张 <a href={item.taskUrl} target="_blank" rel="noopener noreferrer">打开原任务 ↗</a></p>}
      <div className={`${styles.comparison} ${item.results ? styles.packComparison : ""}`}>
        <div className={`${styles.inputs} ${item.inputs.length > 1 ? styles.twoInputs : ""}`}>
          {item.inputs.map(input => <figure key={input.src}><figcaption>{input.label}</figcaption><a href={input.src} target="_blank" rel="noopener noreferrer"><img src={input.src} alt={input.label} width={1086} height={1448} /></a></figure>)}
        </div>
        <figure className={styles.output}><figcaption>网站功能实际返回的{item.results ? ` ${item.results.length} 张` : ""}结果</figcaption>{item.results ? <div className={styles.packGrid}>{item.results.map(result => <a key={result.src} href={result.src} target="_blank" rel="noopener noreferrer"><img src={result.src} alt={`${item.title} · ${result.label}`} width={result.width} height={result.height} loading="lazy" /><span>{result.label}<small>{result.width} × {result.height}</small></span></a>)}</div> : item.result ? <a href={item.result} target="_blank" rel="noopener noreferrer"><img src={item.result} alt={`${item.title}的网站真实输出`} width={768} height={1024} /></a> : <div className={styles.empty} data-status={item.status}>{item.status === "failed" ? <CircleAlert size={34} /> : <Clock3 size={34} />}<h3>{item.status === "failed" ? "本次未取得成品" : "等待平台真实输出"}</h3><p>{item.note}</p></div>}</figure>
      </div>
      {!!item.additionalInputs?.length && <details className={styles.inputArchive}><summary>查看其余 {item.additionalInputs.length} 张原始输入与参考素材</summary><div>{item.additionalInputs.map(input => <figure key={input.src}><a href={input.src} target="_blank" rel="noopener noreferrer"><img src={input.src} alt={input.label} width={180} height={180} loading="lazy" /></a><figcaption>{input.label}</figcaption></figure>)}</div></details>}
      <div className={styles.evidence}><div><strong>{item.historyDate ? "任务原始文件核对" : item.result || item.results ? "本次结果" : "任务状态"}</strong><p>{item.note}</p><details><summary>{item.historyDate ? "任务设置" : "本次生成设置"}</summary><p>{item.settings}</p></details><code>{item.taskId}</code></div><a href={item.taskUrl} target="_blank" rel="noopener noreferrer">到网站核对原任务 <ArrowUpRight size={16} /></a></div>
    </section>
  </main>;
}
