"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";
import type { CapabilityStatus, RuntimeStatus } from "@/lib/api/types";
import { useAuthSession } from "@/features/auth/api/session";

export const runtimeKeys = {
  status: ["runtime-status"] as const,
};

export function useRuntimeStatus() {
  const authSession = useAuthSession();
  return useQuery({
    enabled: authSession.data === true,
    queryKey: runtimeKeys.status,
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET("/api/v1/runtime", { signal });
      return unwrapApiResponse(result);
    },
    staleTime: 60_000,
    retry: 1,
  });
}

export function resolveRuntimeCapability(
  runtime: RuntimeStatus | null | undefined,
  capabilityKey: string,
): CapabilityStatus | undefined {
  if (!runtime) return undefined;

  const featureCapabilities = runtime.capabilities.features ?? {};
  const directFeature = featureCapabilities[capabilityKey];
  if (directFeature) return directFeature;

  const familyKey = capabilityKey.split(".", 1)[0];
  const familyFeature = featureCapabilities[familyKey];
  if (familyFeature) return familyFeature;

  if (capabilityKey === "local_edit") return runtime.capabilities.local_edit;
  if (capabilityKey === "detail_pack") return runtime.capabilities.detail_pack;
  return undefined;
}

export type WorkflowPresentation = {
  mode: "provider" | "mock" | "local" | "unavailable" | "unknown";
  canDeliverImages: boolean;
  canSubmit: boolean;
  badge: string;
  message: string;
  reasonCode?: string | null;
};

export function presentWorkflow(
  capability: CapabilityStatus | null | undefined,
): WorkflowPresentation {
  if (!capability) {
    return {
      mode: "unknown",
      canDeliverImages: false,
      canSubmit: false,
      badge: "服务状态确认中",
      message: "正在确认生成服务状态，暂时无法创建任务。",
    };
  }
  if (capability.availability !== "available") {
    return {
      mode: "unavailable",
      canDeliverImages: false,
      canSubmit: false,
      badge: "当前服务不可用",
      message: "当前生成服务暂不可用，提交入口已关闭。",
      reasonCode: capability.reason_code,
    };
  }
  if (capability.execution === "provider") {
    return {
      mode: "provider",
      canDeliverImages: true,
      canSubmit: true,
      badge: "生成服务可用",
      message: "任务将由生成服务处理并交付图片。",
      reasonCode: capability.reason_code,
    };
  }
  if (capability.execution === "mock") {
    return {
      mode: "mock",
      canDeliverImages: false,
      canSubmit: true,
      badge: "基础流程可用",
      message: "当前支持素材上传与任务流程，暂不生成可交付图片。",
      reasonCode: capability.reason_code,
    };
  }
  if (capability.execution === "local") {
    return {
      mode: "local",
      canDeliverImages: true,
      canSubmit: true,
      badge: "生成服务可用",
      message: "任务将由当前可用的生成服务处理。",
      reasonCode: capability.reason_code,
    };
  }
  return {
    mode: "unavailable",
    canDeliverImages: false,
    canSubmit: false,
    badge: "当前服务不可用",
    message: "当前生成服务暂不可用，提交入口已关闭。",
    reasonCode: capability.reason_code,
  };
}

function supportsProvider(capability: CapabilityStatus | undefined): boolean {
  return (
    capability?.availability === "available" &&
    capability.execution === "provider"
  );
}

export type RuntimePresentation = {
  badge: string;
  summary: string;
  assurance: string;
  providerGenerationEnabled: boolean;
};

export function presentRuntime(
  runtime: RuntimeStatus | null | undefined,
): RuntimePresentation {
  const localEditProvider = supportsProvider(runtime?.capabilities.local_edit);
  const detailPackProvider = supportsProvider(runtime?.capabilities.detail_pack);
  const providerGenerationEnabled = localEditProvider || detailPackProvider;

  if (!runtime) {
    return {
      badge: "服务状态确认中",
      summary: "当前生成服务暂不可用，图片生成入口已关闭。",
      assurance: "不会启动图片生成",
      providerGenerationEnabled: false,
    };
  }

  const workflows = [
    runtime.capabilities.local_edit,
    runtime.capabilities.detail_pack,
  ];
  const allMock = workflows.every(
    (item) => item.availability === "available" && item.execution === "mock",
  );

  if (allMock) {
    return {
      badge: "基础流程可用",
      summary: "素材与任务状态会正常保存，当前暂不生成可交付图片。",
      assurance: "图片生成暂不可用",
      providerGenerationEnabled: false,
    };
  }

  return {
    badge: "服务状态",
    summary: providerGenerationEnabled
      ? "部分图片生成功能当前可用，请按各功能页面提示操作。"
      : "部分图片生成功能当前不可用，请稍后重试。",
    assurance: providerGenerationEnabled
      ? "按服务状态执行"
      : "图片生成暂不可用",
    providerGenerationEnabled,
  };
}
