import { useQuery } from "@tanstack/react-query"
import { customersApi } from "../api/customersApi"
import type { CustomersParams } from "../types/customer.types"

export const customerKeys = {
  all: ["customers"] as const,
  lists: () => [...customerKeys.all, "list"] as const,
  list: (params: CustomersParams) => [...customerKeys.lists(), params] as const,
  stats: () => [...customerKeys.all, "stats"] as const,
  details: () => [...customerKeys.all, "detail"] as const,
  detail: (userId: number) => [...customerKeys.details(), userId] as const,
}

export const useCustomers = (params: CustomersParams = {}) => {
  return useQuery({
    queryKey: customerKeys.list(params),
    queryFn: () => customersApi.getAll(params),
  })
}

export const useCustomersStats = () => {
  return useQuery({
    queryKey: customerKeys.stats(),
    queryFn: () => customersApi.getStats(),
  })
}

export const useCustomer = (userId: number) => {
  return useQuery({
    queryKey: customerKeys.detail(userId),
    queryFn: () => customersApi.getById(userId),
    enabled: !!userId,
  })
}
