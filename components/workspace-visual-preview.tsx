import { ArrowRight, Sparkles } from "lucide-react";
import { WORKSPACE_REPLICA_EXAMPLES } from "@/lib/workspace-replica-examples";

export function WorkspaceVisualPreview({ method = "exact" }: { method?: "exact" | "style" }) {
  const style = method === "style";
  const { product, reference, result } = WORKSPACE_REPLICA_EXAMPLES[method];
  return <div className="workspace-visual-preview" aria-label="产品原图加构图参考得到商品视觉的示例">
    <figure><img {...product} alt="历史任务的商品实拍原图" /><figcaption>你的产品图</figcaption></figure><span>＋</span>
    <figure><img {...reference} alt="同一历史任务实际使用的构图参考" /><figcaption>{style ? "喜欢的风格参考" : "想模仿的构图"}</figcaption></figure><ArrowRight size={20} />
    <figure><img {...result} alt="同一历史任务实际生成的商品图" /><figcaption><Sparkles size={11} /> {style ? "风格复刻结果" : "商品复刻结果"}</figcaption></figure>
  </div>;
}
