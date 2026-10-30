import type { ProductImagePreset } from "./feature-registry";

type InputRoleGuide = { title: string; description: string };
export type ProductImageInputGuide = {
  product: InputRoleGuide;
  reference: InputRoleGuide;
  referenceRequired: boolean;
  change: string;
  reviewIdentity: string;
};

const keepProduct = "商品轮廓、结构、颜色、材质和关键细节与原图一致。";

export const PRODUCT_IMAGE_INPUT_GUIDES: Record<ProductImagePreset, ProductImageInputGuide> = {
  change_background: {
    product: { title: "商品原图", description: "可上传杂乱背景、普通桌面或白底商品图。主体要完整清楚，不必先修好背景。" },
    reference: { title: "目标背景参考图", description: "可选。提供希望使用的背景、色调或布景；商品仍以左侧原图为准。" },
    referenceRequired: false,
    change: "保留同一件商品，把原背景换成你需要的背景。",
    reviewIdentity: keepProduct,
  },
  change_scene: {
    product: { title: "原场景商品图", description: "上传商品在当前环境中的照片，如室内桌面。主体清楚、无遮挡，便于迁移到新的使用场景。" },
    reference: { title: "新场景参考图", description: "可选。提供家居、户外等目标场景，辅助匹配环境光、反射与空间关系。" },
    referenceRequired: false,
    change: "让商品进入新的使用环境，并重新匹配光线与场景关系。",
    reviewIdentity: keepProduct,
  },
  change_color: {
    product: { title: "原配色商品图", description: "上传当前颜色的商品照片，白底或普通背景均可。保留清晰纹理与五金，便于核对换色效果。" },
    reference: { title: "目标颜色参考图", description: "可选。上传色卡或目标配色图片；下方仍需填写目标颜色。" },
    referenceRequired: false,
    change: "只修改指定的商品配色，保留结构、纹理和背景。",
    reviewIdentity: "商品已变为指定颜色；除配色外，轮廓、结构、纹理、五金、Logo 和背景没有意外变化。",
  },
  change_model: {
    product: { title: "原模特商品图", description: "上传已经有人物展示的商品照片。需要保留的衣服、配饰、姿态与背景应清楚可见。" },
    reference: { title: "新模特参考图", description: "可选。提供想替换成的成年模特；也可在补充要求中描述外形、年龄段与风格。" },
    referenceRequired: false,
    change: "更换展示商品的人物，保留原商品、服装与姿态。",
    reviewIdentity: "人物按要求更换；需要保留的服装、商品、配饰、姿态和原场景没有意外变化。",
  },
  try_on: {
    product: { title: "服装商品图", description: "上传平铺或挂拍的衣服，完整展示领口、袖子、纽扣和纹理。这张图决定试穿哪件衣服。" },
    reference: { title: "目标模特图", description: "上传要试穿这件衣服的人物照片，人物清楚、服装区域无遮挡。这张图决定由谁试穿。" },
    referenceRequired: true,
    change: "把服装商品穿到指定模特身上，核对版型、细节和人物身份。",
    reviewIdentity: "试穿的是上传的同款服装，颜色、领口、纹理和纽扣细节没有跑偏；目标模特身份保持可识别。",
  },
  scene_main: {
    product: { title: "商品主体图", description: "上传白底图或主体完整的普通商品照片，建议主体清晰、角度明确；不需要预先做成宣传海报。" },
    reference: { title: "主图氛围参考", description: "可选。提供希望采用的布景、光线和构图方向，输出仍围绕你上传的商品。" },
    referenceRequired: false,
    change: "从普通商品素材出发，制作突出主体的商业场景主视觉。",
    reviewIdentity: keepProduct,
  },
  white_background: {
    product: { title: "待处理商品原图", description: "上传有背景的商品照片，如桌面或生活随手拍。无需先抠图或做成白底，商品边缘要清楚完整。" },
    reference: { title: "阴影与光线参考", description: "可选，通常只需商品原图。参考图仅辅助说明接地阴影或打光方向。" },
    referenceRequired: false,
    change: "去除原有环境，得到主体清晰、背景纯白的商品图。",
    reviewIdentity: keepProduct,
  },
  model_image: {
    product: { title: "待展示商品图", description: "上传服装、帽子、包袋等商品本身的照片，可以是白底、平铺或挂拍图，不要求原图已经有模特。" },
    reference: { title: "模特风格参考", description: "可选。提供人物气质、景别或展示方式；不提供时可在补充要求中描述。" },
    referenceRequired: false,
    change: "为商品生成有人物穿戴、手持或展示的画面。",
    reviewIdentity: "展示的商品与原图同款，款式、颜色、结构和关键细节一致；人物与商品的穿戴、手持关系自然。",
  },
};

export function productImageInputIssue(preset: ProductImagePreset, productCount: number, referenceCount: number): string | undefined {
  const guide = PRODUCT_IMAGE_INPUT_GUIDES[preset];
  if (productCount === 0) return `请先上传${guide.product.title}。`;
  if (guide.referenceRequired && referenceCount === 0) return "请再上传目标模特图，指定由谁试穿这件衣服。";
  return undefined;
}
