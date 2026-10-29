export type AmazonSuiteSpeed = "standard" | "fast";

export function readAmazonSuiteSpeed(value: unknown): AmazonSuiteSpeed {
  return value === "fast" ? "fast" : "standard";
}

// Omitting the standard field preserves the identity of historical requests.
export function amazonSuiteSpeedConfiguration(speed: AmazonSuiteSpeed): { amazon_speed?: "fast" } {
  return speed === "fast" ? { amazon_speed: "fast" } : {};
}

export function amazonSuiteSpeedDraftKey(workspaceId: string): string {
  return `polaris-amazon-suite-speed:${workspaceId}`;
}
