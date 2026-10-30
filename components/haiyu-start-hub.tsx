import { DisplayImage } from "./display-image";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, Grid2X2, ImagePlus, PackageCheck } from "lucide-react";
import { PRIMARY_CREATION_ENTRIES } from "@/lib/creation-entrypoints";
import { FEATURE_REGISTRY } from "@/lib/feature-registry";
import { FeatureCaseArt } from "./feature-case-art";
import { getFeatureCaseArt } from "@/lib/feature-case-art";

export function HaiyuStartHub() {
  return (
    <section className="haiyu-start-hub" id="start" aria-labelledby="haiyu-start-title">
      <header><div><span className="haiyu-eyebrow">START WITH WHAT YOU NEED</span><h2 id="haiyu-start-title">先选一件你想做的事。</h2><p>有参考就复刻，要上新就做套图。还没想法，先逛灵感。</p></div><Link href="#tools" className="haiyu-hub-all"><Grid2X2 size={16} /> 查看全部 {FEATURE_REGISTRY.length} 项功能 <ArrowUpRight size={15} /></Link></header>
      <div className="haiyu-start-hub__grid">
        {PRIMARY_CREATION_ENTRIES.map((entry, index) => <Link href={entry.href} className={`haiyu-start-card haiyu-start-card--${entry.id}`} key={entry.id} data-creation-entry={entry.id}>
          <div className="haiyu-start-card__visual">{getFeatureCaseArt(entry.id) ? <div className="case-art-cover"><FeatureCaseArt featureId={entry.id} /></div> : <DisplayImage src={entry.image} alt={`${entry.label}商品视觉`} width={1374} height={1145} loading="lazy" />}<span>{entry.tag}</span><b>0{index + 1}</b></div>
          <div className="haiyu-start-card__body"><h3>{entry.label}<ArrowUpRight size={19} /></h3><strong>{entry.question}</strong><p>{entry.description}</p><dl><div><dt><ImagePlus size={13} /> 准备</dt><dd>{entry.input}</dd></div><div><dt><PackageCheck size={13} /> 得到</dt><dd>{entry.output}</dd></div></dl><span className="haiyu-start-card__action">{entry.action}<ArrowRight size={16} /></span></div>
        </Link>)}
      </div>
      <div className="haiyu-start-hub__steps"><span><i>1</i> 上传商品 / 选择参考</span><b>→</b><span><i>2</i> 填写商品信息</span><b>→</b><span><i>3</i> 在任务中心查看与下载</span></div>
    </section>
  );
}

export function HaiyuQuickStartLinks() {
  return <section className="haiyu-quick-start" aria-label="按创作目标快速开始"><h2>按你的目标，直接开始</h2><nav>{PRIMARY_CREATION_ENTRIES.map((entry, index) => <Link href={entry.href} key={entry.id}><span>0{index + 1}</span><div><strong>{entry.label}</strong><small>{entry.question}</small></div><ArrowUpRight size={17} /></Link>)}</nav></section>;
}
