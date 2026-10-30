import { DisplayImage } from "./display-image";
import { ArrowRight, MousePointer2 } from "lucide-react";
import { getFeatureCaseArt } from "@/lib/feature-case-art";
import styles from "./feature-case-art.module.css";

export function FeatureCaseArt({ featureId, portrait = false }: { featureId: string; portrait?: boolean }) {
  const data = getFeatureCaseArt(featureId, portrait);
  if (!data) return null;
  if (!data.outputs.length) return <span className={`${styles.art} ${portrait ? styles.portrait : styles.landscape} ${styles.inputOnly}`} data-case-art={featureId}>
    <span className={styles.label}>上传示例 · {data.inputLabel}</span>
    <span className={styles.inputImages}>{data.inputs.map(input => <DisplayImage key={input.src} src={input.src} alt={input.label} width={240} height={280} loading="lazy" />)}</span>
    <span className={styles.goal}><ArrowRight size={12} />{data.outputLabel}</span>
  </span>;
  const reference = /参考|模特/.test(data.inputLabel) ? data.inputs[1] : undefined;
  return <span className={`${styles.art} ${styles.replay} ${reference ? styles.withReference : ""} ${portrait ? styles.portrait : styles.landscape}`} data-case-art={featureId} data-case-format={portrait ? "portrait" : "square"}>
    <DisplayImage className={styles.poster} src={data.outputs[0]} alt={`${data.outputLabel}，移入查看前后变化`} width={600} height={600} loading="lazy" />
    <span className={styles.sequence} aria-hidden="true">
      <span className={styles.sourceFrame}>
        <DisplayImage src={data.inputs[0].src} alt="" width={600} height={600} loading="lazy" />
        <span className={styles.frameLabel}>原图</span>
      </span>
      {reference && <span className={styles.referenceFrame}>
        <DisplayImage src={reference.src} alt="" width={600} height={600} loading="lazy" />
        <span className={styles.frameLabel}>{data.inputLabel.includes("模特") ? "模特原图" : data.inputLabel.includes("参考") ? "参考图" : "商品原图"}</span>
      </span>}
      <span className={styles.revealFrame}>
        <DisplayImage src={data.outputs[0]} alt="" width={600} height={600} loading="lazy" />
        <span className={styles.frameLabel}>{data.outputLabel}</span>
      </span>
      <span className={styles.scanLine} />
    </span>
    <span className={styles.resultLabel}>{data.outputLabel}</span>
    <span className={styles.hoverHint}><MousePointer2 size={11} /> 移入看变化</span>
  </span>;
}
