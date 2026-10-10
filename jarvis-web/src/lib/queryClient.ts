import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        refetchOnWindowFocus: true,
        // Retrying a 4xx (bad request, forbidden, expired session) never helps; only transient failures.
        retry: (failures, error) => !(error instanceof ApiError && error.status < 500) && failures < 2,
      },
    },
  });
}
