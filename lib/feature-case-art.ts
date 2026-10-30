import { PLATFORM_SHOWCASE_CASES as PLATFORM_CASE_REVIEW } from "./platform-showcase-cases";
import { PRODUCT_INPUT_EXAMPLES, RETAINED_WORKSPACE_EXAMPLES } from "./workspace-input-examples";

export type FeatureCaseArtData = {
  inputs: { src: string; label: string }[];
  outputs: string[];
  inputLabel: string;
  outputLabel: string;
};

const taskForFeature: Record<string, string> = {
  "one-click-replica": "replica",
  "detail-pack": "detail",
  "domestic-main-image": "domestic",
  "amazon-visual-suite": "amazon",
  "change-background": "background",
  "change-color": "color",
  "try-on": "try-on",
};

export function getFeatureCaseArt(featureId: string, portrait = false): FeatureCaseArtData | undefined {
  const task = PLATFORM_CASE_REVIEW.find(item => item.id === taskForFeature[featureId]);
  if (task) {
    const outputs = task.results?.map(item => item.src) ?? (task.result ? [task.result] : []);
    if (!outputs.length) return undefined;
    // Choose a native portrait delivery for portrait modules; keep every original file intact.
    const portraitResult = portrait ? task.results?.find(item => item.height / item.width > 1.2 && item.height / item.width < 1.6) : undefined;
    const poster = featureId === "one-click-replica" && portrait ? task.results?.[6] : portraitResult;
    const inputs = [...task.inputs];
    if (featureId === "one-click-replica" && portrait) {
      const matchingReference = task.additionalInputs?.find(item => item.src.endsWith("reference-06.webp"));
      if (matchingReference) inputs[1] = matchingReference;
    }
    return {
      inputs,
      outputs: poster ? [poster.src, ...outputs.filter(src => src !== poster.src)] : outputs,
      inputLabel: featureId === "one-click-replica" ? "商品 + 参考" : featureId === "try-on" ? "服装 + 模特" : "商品原图",
      outputLabel: outputs.length > 1 ? `${outputs.length} 张成图` : "生成结果",
    };
  }
  const legacy = RETAINED_WORKSPACE_EXAMPLES.find(item => item.id === featureId);
  if (!legacy) {
    const inputExamples: Record<string, { src: string; label: string; goal: string }> = {
      "change-scene": { src: PRODUCT_INPUT_EXAMPLES.change_scene.product, label: "原场景商品图", goal: "迁移到新的使用场景" },
      "change-model": { src: PRODUCT_INPUT_EXAMPLES.change_model.product, label: "原模特商品图", goal: "更换人物，保留服装" },
      "scene-main": { src: PRODUCT_INPUT_EXAMPLES.scene_main.product, label: "商品主体图", goal: "制作商业场景主图" },
      "white-background": { src: PRODUCT_INPUT_EXAMPLES.white_background.product, label: "有背景的商品原图", goal: "去除背景，保留主体" },
      "model-image": { src: PRODUCT_INPUT_EXAMPLES.model_image.product, label: "待展示的服装商品", goal: "生成模特展示画面" },
      "creative-image": { src: "/images/homepage-features/native-v2-webp/04-input-desktop.webp", label: "清晰商品照片", goal: "展开卖点与活动创意" },
      "model-pose-transfer": { src: "/images/model-pose-transfer/previews/female-001-960.webp", label: "目标姿势参考", goal: "保留人物服装，换姿态" },
    };
    const example = inputExamples[featureId];
    return example ? { inputs: [{ src: example.src, label: example.label }], outputs: [], inputLabel: example.label, outputLabel: example.goal } : undefined;
  }
  return {
    inputs: [{ src: legacy.input, label: "商品原图" }, ...(legacy.reference ? [{ src: legacy.reference, label: "构图参考" }] : [])],
    outputs: [legacy.result],
    inputLabel: legacy.reference ? "商品 + 参考" : "原图",
    outputLabel: "效果示例",
  };
}
