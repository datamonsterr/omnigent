import { useQuery } from "@tanstack/react-query";
import { fetchAccountPools } from "@/lib/accountPoolsApi";
import type { AccountPool } from "@/lib/accountPoolsApi";

/**
 * The harness account pools, read once and shared by every picker.
 *
 * The inventory changes rarely, so it is cached; the picker refetches when it
 * opens so a change made in settings shows up without a reload.
 */
export function accountPoolsKey() {
  return ["account-pools"] as const;
}

export function useAccountPools(enabled = true) {
  return useQuery({
    queryKey: accountPoolsKey(),
    queryFn: (): Promise<AccountPool[]> => fetchAccountPools(),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}
