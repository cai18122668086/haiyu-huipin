import type { FeatureDefinition } from "./feature-registry";

export type FeatureSearchMatch = {
  feature: FeatureDefinition;
  score: number;
  matchReason: string;
  matchedText: string;
  matchedField: "title" | "alias" | "useCase" | "intent" | "keyword" | "description";
};

type SearchField = {
  kind: FeatureSearchMatch["matchedField"];
  label: string;
  value: string;
  weight: number;
};

const FIELD_LABELS: Record<FeatureSearchMatch["matchedField"], string> = {
  title: "功能名称",
  alias: "常用叫法",
  useCase: "使用场景",
  intent: "用户需求",
  keyword: "相关关键词",
  description: "功能说明",
};

function normalizedVariants(value: string): string[] {
  const normalized = value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .trim()
    .replace(/[，。！？、；：,.!?;:()（）【】\[\]{}“”"'`~·/\\_-]+/gu, " ")
    .replace(/\s+/gu, " ");
  const compact = normalized.replace(/\s+/gu, "");
  return compact && compact !== normalized ? [normalized, compact] : [normalized];
}

export function normalizeFeatureSearchQuery(query: string): string {
  return normalizedVariants(query)[0] ?? "";
}

function featureSearchFields(feature: FeatureDefinition): SearchField[] {
  return [
    { kind: "title", label: FIELD_LABELS.title, value: feature.title, weight: 150 },
    { kind: "title", label: FIELD_LABELS.title, value: feature.shortTitle, weight: 140 },
    ...(feature.aliases ?? []).map((value) => ({
      kind: "alias" as const,
      label: FIELD_LABELS.alias,
      value,
      weight: 130,
    })),
    ...(feature.useCases ?? []).map((value) => ({
      kind: "useCase" as const,
      label: FIELD_LABELS.useCase,
      value,
      weight: 120,
    })),
    ...(feature.userIntents ?? []).map((value) => ({
      kind: "intent" as const,
      label: FIELD_LABELS.intent,
      value,
      weight: 110,
    })),
    ...feature.searchKeywords.map((value) => ({
      kind: "keyword" as const,
      label: FIELD_LABELS.keyword,
      value,
      weight: 90,
    })),
    { kind: "description", label: FIELD_LABELS.description, value: feature.description, weight: 55 },
    { kind: "description", label: FIELD_LABELS.description, value: feature.guidance, weight: 45 },
  ];
}

function scoreField(queryVariants: string[], field: SearchField): number {
  const fieldVariants = normalizedVariants(field.value);
  let best = 0;

  for (const query of queryVariants) {
    if (query.length < 2) continue;
    for (const candidate of fieldVariants) {
      if (!candidate) continue;
      if (candidate === query) {
        best = Math.max(best, field.weight + 90 + Math.min(query.length, 24));
      } else if (candidate.includes(query)) {
        best = Math.max(best, field.weight + 55 + Math.min(query.length, 20));
      } else if (query.includes(candidate) && candidate.length >= 2) {
        best = Math.max(best, field.weight + 35 + Math.min(candidate.length, 20));
      }
    }
  }

  return best;
}

function formatMatchReason(field: SearchField): string {
  return `${field.label}：${field.value}`;
}

export function searchFeatures(
  query: string,
  features: readonly FeatureDefinition[],
): FeatureSearchMatch[] {
  const queryVariants = normalizedVariants(query).filter((value) => value.length >= 2);
  if (!queryVariants.length) return [];

  const matches = features
    .map((feature) => {
      let bestField: SearchField | undefined;
      let bestScore = 0;
      let supportingMatches = 0;

      for (const field of featureSearchFields(feature)) {
        const fieldScore = scoreField(queryVariants, field);
        if (!fieldScore) continue;
        supportingMatches += 1;
        if (fieldScore > bestScore) {
          bestScore = fieldScore;
          bestField = field;
        }
      }

      if (!bestField) return null;
      return {
        feature,
        score: bestScore + Math.min(supportingMatches - 1, 4) * 3,
        matchReason: formatMatchReason(bestField),
        matchedText: bestField.value,
        matchedField: bestField.kind,
      } satisfies FeatureSearchMatch;
    })
    .filter((match): match is FeatureSearchMatch => match !== null)
    .sort((left, right) => right.score - left.score || left.feature.title.localeCompare(right.feature.title, "zh-CN"));

  const topScore = matches[0]?.score ?? 0;
  const relevanceFloor = Math.max(120, topScore - 60);
  return matches.filter((match) => match.score >= relevanceFloor);
}
