import { useQuery } from "@tanstack/react-query";
import { fetchAccountPools } from "@/lib/accountPoolsApi";
import type { AccountPool } from "@/lib/accountPoolsApi";

/**
 * The harness account pools, read once and shared by every picker.
 *
 * The inventory changes rarely, so it is cached; the picker refetches when it
 * opens so a change made in settings shows up without a reload.
 */
export function accountPoolsKey(periodDays?: number) {
  return periodDays === undefined
    ? (["account-pools"] as const)
    : (["account-pools", periodDays] as const);
}

export function useAccountPools(enabled = true, periodDays = 30) {
  return useQuery({
    queryKey: accountPoolsKey(periodDays),
    queryFn: (): Promise<AccountPool[]> => fetchAccountPools(periodDays),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}
