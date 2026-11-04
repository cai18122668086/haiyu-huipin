// Public functional illustrations, not results from a platform generation task.
// Inputs, prompts and asset provenance: docs/seo-tool-examples-20261104.json.
export const SEO_TOOL_EXAMPLES: Record<string, { input: string; inputLabel: string; output: string }> = {
  "change-scene": {
    input: "/images/homepage-features/native-v2-webp/02-input-desktop.webp",
    inputLabel: "原场景商品图",
    output: "/images/seo-tool-examples/change-scene.webp",
  },
  "scene-main": {
    input: "/images/cases/platform-v3/01-bag-white.png",
    inputLabel: "商品主体图",
    output: "/images/seo-tool-examples/scene-main.webp",
  },
  "creative-image": {
    input: "/images/homepage-features/native-v2-webp/04-input-desktop.webp",
    inputLabel: "商品原图",
    output: "/images/seo-tool-examples/creative-image.webp",
  },
  "change-model": {
    input: "/images/cases/platform-v2/04-model.png",
    inputLabel: "原模特商品图",
    output: "/images/seo-tool-examples/change-model.webp",
  },
  "model-image": {
    input: "/images/cases/platform-v2/03-cardigan.png",
    inputLabel: "服装商品原图",
    output: "/images/seo-tool-examples/model-image.webp",
  },
  "model-pose-transfer": {
    input: "/images/cases/platform-v2/04-model.png",
    inputLabel: "原人物与姿势",
    output: "/images/seo-tool-examples/model-pose-transfer.webp",
  },
  "white-background": {
    input: "/images/homepage-features/native-v2-webp/02-input-desktop.webp",
    inputLabel: "有背景的商品图",
    output: "/images/seo-tool-examples/white-background.webp",
  },
};

export const TOOL_ILLUSTRATION_CAPTION = "功能效果示意，实际结果以你的素材和制作要求为准";
