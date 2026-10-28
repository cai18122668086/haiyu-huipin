import { ApiError } from "./api-error";

type ErrorPayload = {
  code?: string;
  message?: string;
  details?: unknown;
  request_id?: string;
  retryable?: boolean;
};

type ErrorEnvelope = {
  error?: ErrorPayload;
  detail?: ErrorPayload;
};

export function mapApiError(error: unknown, response?: Response): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError({
      kind: "aborted",
      code: "REQUEST_ABORTED",
      message: "请求已取消。",
      retryable: false,
    });
  }
  const payload = isErrorEnvelope(error)
    ? error.error ?? error.detail
    : undefined;
  if (response) {
    return new ApiError({
      kind: "http",
      status: response.status,
      code: payload?.code ?? `HTTP_${response.status}`,
      message: payload?.message ?? "请求未能完成。",
      details: payload?.details,
      requestId:
        payload?.request_id ?? response.headers.get("X-Request-Id") ?? undefined,
      retryable: payload?.retryable,
    });
  }
  return new ApiError({
    kind: "network",
    code: "NETWORK_ERROR",
    message: "网络连接失败，请检查网络后重试。",
  });
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as { error?: unknown; detail?: unknown };
  return isErrorPayload(candidate.error) || isErrorPayload(candidate.detail);
}

function isErrorPayload(value: unknown): value is ErrorPayload {
  return typeof value === "object" && value !== null;
}
