import type { ProductImagePreset } from "@/lib/feature-registry";
import { PRODUCT_IMAGE_INPUT_GUIDES } from "@/lib/product-image-input-guide";
import { PRODUCT_INPUT_EXAMPLES } from "@/lib/workspace-input-examples";
import styles from "./workspace-input-review.module.css";

export function ProductInputExamples({ preset, open = false }: { preset: ProductImagePreset; open?: boolean }) {
  const guide = PRODUCT_IMAGE_INPUT_GUIDES[preset];
  const examples = PRODUCT_INPUT_EXAMPLES[preset];
  const roles = [{ image: examples.product, role: guide.product, required: true }, ...(examples.reference ? [{ image: examples.reference, role: guide.reference, required: guide.referenceRequired }] : [])];
  return <details className={styles.inputExamples} open={open}>
    <summary>查看输入图片示例</summary>
    <p>示例只帮助你判断图片放在哪个位置，请上传自己的素材。</p>
    <div className={styles.roleExamples}>{roles.map(({ image, role, required }) => <figure key={role.title}><img src={image} alt={`${role.title}输入示例`} width={180} height={150} loading="lazy" /><figcaption><strong>{role.title} · {required ? "必填" : "可选"}</strong><span>{role.description}</span></figcaption></figure>)}</div>
  </details>;
}
