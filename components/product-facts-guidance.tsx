import { Info } from "lucide-react";

export const PRODUCT_FACTS_HINT = "建议尽可能完整、详细地填写真实商品卖点与资料，例如材质、规格、功能、使用场景、适用人群和差异化特点。资料越完整，越有助于 AI 理解商品，规划贴合商品的画面与文案。可直接粘贴已有商品介绍，请只填写可确认的信息。";

export function ProductFactsGuidance({ editing = false }: { editing?: boolean }) {
  return <p className="product-facts-guidance"><Info size={16} aria-hidden="true" /><span>{editing ? "请详细描述希望如何修改，以及必须保留的结构、材质、纹理、Logo 和其他特征；也可补充与本次修改相关的真实商品资料。" : PRODUCT_FACTS_HINT}</span></p>;
}
