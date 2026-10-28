const viteEnv = (import.meta as ImportMeta & {
  env?: Record<string, string | undefined> & { DEV?: boolean };
}).env;

export const LOCAL_DEV_AUTH_USER_ID =
  (typeof process !== "undefined" ? process.env.NEXT_PUBLIC_LOCAL_DEV_USER_ID : undefined) ||
  viteEnv?.VITE_LOCAL_DEV_USER_ID?.trim() ||
  "usr_development";

export function isLocalDevelopmentAuthEnabled(): boolean {
  const nextPublicFlag =
    typeof process !== "undefined" ? process.env.NEXT_PUBLIC_LOCAL_DEV_AUTH : undefined;
  const isDevelopmentRuntime =
    viteEnv?.DEV === true ||
    (typeof process !== "undefined" && process.env.NODE_ENV !== "production");
  return (
    isDevelopmentRuntime &&
    (nextPublicFlag === "true" || viteEnv?.VITE_LOCAL_DEV_AUTH === "true")
  );
}
