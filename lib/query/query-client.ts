import { QueryClient } from "@tanstack/react-query";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry(failureCount, error) {
          const status =
            typeof error === "object" && error !== null && "status" in error
              ? Number(error.status)
              : undefined;
          if (status !== undefined && status < 500) {
            return false;
          }
          return failureCount < 2;
        },
        refetchOnWindowFocus: true,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
