export type JobAction =
  | "cancel"
  | "retry"
  | "confirm_plan"
  | "resolve_intake"
  | "finalize"
  | "edit_plan"
  | "clone"
  | "edit"
  | "submit";

export function allowsJobAction(
  allowedActions: readonly string[],
  action: JobAction,
): boolean {
  return allowedActions.includes(action);
}

export function assertJobAction(
  allowedActions: readonly string[],
  action: JobAction,
): void {
  if (!allowsJobAction(allowedActions, action)) {
    throw new Error(`服务端未授权操作：${action}`);
  }
}
