import { PLATFORM_SHOWCASE_CASES as PLATFORM_CASE_REVIEW } from "./platform-showcase-cases";
import { RETAINED_WORKSPACE_EXAMPLES } from "./workspace-input-examples";
import { SEO_TOOL_EXAMPLES, TOOL_ILLUSTRATION_CAPTION } from "./seo-tool-examples";

export type FeatureCaseArtData = {
  inputs: { src: string; label: string }[];
  outputs: string[];
  inputLabel: string;
  outputLabel: string;
  caption?: string;
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
  const illustration = SEO_TOOL_EXAMPLES[featureId];
  if (illustration) return {
    inputs: [{ src: illustration.input, label: illustration.inputLabel }],
    outputs: [illustration.output], inputLabel: illustration.inputLabel,
    outputLabel: "效果示意", caption: TOOL_ILLUSTRATION_CAPTION,
  };
  const legacy = RETAINED_WORKSPACE_EXAMPLES.find(item => item.id === featureId);
  if (!legacy) return undefined;
  return {
    inputs: [{ src: legacy.input, label: "商品原图" }, ...(legacy.reference ? [{ src: legacy.reference, label: "构图参考" }] : [])],
    outputs: [legacy.result],
    inputLabel: legacy.reference ? "商品 + 参考" : "原图",
    outputLabel: "效果示例",
  };
}
