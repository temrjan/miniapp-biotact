import { afterEach, describe, expect, it, vi } from "vitest"
import { AxiosHeaders } from "axios"
import { apiClient } from "@/shared/api/client"
import { customersApi } from "./customersApi"
import type { CustomerDetailDTO } from "../types/customer.types"

const detail: CustomerDetailDTO = {
  id: 42,
  telegram_id: null,
  first_name: "Amina",
  last_name: null,
  username: null,
  phone: null,
  symptoms: ["gut"],
  ai_notes: "Synthetic detail",
  created_at: "2026-07-01T00:00:00Z",
  family_members: [
    {
      id: 7,
      user_id: 42,
      name: "Synthetic family member",
      relation: null,
      age: null,
      symptoms: [],
      created_at: "2026-07-01T00:00:00Z",
    },
  ],
  leads: [
    {
      id: 9,
      user_id: 42,
      telegram_id: 123456,
      request_text: null,
      ai_summary: null,
      recommended_products: [],
      status: "new",
      manager_notes: null,
      created_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-07-01T00:00:00Z",
    },
  ],
}

describe("customersApi.getById", () => {
  afterEach(() => vi.restoreAllMocks())

  it("requests a customer by canonical user ID and preserves the detail DTO", async () => {
    const get = vi.spyOn(apiClient, "get").mockResolvedValueOnce({ data: detail, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } })

    await expect(customersApi.getById(42)).resolves.toBe(detail)
    expect(get).toHaveBeenCalledWith("/admin/crm/customers/42")
  })
})
