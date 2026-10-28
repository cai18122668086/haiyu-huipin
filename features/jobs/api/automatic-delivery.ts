"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { apiClient } from "@/lib/api/client";
import { IdempotencyIntentRegistry, intentSignature } from "@/lib/api/idempotency";
import { unwrapApiResponse } from "@/lib/api/request";
import type { components } from "@/lib/api/generated/openapi";
import type { JobDetail } from "@/lib/api/types";
import { jobKeys } from "./jobs-keys";

export type AutomaticDeliveryAuthorization = components["schemas"]["DomesticDetailAutomaticDeliveryAuthorization"];
export function automaticAuthorityForJob(job?: JobDetail): AutomaticDeliveryAuthorization | undefined {
  const native = job?.plan?.payload.aurora_v4_3 as Record<string, unknown> | undefined;
  const receipt = native?.approval_receipt as Record<string, unknown> | undefined;
  const value = receipt?.automatic_delivery_authorization as AutomaticDeliveryAuthorization | undefined;
  return job?.configuration.domestic_detail_pipeline === "aurora-domestic-detail-v4.3.1" && value?.delivery_mode === "AUTO_FINALIZE_AUTHORIZED" && value.job_id === job.id && value.workspace_id === job.workspace_id && /^[0-9a-f]{64}$/.test(value.sha256) ? value : undefined;
}

export function useAutomaticDeliveryAuthorization(workspaceId: string | undefined, job: JobDetail | undefined, requested: boolean) {
  const native = job?.plan?.payload.native_approval as Record<string, unknown> | undefined;
  return useQuery({
    queryKey: [...jobKeys.detail(workspaceId ?? "pending", job?.id ?? "pending"), "automatic-delivery-authorization", job?.plan?.revision, native?.document_sha256],
    enabled: Boolean(workspaceId && requested && job?.status === "awaiting_confirmation" && job.configuration.domestic_detail_pipeline === "aurora-domestic-detail-v4.3.1"), retry: false,
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/plan/automatic-delivery-authorization",
      { params: { path: { workspace_id: workspaceId!, job_id: job!.id } }, signal },
    )),
  });
}

export function useRecoverAutomaticDelivery(workspaceId: string | undefined) {
  const client = useQueryClient(); const registry = useRef<IdempotencyIntentRegistry | null>(null);
  registry.current ??= new IdempotencyIntentRegistry();
  return useMutation({
    mutationFn: async (input: { jobId: string; authorizationSha256: string }) => {
      if (!workspaceId || !/^[0-9a-f]{64}$/.test(input.authorizationSha256)) throw new Error("原自动交付授权不可用。");
      const signature = intentSignature("recover-automatic-delivery", { workspaceId, ...input });
      return unwrapApiResponse(await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/automatic-delivery/recover",
        { params: { path: { workspace_id: workspaceId, job_id: input.jobId }, header: { "Idempotency-Key": registry.current!.keyFor(signature) } }, body: { authorization_sha256: input.authorizationSha256 } },
      ));
    },
    onSuccess: async (_, input) => {
      registry.current!.complete(intentSignature("recover-automatic-delivery", { workspaceId, ...input }));
      await client.invalidateQueries({ queryKey: jobKeys.all(workspaceId!) });
    },
  });
}
