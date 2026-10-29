export type DetailSkill = "quick" | "refined";

export const DETAIL_SKILLS = [
  {
    value: "refined" as const,
    title: "精致",
    description: "深入梳理买家需求与卖点，丰富逐页构图和视觉节奏。",
  },
];

export function detailSkillSettings(region: "domestic" | "international", skill: DetailSkill) {
  if (region === "international") return {};
  return {
    detail_skill: skill,
    ...(skill === "quick" ? { website_contract_version: "WEBSITE_DIRECT_V2" as const } : {}),
  };
}

export function savedDetailSkill(configuration?: { domestic_detail_pipeline?: unknown; settings?: unknown }): DetailSkill {
  // The pipeline is assigned by the server at creation and survives retries.
  return ["xinghe-domestic-detail-v4.0", "xinghe-domestic-detail-v4.0-fast"].includes(String(configuration?.domestic_detail_pipeline)) ? "refined" : "quick";
}
