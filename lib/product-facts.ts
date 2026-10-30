/** Fit the existing structured contract without discarding any user facts. */
export function splitProductFacts(value: string, limit = 12): string[] {
  const facts = value.split(/[\n,，;；]+/).map(item => item.trim()).filter(Boolean);
  if (facts.length <= limit) return facts;
  return [...facts.slice(0, limit - 1), facts.slice(limit - 1).join("\n")];
}
