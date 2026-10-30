import notice from "@/backend/app/processing_notice.json";

export const PROCESSING_NOTICE = notice;
export type ProcessingConsentChoice = { policyVersion: string; hongKongProcessing: boolean; aiTraining: boolean };
export const processingChoice = (hongKongProcessing: boolean, aiTraining: boolean): ProcessingConsentChoice => ({ policyVersion: notice.version, hongKongProcessing, aiTraining });
export type ProcessingConsentStatus = { policy_version: string; accepted: boolean; accepted_at: string | null; notice: { version: string; crossBorder: string; aiTraining: string } };

export async function requestProcessingConsent(method = "GET", choice?: ProcessingConsentChoice): Promise<ProcessingConsentStatus> {
  const response = await fetch("/api/v1/me/processing-consent", {
    method, credentials: "include", headers: { "Content-Type": "application/json", rid: "session" },
    body: choice ? JSON.stringify(choice) : undefined, signal: AbortSignal.timeout(20_000),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || "暂时无法读取处理授权，请稍后重试。");
  return body;
}
