export type AssetListFilters = {
  originKind?: string;
  mediaKind?: string;
  cursor?: string;
  limit?: number;
};

function normalize(filters: AssetListFilters) {
  return {
    originKind: filters.originKind ?? null,
    mediaKind: filters.mediaKind ?? null,
    cursor: filters.cursor ?? null,
    limit: filters.limit ?? 24,
  };
}

export const assetKeys = {
  all: (workspaceId: string) => ["assets", workspaceId] as const,
  lists: (workspaceId: string) =>
    [...assetKeys.all(workspaceId), "list"] as const,
  list: (workspaceId: string, filters: AssetListFilters) =>
    [...assetKeys.lists(workspaceId), normalize(filters)] as const,
  detail: (workspaceId: string, assetId: string) =>
    [...assetKeys.all(workspaceId), "detail", assetId] as const,
  summary: (workspaceId: string) =>
    [...assetKeys.all(workspaceId), "summary"] as const,
};
