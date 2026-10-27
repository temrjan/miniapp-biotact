import { render, screen } from "@testing-library/react"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes } from "react-router-dom"

import { OrdersListPage } from "./OrdersListPage"
import { OrderDetailsPage } from "./OrderDetailsPage"
import { ordersApi } from "../api/ordersApi"
import type { Order } from "../types/order.types"

vi.mock("../api/ordersApi", () => ({
  ordersApi: { getAll: vi.fn(), getById: vi.fn(), updateStatus: vi.fn() },
}))
// The referral-return panel reads its own endpoint; a guest order has no referral data.
vi.mock("@/shared/api/client", () => ({
  apiClient: { get: vi.fn(() => new Promise(() => {})), post: vi.fn() },
}))

const getAllMock = vi.mocked(ordersApi.getAll)
const getByIdMock = vi.mocked(ordersApi.getById)

// Website guest order (G1): no owner, contact data in the delivery fields.
const guestOrder: Order = {
  id: 77,
  order_number: "ORD-20261027120000-ABC123",
  user_id: null,
  status: "pending",
  payment_uuid: null,
  payment_status: "unpaid",
  paid_at: null,
  points_applied: 0,
  subtotal: "120000.00",
  delivery_fee: "0.00",
  total: "120000.00",
  currency: "UZS",
  delivery_address: "Ташкент, ул. Гостевая, 7",
  delivery_phone: "+998901234567",
  delivery_name: "Гость Сайта",
  customer_note: null,
  admin_note: null,
  created_at: "2026-10-27T07:00:00Z",
  updated_at: "2026-10-27T07:00:00Z",
  completed_at: null,
  items: [
    {
      id: 1,
      product_id: 5,
      product_name: "Товар сайта",
      product_price: "60000.00",
      quantity: 2,
      total: "120000.00",
      size: null,
    },
  ],
  size_stock: null,
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

describe("guest order without an owner", () => {
  beforeEach(() => {
    getAllMock.mockReset()
    getByIdMock.mockReset()
  })

  it("orders list shows the guest's name and phone", async () => {
    getAllMock.mockResolvedValue({ items: [guestOrder], total: 1, page: 1, size: 20, pages: 1 })
    render(
      <QueryClientProvider client={client()}>
        <MemoryRouter>
          <OrdersListPage />
        </MemoryRouter>
      </QueryClientProvider>
    )

    expect(await screen.findByText(guestOrder.order_number)).toBeInTheDocument()
    expect(screen.getByText("Гость Сайта")).toBeInTheDocument()
    expect(screen.getByText("+998901234567")).toBeInTheDocument()
  })

  it("order details show the guest's name, phone and address", async () => {
    getByIdMock.mockResolvedValue(guestOrder)
    render(
      <QueryClientProvider client={client()}>
        <MemoryRouter initialEntries={["/orders/77"]}>
          <Routes>
            <Route path="/orders/:id" element={<OrderDetailsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )

    expect(await screen.findByText(`Заказ ${guestOrder.order_number}`)).toBeInTheDocument()
    expect(screen.getByText("Гость Сайта")).toBeInTheDocument()
    expect(screen.getByText("+998901234567")).toBeInTheDocument()
    expect(screen.getByText("Ташкент, ул. Гостевая, 7")).toBeInTheDocument()
    expect(getByIdMock).toHaveBeenCalledWith(77)
  })
})
