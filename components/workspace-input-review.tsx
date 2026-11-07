"use client";

import { useState } from "react";
import Link from "next/link";
import { FEATURE_REGISTRY } from "@/lib/feature-registry";
import { PRODUCT_IMAGE_INPUT_GUIDES } from "@/lib/product-image-input-guide";
import { RETAINED_WORKSPACE_EXAMPLES } from "@/lib/workspace-input-examples";
import { ProductInputExamples } from "./product-input-examples";
import { WorkspaceVisualPreview } from "./workspace-visual-preview";
import styles from "./workspace-input-review.module.css";

const otherRoles: Record<string, string[]> = {
  "local-edit": ["上传需要局部修复的原图", "在原图上涂选允许修改的区域", "填写选区内需要怎样修改"],
  "reference-remix": ["我的商品图：锁定自己商品的外观", "参考图：提供想模仿的构图与光影，每个角色各一张"],
  "one-click-replica": ["自己的产品图：决定最终商品身份", "商品链接或风格参考图：决定想模仿的画面", "一比一模仿和风格复刻分别选择"],
  "model-pose-transfer": ["模特服装原图：保留人物、衣服和环境", "目标姿势：从姿势库选择，或上传另一张姿势参考图"],
  "creative-image": ["自己的商品照片：展示商品本身与关键细节", "竞品参考可选；另选创意方向并填写商品卖点"],
  "detail-pack": ["商品照片：正面、细节等多角度图", "风格参考单独标记，另填商品信息与详情页数"],
  "domestic-main-image": ["商品原图：一到八张，保持同一商品身份", "填写可核实卖点，生成一张搜索主图和五张轮播图"],
  "amazon-visual-suite": ["清晰商品素材，白底图优先", "细节、包装、场景、品牌或竞品图按用途归类，参考素材可选"],
};

export function WorkspaceInputReview() {
  const [id, setId] = useState("try-on");
  const feature = FEATURE_REGISTRY.find(item => item.id === id)!;
  const preset = "taskPreset" in feature ? feature.taskPreset : undefined;
  const guide = preset ? PRODUCT_IMAGE_INPUT_GUIDES[preset] : undefined;
  return <details className={styles.audit}>
    <summary>功能页输入指引与原站素材保留</summary>
    <p>按实际上传角色检查全部 16 项功能。原站示例与本次真实任务分别展示，尚未补齐的功能不计作已实跑。</p>
    <label className={styles.selector}>选择要核对的功能<select value={id} onChange={event => setId(event.target.value)}>{FEATURE_REGISTRY.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
    <div className={styles.selected} key={id}><h3>{feature.title}：该上传什么</h3>{guide && preset ? <><div className={styles.roles}><p><strong>{guide.product.title} · 必填</strong>{guide.product.description}</p><p><strong>{guide.reference.title} · {guide.referenceRequired ? "必填" : "可选"}</strong>{guide.reference.description}</p></div><ProductInputExamples preset={preset} open /></> : <ol>{otherRoles[id].map(role => <li key={role}>{role}</li>)}</ol>}{id === "one-click-replica" && <WorkspaceVisualPreview method="style" />}<Link href={feature.route}>打开对应功能页 →</Link></div>
    <h3>可以保留的原站示例</h3><p>以下保留的是原站图片配对与封面。套图封面不代表整套已重新验收，也不计作本次新任务产物。</p>
    <div className={styles.legacyGrid}>{RETAINED_WORKSPACE_EXAMPLES.map(example => <section key={example.id}><h4>{example.title}</h4><p>{example.change}</p><div className={styles.legacyImages}>{[{ src: example.input, label: "原图" }, ...(example.reference ? [{ src: example.reference, label: "参考" }] : []), { src: example.result, label: "原站示例" }].map(image => <figure key={image.src}><a href={image.src} target="_blank" rel="noopener noreferrer"><img src={image.src} alt={`${example.title}${image.label}`} width={240} height={200} loading="lazy" /></a><figcaption>{image.label}</figcaption></figure>)}</div></section>)}</div>
  </details>;
}
