import { ArrowRight, Sparkles } from "lucide-react";

export function WorkspaceVisualPreview() {
  return <div className="workspace-visual-preview" aria-label="从参考风格与商品原图到创作方向的展示">
    <figure><img src="/images/homepage-features/native-v2-webp/01-masked-primary-desktop.webp" alt="参考画面的构图与氛围" width={1374} height={1145} /><figcaption>喜欢的构图</figcaption></figure><span>＋</span>
    <figure><img src="/images/homepage-features/native-v2-webp/03-input-desktop.webp" alt="自己的商品原图" width={1374} height={1145} /><figcaption>你的好商品</figcaption></figure><ArrowRight size={20} />
    <figure><img src="/images/homepage-features/native-v2-webp/03-remix-primary-desktop.webp" alt="融合参考氛围的商品视觉" width={1374} height={1145} /><figcaption><Sparkles size={11} /> 新的视觉方向</figcaption></figure>
  </div>;
}
