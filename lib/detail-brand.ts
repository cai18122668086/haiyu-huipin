const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function needsAutomaticBrandHandling(value: unknown): boolean {
  const intake = object(value);
  if (intake.brand_confirmation_required === true) return true;
  const facts = new Map((Array.isArray(intake.observed_facts) ? intake.observed_facts : []).map(value => { const fact = object(value); return [String(fact.id), fact]; }));
  return (Array.isArray(intake.source_conflicts) ? intake.source_conflicts : []).some(value => {
    const conflict = object(value);
    if (!/品牌|标识|标志|\bbrand\b|\blogo\b/i.test(String(conflict.topic)) || !Array.isArray(conflict.alternative_fact_ids) || !conflict.alternative_fact_ids.length) return false;
    return conflict.alternative_fact_ids.every(id => {
      const fact = facts.get(String(id)) ?? {};
      const text = String(fact.text ?? "");
      return fact.source_class === "OBSERVED_PRODUCT_EVIDENCE" &&
        (/^(brand|branding|logo)\./.test(String(fact.atomic_key ?? "")) || /品牌|标识|标志|字样|\bbrand\b|\blogo\b/i.test(text)) &&
        !/净含量|容量|营养|成分|配方|尺寸|材质|重量|\b(weight|capacity|ingredient)\b/i.test(text);
    });
  });
}
