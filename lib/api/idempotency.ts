export type IdempotentOperation =
  | "upload"
  | "create-job"
  | "local-revision"
  | "cancel-job"
  | "retry-job"
  | "clone-job"
  | "resolve-intake"
  | "continue-detail-brand"
  | "confirm-plan"
  | "finalize-job"
  | "campaign-review"
  | "recover-campaign-review"
  | "recover-automatic-delivery";

export class IdempotencyIntentRegistry {
  private readonly keys = new Map<string, string>();
  private readonly keyFactory: () => string;

  constructor(keyFactory: () => string = () => crypto.randomUUID()) {
    this.keyFactory = keyFactory;
  }

  keyFor(intent: string): string {
    const existing = this.keys.get(intent);
    if (existing) return existing;
    const key = this.keyFactory();
    this.keys.set(intent, key);
    return key;
  }

  complete(intent: string): void {
    this.keys.delete(intent);
  }

  abandon(intent: string): void {
    this.keys.delete(intent);
  }
}

export function intentSignature(
  operation: IdempotentOperation,
  input: unknown,
): string {
  return `${operation}:${hashText(canonicalJson(input))}`;
}

export async function uploadIntentSignature(
  file: File,
  mediaKind: "image" | "mask",
): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return intentSignature("upload", {
    contentDigest: hex,
    mediaKind,
    name: file.name,
    size: file.size,
    type: file.type,
  });
}

function canonicalJson(input: unknown): string {
  if (Array.isArray(input)) {
    return `[${input.map(canonicalJson).join(",")}]`;
  }
  if (input && typeof input === "object") {
    const record = input as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(input) ?? "undefined";
}

function hashText(value: string): string {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return `${(first >>> 0).toString(16).padStart(8, "0")}${(second >>> 0)
    .toString(16)
    .padStart(8, "0")}`;
}
