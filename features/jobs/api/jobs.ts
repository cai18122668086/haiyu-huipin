"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { apiClient } from "@/lib/api/client";
import {
  IdempotencyIntentRegistry,
  intentSignature,
  type IdempotentOperation,
} from "@/lib/api/idempotency";
import { unwrapApiResponse } from "@/lib/api/request";
import { mapApiError } from "@/lib/api/error-mapper";
import {
  activeJobStatuses,
  isActiveJob,
  type DomesticDetailFinalReview,
  type JobCreate,
  type JobDetail,
  type JobStatus,
  type JobType,
  type RetryScope,
} from "@/lib/api/types";
import { jobKeys } from "./jobs-keys";
import { collectCursorPages } from "@/lib/api/cursor-pages";
import { assertJobAction } from "@/lib/jobs/actions";

type JobFilters = {
  type?: JobType;
  statuses?: JobStatus[];
  limit?: number;
  allPages?: boolean;
};

export function useJobs(
  workspaceId: string | undefined,
  filters: JobFilters = {},
) {
  const query = useQuery({
    queryKey: jobKeys.list(workspaceId ?? "pending", filters),
    enabled: Boolean(workspaceId),
    queryFn: async ({ signal }) => {
      const fetchPage = async (cursor?: string) => unwrapApiResponse(await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/jobs",
        {
          params: {
            path: { workspace_id: workspaceId! },
            query: {
              type: filters.type,
              statuses: filters.statuses,
              limit: filters.limit ?? 100,
              include_total: true,
              cursor,
            },
          },
          signal,
        },
      ));
      return filters.allPages
        ? collectCursorPages(fetchPage, "任务记录分页异常，请重新连接后再试。")
        : fetchPage();
    },
    refetchInterval(current) {
      return current.state.data?.items.some((job) => isActiveJob(job.status))
        ? 2_000
        : false;
    },
  });
  return query;
}

export function useJob(
  workspaceId: string | undefined,
  jobId: string | undefined,
) {
  return useQuery({
    queryKey: jobKeys.detail(workspaceId ?? "pending", jobId ?? "pending"),
    enabled: Boolean(workspaceId && jobId),
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}",
        {
          params: {
            path: { workspace_id: workspaceId!, job_id: jobId! },
          },
          signal,
        },
      );
      return unwrapApiResponse(result);
    },
    refetchInterval(current) {
      const status = current.state.data?.status;
      if (status && isActiveJob(status)) return 1_500;
      const data = current.state.data;
      const native = data?.plan?.payload.aurora_v4_3 as Record<string, unknown> | undefined;
      const receipt = native?.approval_receipt as Record<string, unknown> | undefined;
      const automatic = receipt?.automatic_delivery_authorization as Record<string, unknown> | undefined;
      return status === "awaiting_final_review" && data?.configuration.domestic_detail_pipeline === "aurora-domestic-detail-v4.3.1" && automatic?.delivery_mode === "AUTO_FINALIZE_AUTHORIZED" && data.stage !== "automatic_delivery_reconciliation_required" ? 1_500 : false;
    },
  });
}

export async function getJobDeliverablesArchive(
  workspaceId: string,
  jobId: string,
): Promise<Blob> {
  const result = await apiClient.GET(
    "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/deliverables.zip",
    {
      params: { path: { workspace_id: workspaceId, job_id: jobId } },
      parseAs: "blob",
    },
  );
  if (result.error !== undefined || !result.response.ok) {
    let error: unknown = result.error;
    if (error instanceof Blob) {
      try {
        error = JSON.parse(await error.text());
      } catch {
        error = undefined;
      }
    }
    throw mapApiError(error, result.response);
  }
  if (!(result.data instanceof Blob)) {
    throw new Error("整套下载返回了无法识别的文件格式。");
  }
  return result.data;
}

function useJobMutation<TResult, TVariables>(
  workspaceId: string | undefined,
  operation: IdempotentOperation,
  signatureInput: (variables: TVariables) => unknown,
  mutationFn: (
    workspaceId: string,
    variables: TVariables,
    idempotencyKey: string,
  ) => Promise<TResult>,
) {
  const queryClient = useQueryClient();
  const registry = useRef<IdempotencyIntentRegistry | null>(null);
  registry.current ??= new IdempotencyIntentRegistry();
  const signatureFor = (variables: TVariables) =>
    intentSignature(operation, signatureInput(variables));
  const mutation = useMutation({
    mutationFn: async (variables: TVariables) => {
      if (!workspaceId) throw new Error("工作区尚未就绪。");
      const signature = signatureFor(variables);
      return mutationFn(
        workspaceId,
        variables,
        registry.current!.keyFor(signature),
      );
    },
    onSuccess: async (_, variables) => {
      registry.current!.complete(signatureFor(variables));
      await queryClient.invalidateQueries({ queryKey: ["credits", workspaceId!] });
      await queryClient.invalidateQueries({ queryKey: ["credits-ledger", workspaceId!] });
      await queryClient.invalidateQueries({
        queryKey: jobKeys.all(workspaceId!),
      });
    },
  });
  return {
    ...mutation,
    abandonIntent: (variables: TVariables) =>
      registry.current!.abandon(signatureFor(variables)),
  };
}

export function useCreateJob(workspaceId: string | undefined) {
  return useJobMutation<JobDetail, JobCreate>(
    workspaceId,
    "create-job",
    (body) => body,
    async (id, body, key) => {
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs",
        {
          params: {
            path: { workspace_id: id },
            header: { "Idempotency-Key": key },
          },
          body,
        },
      );
      return unwrapApiResponse(result);
    },
  );
}

type AuthorizedJobAction = {
  jobId: string;
  allowedActions: string[];
};

export function useCancelJob(workspaceId: string | undefined) {
  return useJobMutation<JobDetail, AuthorizedJobAction>(
    workspaceId,
    "cancel-job",
    ({ jobId }) => ({ jobId }),
    async (id, input, key) => {
      assertJobAction(input.allowedActions, "cancel");
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/cancel",
        {
          params: {
            path: { workspace_id: id, job_id: input.jobId },
            header: { "Idempotency-Key": key },
          },
          body: { reason: "用户主动取消任务" },
        },
      );
      return unwrapApiResponse(result);
    },
  );
}

export function useCloneJob(workspaceId: string | undefined) {
  return useJobMutation<JobDetail, AuthorizedJobAction>(
    workspaceId,
    "clone-job",
    ({ jobId }) => ({ jobId }),
    async (id, input, key) => {
      assertJobAction(input.allowedActions, "clone");
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/clone",
        {
          params: {
            path: { workspace_id: id, job_id: input.jobId },
            header: { "Idempotency-Key": key },
          },
        },
      );
      return unwrapApiResponse(result);
    },
  );
}

type RetryJobAction = AuthorizedJobAction & {
  scope?: RetryScope;
  itemIds?: string[];
};

export function useRetryJob(workspaceId: string | undefined) {
  return useJobMutation<JobDetail, RetryJobAction>(
    workspaceId,
    "retry-job",
    ({ jobId, scope = "failed", itemIds = [] }) => ({
      jobId,
      scope,
      itemIds,
    }),
    async (id, input, key) => {
      assertJobAction(input.allowedActions, "retry");
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/retry",
        {
          params: {
            path: { workspace_id: id, job_id: input.jobId },
            header: { "Idempotency-Key": key },
          },
          body: {
            scope: input.scope ?? "failed",
            item_ids: input.itemIds ?? [],
          },
        },
      );
      return unwrapApiResponse(result);
    },
  );
}

export function useResolveDetailIntake(workspaceId: string | undefined) {
  return useJobMutation<JobDetail, AuthorizedJobAction & {
    revision: number; confirmationText: string;
    resolutions: { conflict_id: string; retained_fact_ids: string[]; rejected_fact_ids: string[] }[];
    brandlessConfirmation?: string; additionalProductAssetIds?: string[];
  }>(workspaceId, "resolve-intake", (input) => ({
    jobId: input.jobId,
    revision: input.revision,
    confirmationText: input.confirmationText,
    resolutions: input.resolutions,
    brandlessConfirmation: input.brandlessConfirmation,
    additionalProductAssetIds: input.additionalProductAssetIds ?? [],
  }), async (id, input, key) => {
    assertJobAction(input.allowedActions, "resolve_intake");
    const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/jobs/{job_id}/intake/resolve", {
      params: { path: { workspace_id: id, job_id: input.jobId }, header: { "Idempotency-Key": key } },
      body: { expected_revision: input.revision, confirmation_text: input.confirmationText,
        resolutions: input.resolutions, brandless_confirmation: input.brandlessConfirmation,
        additional_product_asset_ids: input.additionalProductAssetIds ?? [] },
    });
    return unwrapApiResponse(result);
  });
}

export function useContinueDetailBrand(workspaceId: string | undefined) {
  return useJobMutation<JobDetail, AuthorizedJobAction & { revision: number }>(
    workspaceId, "continue-detail-brand", input => ({ jobId: input.jobId, revision: input.revision }),
    async (id, input, key) => {
      assertJobAction(input.allowedActions, "resolve_intake");
      const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/jobs/{job_id}", {
        params: { path: { workspace_id: id, job_id: input.jobId }, header: { "Idempotency-Key": key } },
        body: { expected_revision: input.revision },
      });
      return unwrapApiResponse(result);
    },
  );
}

export function useConfirmPlan(workspaceId: string | undefined) {
  return useJobMutation<
    JobDetail,
    AuthorizedJobAction & {
      revision: number;
      approvalProtocol?: "domestic-native-approval-v1";
      nativeApprovalDocumentSha256?: string;
      approvalText?: string;
      approvalDocumentSha256?: string;
      approvedModuleCount?: number;
      approvedPairCanvasAspectRatio?: "3:8";
      automaticDeliveryAuthorization?: { sha256: string; authorization_text: string };
    }
  >(
    workspaceId,
    "confirm-plan",
    (input) => ({ jobId: input.jobId, revision: input.revision,
      approvalProtocol: input.approvalProtocol, nativeApprovalDocumentSha256: input.nativeApprovalDocumentSha256,
      approvalText: input.approvalText, approvalDocumentSha256: input.approvalDocumentSha256,
      approvedModuleCount: input.approvedModuleCount,
      approvedPairCanvasAspectRatio: input.approvedPairCanvasAspectRatio,
      ...(input.automaticDeliveryAuthorization ? { automaticDeliveryAuthorization: { sha256: input.automaticDeliveryAuthorization.sha256, authorization_text: input.automaticDeliveryAuthorization.authorization_text } } : {}) }),
    async (id, input, key) => {
      assertJobAction(input.allowedActions, "confirm_plan");
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/plan/confirm",
        {
          params: {
            path: { workspace_id: id, job_id: input.jobId },
            header: { "Idempotency-Key": key },
          },
          body: {
            ...(input.automaticDeliveryAuthorization ? { domestic_detail_delivery_mode: "AUTO_FINALIZE_AUTHORIZED" as const, automatic_delivery_authorization_text: input.automaticDeliveryAuthorization.authorization_text, automatic_delivery_authorization_sha256: input.automaticDeliveryAuthorization.sha256 } : {}),
            expected_revision: input.revision,
            approval_protocol: input.approvalProtocol,
            native_approval_document_sha256: input.nativeApprovalDocumentSha256,
            approval_text: input.approvalText,
            approval_document_sha256: input.approvalDocumentSha256,
            approved_module_count: input.approvedModuleCount,
            approved_pair_canvas_aspect_ratio: input.approvedPairCanvasAspectRatio,
          },
        },
      );
      return unwrapApiResponse(result);
    },
  );
}

export function useDomesticDetailFinalReview(workspaceId: string | undefined, job: JobDetail | undefined, selection?: { review_job_id: string; report_sha256: string }) {
  return useQuery<DomesticDetailFinalReview>({
    queryKey: [...jobKeys.detail(workspaceId ?? "pending", job?.id ?? "pending"), "final-review", job?.plan?.revision, job?.status, selection ?? null],
    enabled: Boolean(workspaceId && job?.configuration.domestic_detail_pipeline === "aurora-domestic-detail-v4.3.1" && ["awaiting_final_review", "completed"].includes(job.status)),
    retry: false,
    queryFn: async ({ signal }) => {
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/detail-final-review",
        { params: { path: { workspace_id: workspaceId!, job_id: job!.id } }, ...(selection ? { body: selection } : {}), signal },
      );
      return unwrapApiResponse(result);
    },
  });
}

export function useFinalizeJob(workspaceId: string | undefined) {
  return useJobMutation<JobDetail, AuthorizedJobAction & { approvalText: string; finalReviewSha256?: string }>(
    workspaceId,
    "finalize-job",
    ({ jobId, approvalText, finalReviewSha256 }) => ({ jobId, approvalText, ...(finalReviewSha256 ? { finalReviewSha256 } : {}) }),
    async (id, input, key) => {
      assertJobAction(input.allowedActions, "finalize");
      const result = await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/finalize",
        {
          params: {
            path: { workspace_id: id, job_id: input.jobId },
            header: { "Idempotency-Key": key },
          },
          body: { approval_text: input.approvalText, ...(input.finalReviewSha256 ? { final_review_sha256: input.finalReviewSha256 } : {}) },
        },
      );
      return unwrapApiResponse(result);
    },
  );
}

export { activeJobStatuses };
