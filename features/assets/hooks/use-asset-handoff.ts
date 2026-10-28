"use client";

import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";
import { assetHandoffKey, readAssetHandoff } from "@/lib/assets/creation-handoff";
import { getAssetAccessUrl } from "@/features/assets/api/assets";

export function useAssetHandoff(workspaceId: string | undefined, maxAssets: number, savedJobId?: string) {
  const search = useSearchParams();
  const key = assetHandoffKey(search);
  const requested = !savedJobId && Boolean(search.getAll("assetId").length);
  const query = useQuery({
    queryKey: ["asset-creation-handoff", workspaceId, maxAssets, key],
    enabled: Boolean(workspaceId && requested),
    retry: false,
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const handoff = readAssetHandoff(search, workspaceId!, maxAssets);
      if (!handoff) throw new Error("素材链接无效，请重新选择。");
      const sources = await Promise.all(handoff.ids.map(async assetId => {
        const asset = unwrapApiResponse(await apiClient.GET("/api/v1/workspaces/{workspace_id}/assets/{asset_id}", {
          params: { path: { workspace_id: workspaceId!, asset_id: assetId } }, signal,
        }));
        if (asset.media_kind !== "image" || asset.lifecycle_status !== "ready") throw new Error("部分素材已经不可用，请返回素材中心重新选择。");
        const access = await getAssetAccessUrl(workspaceId!, assetId, "inline", signal);
        return { asset, previewUrl: access.original_url ?? access.url, role: "product_reference" as const };
      }));
      const inspiration = handoff.caseId ? unwrapApiResponse(await apiClient.GET("/api/v1/inspiration/cases/{case_id}", {
        params: { path: { case_id: handoff.caseId } }, signal,
      })) : undefined;
      if (inspiration && (maxAssets !== 1 || inspiration.workflow_type !== "local_edit")) throw new Error("该灵感案例需要使用对应的创作工作台。");
      return { sources, instruction: inspiration?.prompt, caseId: inspiration?.id };
    },
  });
  return { ...query, key, loading: requested && (query.isPending || !workspaceId) };
}
