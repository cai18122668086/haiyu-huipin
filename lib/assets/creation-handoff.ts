type Search = Pick<URLSearchParams, "get" | "getAll">;
const identifier = /^[a-zA-Z0-9_-]{1,40}$/;

export function assetHandoffKey(search: Search): string {
  return JSON.stringify([search.get("workspaceId"), search.getAll("assetId"), search.get("inspirationCaseId")]);
}

export function readAssetHandoff(search: Search, workspaceId: string, maxAssets: number) {
  // A saved task always owns its inputs, even when a stale handoff is in the URL.
  if (search.get("jobId") || !search.getAll("assetId").length) return undefined;
  const ids = [...new Set(search.getAll("assetId"))];
  if (search.get("workspaceId") !== workspaceId) throw new Error("所选素材不属于当前工作区，请返回素材中心重新选择。");
  if (ids.length > maxAssets || ids.some(id => !identifier.test(id))) throw new Error(`本工作台支持带入 1–${maxAssets} 张有效图片，请重新选择。`);
  const caseId = search.get("inspirationCaseId") ?? undefined;
  if (caseId && !identifier.test(caseId)) throw new Error("灵感案例链接无效，请重新选择案例。");
  return { ids, caseId };
}

export function assetCreationHref(route: "/workspace/local-edit" | "/workspace/detail-pack" | "/workspace/domestic-main-image" | "/style-replica", workspaceId: string, ids: string[], caseId?: string) {
  const params = new URLSearchParams({ workspaceId });
  for (const id of ids) params.append("assetId", id);
  if (caseId) params.set("inspirationCaseId", caseId);
  readAssetHandoff(params, workspaceId, route === "/workspace/local-edit" ? 1 : route === "/style-replica" ? 6 : 8);
  if (!ids.length) throw new Error("请先选择素材。");
  return `${route}?${params}`;
}
