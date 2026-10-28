"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { apiClient } from "@/lib/api/client";
import { collectAssetPages } from "@/lib/api/asset-pages";
import {
  IdempotencyIntentRegistry,
  uploadIntentSignature,
} from "@/lib/api/idempotency";
import { unwrapApiResponse } from "@/lib/api/request";
import type { AssetOriginKind } from "@/lib/api/types";
import { assetKeys } from "./assets-keys";

export function useAssets(
  workspaceId: string | undefined,
  filters: { originKind?: AssetOriginKind; limit?: number } = {},
) {
  return useQuery({
    queryKey: assetKeys.list(workspaceId ?? "pending", filters),
    enabled: Boolean(workspaceId),
    queryFn: ({ signal }) => collectAssetPages(async (cursor) => {
      const result = await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/assets",
        {
          params: {
            path: { workspace_id: workspaceId! },
            query: {
              origin_kind: filters.originKind,
              media_kind: "image",
              limit: filters.limit ?? 100,
              include_total: true,
              cursor,
            },
          },
          signal,
        },
      );
      return unwrapApiResponse(result);
    }),
  });
}

export function useAsset(
  workspaceId: string | undefined,
  assetId: string | undefined,
) {
  return useQuery({
    queryKey: ["asset", workspaceId, assetId],
    enabled: Boolean(workspaceId && assetId),
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/assets/{asset_id}",
        {
          params: { path: { workspace_id: workspaceId!, asset_id: assetId! } },
          signal,
        },
      );
      return unwrapApiResponse(result);
    },
    staleTime: 60_000,
  });
}

export function useUploadAsset(workspaceId: string | undefined) {
  const queryClient = useQueryClient();
  const registry = useRef<IdempotencyIntentRegistry | null>(null);
  registry.current ??= new IdempotencyIntentRegistry();
  const signatureByInput = useRef(
    new WeakMap<File, Map<"image" | "mask", Promise<string>>>(),
  );
  const signatureFor = (input: { file: File; mediaKind?: "image" | "mask" }) => {
    const mediaKind = input.mediaKind ?? "image";
    const signatures =
      signatureByInput.current.get(input.file) ??
      new Map<"image" | "mask", Promise<string>>();
    signatureByInput.current.set(input.file, signatures);
    const existing = signatures.get(mediaKind);
    if (existing) return existing;
    const signature = uploadIntentSignature(input.file, mediaKind);
    signatures.set(mediaKind, signature);
    return signature;
  };
  const mutation = useMutation({
    mutationFn: async ({
      file,
      mediaKind = "image",
      originKind = "upload",
    }: {
      file: File;
      mediaKind?: "image" | "mask";
      originKind?: AssetOriginKind;
    }) => {
      if (!workspaceId) throw new Error("工作区尚未就绪。");
      const signature = await signatureFor({ file, mediaKind });
      const idempotencyKey = registry.current!.keyFor(signature);
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/assets",
        {
          params: {
            path: { workspace_id: workspaceId },
            header: { "Idempotency-Key": idempotencyKey },
          },
          body: {
            file: file as unknown as string,
            media_kind: mediaKind,
            origin_kind: originKind,
          },
          bodySerializer() {
            const form = new FormData();
            form.set("file", file);
            form.set("media_kind", mediaKind);
            form.set("origin_kind", originKind);
            return form;
          },
        },
      );
      return unwrapApiResponse(result);
    },
    onSuccess: (_, input) => {
      void (async () => {
        registry.current!.complete(await signatureFor(input));
        // Do not make callers wait for the personal-library refresh. Inspiration
        // case materialization needs the uploaded asset immediately so it can
        // resolve its access URL and finish the click flow.
        void queryClient.invalidateQueries({
          queryKey: assetKeys.lists(workspaceId!),
        });
      })();
    },
  });
  return {
    ...mutation,
    abandonIntent: async (input: {
      file: File;
      mediaKind?: "image" | "mask";
    }) => registry.current!.abandon(await signatureFor(input)),
  };
}

export async function getAssetAccessUrl(
  workspaceId: string,
  assetId: string,
  disposition: "inline" | "attachment" = "inline",
  signal?: AbortSignal,
  variant: "original" | "thumbnail" = "original",
) {
  const result = await apiClient.POST(
    "/api/v1/workspaces/{workspace_id}/assets/{asset_id}/access-url",
    {
      params: { path: { workspace_id: workspaceId, asset_id: assetId } },
      body: { disposition, variant },
      signal,
    },
  );
  return unwrapApiResponse(result);
}

export function useAssetAccessUrl(
  workspaceId: string | undefined,
  assetId: string | undefined,
  variant: "original" | "thumbnail" = "original",
) {
  return useQuery({
    queryKey: ["asset-access-url", workspaceId, assetId, "inline", variant],
    enabled: Boolean(workspaceId && assetId),
    queryFn: ({ signal }) => getAssetAccessUrl(workspaceId!, assetId!, "inline", signal, variant),
    staleTime: 4 * 60_000,
  });
}
