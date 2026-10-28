export type ApiErrorKind =
  | "http"
  | "network"
  | "timeout"
  | "aborted"
  | "decode";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly code: string;
  readonly details?: unknown;
  readonly requestId?: string;
  readonly retryable: boolean;

  constructor(options: {
    kind: ApiErrorKind;
    code: string;
    message: string;
    status?: number;
    details?: unknown;
    requestId?: string;
    retryable?: boolean;
  }) {
    super(options.message);
    this.name = "ApiError";
    this.kind = options.kind;
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.requestId = options.requestId;
    this.retryable =
      options.retryable ??
      (
      options.kind === "network" ||
      options.kind === "timeout" ||
      (options.status !== undefined && options.status >= 500)
      );
  }
}
