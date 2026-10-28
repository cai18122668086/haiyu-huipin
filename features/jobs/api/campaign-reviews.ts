"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { apiClient } from "@/lib/api/client";
import { IdempotencyIntentRegistry, intentSignature, type IdempotentOperation } from "@/lib/api/idempotency";
import { unwrapApiResponse } from "@/lib/api/request";
import type { components } from "@/lib/api/generated/openapi";
import type { JobDetail } from "@/lib/api/types";
import { jobKeys } from "./jobs-keys";

export type CampaignSelection = components["schemas"]["DomesticCampaignReviewSelection"];
export type CampaignQuote = components["schemas"]["DomesticCampaignReviewQuote"];
export type RecoveryQuote = components["schemas"]["DomesticCampaignRecoveryQuote"];
export type CampaignReport = components["schemas"]["DomesticCampaignReviewReport"];
export const CAMPAIGN_AUTHORIZATION = "我已审阅当前成图，授权另行进行一次可选整页视觉审核，并同意所列费用预留。";
export const campaignTerminal = (status?: string) => ["completed", "failed", "cancelled"].includes(status ?? "");

export function useCampaignHistory(workspaceId?: string, sourceId?: string) {
  return useInfiniteQuery({
    queryKey: [...jobKeys.detail(workspaceId ?? "pending", sourceId ?? "pending"), "campaign-reviews"],
    enabled: Boolean(workspaceId && sourceId),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-reviews",
      { params: { path: { workspace_id: workspaceId!, job_id: sourceId! }, query: { limit: 20, cursor: pageParam } }, signal },
    )),
    getNextPageParam: last => last.next_cursor ?? undefined,
    refetchInterval: query => query.state.data?.pages.some(page => page.items.some(item => ["queued", "processing", "cancelling"].includes(item.status))) ? 2000 : false,
  });
}

export function useCampaignQuote(workspaceId: string | undefined, sourceId: string, fingerprint?: string) {
  return useQuery({
    queryKey: [...jobKeys.detail(workspaceId ?? "pending", sourceId), "campaign-quote", fingerprint],
    enabled: Boolean(workspaceId && fingerprint), retry: false,
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/quote",
      { params: { path: { workspace_id: workspaceId!, job_id: sourceId }, query: { final_review_sha256: fingerprint! } }, signal },
    )),
  });
}

export function useCampaignReport(workspaceId: string | undefined, sourceId: string, job: JobDetail | undefined) {
  return useQuery({
    queryKey: [...jobKeys.detail(workspaceId ?? "pending", job?.id ?? "pending"), "campaign-report", sourceId, job?.status],
    enabled: Boolean(workspaceId && job && campaignTerminal(job.status)), retry: false,
    queryFn: async ({ signal }) => {
      const value = unwrapApiResponse(await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/report",
        { params: { path: { workspace_id: workspaceId!, job_id: job!.id } }, signal },
      ));
      if (value.source_job_id !== sourceId) throw new Error("该报告属于另一份详情任务。");
      return value;
    },
  });
}

export function useCampaignRecoveryQuote(workspaceId: string | undefined, reviewId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...jobKeys.detail(workspaceId ?? "pending", reviewId), "campaign-recovery-quote"],
    enabled: Boolean(workspaceId && reviewId && enabled), retry: false,
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/recovery-quote",
      { params: { path: { workspace_id: workspaceId!, job_id: reviewId } }, signal },
    )),
  });
}

function useCampaignMutation<Input>(workspaceId: string | undefined, operation: IdempotentOperation, execute: (workspaceId: string, input: Input, key: string) => Promise<JobDetail>) {
  const client = useQueryClient();
  const registry = useRef<IdempotencyIntentRegistry | null>(null);
  registry.current ??= new IdempotencyIntentRegistry();
  return useMutation({
    mutationFn: async (input: Input) => {
      if (!workspaceId) throw new Error("工作区尚未就绪。");
      const signature = intentSignature(operation, { workspaceId, input });
      // A transport error retains this key, including while the server outcome is unknown.
      return execute(workspaceId, input, registry.current!.keyFor(signature));
    },
    onSuccess: async (_, input) => {
      registry.current!.complete(intentSignature(operation, { workspaceId, input }));
      await client.invalidateQueries({ queryKey: jobKeys.all(workspaceId!) });
    },
  });
}

export function useCreateCampaignReview(workspaceId?: string) {
  return useCampaignMutation<{ sourceId: string; quote: CampaignQuote; approvalText: string }>(workspaceId, "campaign-review", async (id, input, key) => {
    if (input.quote.source_job_id !== input.sourceId || input.approvalText !== CAMPAIGN_AUTHORIZATION) throw new Error("请核对当前成图、报价并明确授权。");
    return unwrapApiResponse(await apiClient.POST(
      "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review",
      { params: { path: { workspace_id: id, job_id: input.sourceId }, header: { "Idempotency-Key": key } }, body: { final_review_sha256: input.quote.final_review_sha256, quote_sha256: input.quote.sha256, authorized_reserve_micros: input.quote.reserve_micros, approval_text: input.approvalText } },
    ));
  });
}

export function useRecoverCampaignReview(workspaceId?: string) {
  return useCampaignMutation<{ reviewId: string; quote: RecoveryQuote }>(workspaceId, "recover-campaign-review", async (id, input, key) => {
    if (input.quote.original_review_job_id !== input.reviewId || input.quote.model_requests !== 0 || input.quote.image_requests !== 0 || input.quote.reserve_micros !== 0) throw new Error("原结果恢复报价无效。");
    return unwrapApiResponse(await apiClient.POST(
      "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/recover",
      { params: { path: { workspace_id: id, job_id: input.reviewId }, header: { "Idempotency-Key": key } }, body: { original_result_journal_sha256: input.quote.original_result_journal_sha256, quote_sha256: input.quote.sha256 } },
    ));
  });
}

export async function getCampaignPreview(workspaceId: string, reviewId: string): Promise<Blob> {
  const result = await apiClient.GET("/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/preview.png", { params: { path: { workspace_id: workspaceId, job_id: reviewId } }, parseAs: "blob" });
  const value = unwrapApiResponse(result);
  if (!(value instanceof Blob)) throw new Error("审核预览格式无法识别。");
  return value;
}
