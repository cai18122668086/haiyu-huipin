import { ApiError } from "./api-error";
import { mapApiError } from "./error-mapper";

export type ApiResult<T> = {
  data?: T;
  error?: unknown;
  response: Response;
};

export function unwrapApiResponse<T>({ data, error, response }: ApiResult<T>): T {
  if (error !== undefined || !response.ok || data === undefined) {
    throw mapApiError(error, response);
  }
  return data;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "请求未能完成，请稍后重试。";
}


export function errorDiagnostic(error: unknown): string {
  if (!(error instanceof ApiError)) return errorMessage(error);
  const metadata = [
    error.code,
    error.status !== undefined ? `HTTP ${error.status}` : null,
    error.requestId ? `请求 ${error.requestId}` : null,
    `可重试：${error.retryable ? "是" : "否"}`,
  ].filter(Boolean).join(" · ");
  return metadata ? `${error.message}（${metadata}）` : error.message;
}
