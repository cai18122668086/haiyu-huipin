/** Sparse, parallel results belong to their original screen, not list position. */
export function fastReplicaOutputAsset<T extends {
  direction: string;
  is_deliverable: boolean;
  job_item_id: string | null;
}>(items: readonly { id: string; ordinal: number }[], assets: readonly T[], ordinal: number): T | undefined {
  const item = items.find(value => value.ordinal === ordinal);
  return item ? assets.find(value => value.direction === "output" && value.is_deliverable && value.job_item_id === item.id) : undefined;
}
