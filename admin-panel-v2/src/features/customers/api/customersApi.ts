import { apiClient } from "@/shared/api/client"
import { ENDPOINTS } from "@/shared/api/endpoints"
import type {
  Customer,
  CustomersParams,
  PaginatedCustomers,
  CustomersStats,
} from "../types/customer.types"

export const customersApi = {
  getAll: async (params: CustomersParams = {}): Promise<PaginatedCustomers> => {
    const response = await apiClient.get<PaginatedCustomers>(
      ENDPOINTS.CRM.CUSTOMERS,
      { params }
    )
    return response.data
  },

  getById: async (telegramId: number): Promise<Customer> => {
    const response = await apiClient.get<Customer>(
      ENDPOINTS.CRM.CUSTOMER(telegramId)
    )
    return response.data
  },

  getStats: async (): Promise<CustomersStats> => {
    const response = await apiClient.get<CustomersStats>(ENDPOINTS.CRM.STATS)
    return response.data
  },
}
