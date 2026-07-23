import { render, screen, waitFor } from "@testing-library/react"
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"

import { OrdersListPage } from "./OrdersListPage"
import { ordersApi } from "../api/ordersApi"
import type { Order } from "../types/order.types"

vi.mock("../api/ordersApi", () => ({
  ordersApi: { getAll: vi.fn(), getById: vi.fn(), updateStatus: vi.fn() },
}))

const getAllMock = vi.mocked(ordersApi.getAll)

// Radix Select в jsdom — тот же точечный полифил, что и в CustomersPage.test.tsx.
class PointerEventPolyfill extends MouseEvent {
  pointerId: number
  pointerType: string
  constructor(type: string, props: PointerEventInit = {}) {
    super(type, props)
    this.pointerId = props.pointerId ?? 1
    this.pointerType = props.pointerType ?? "mouse"
  }
}
beforeAll(() => {
  window.PointerEvent = PointerEventPolyfill as typeof PointerEvent
  Element.prototype.scrollIntoView = vi.fn()
})

function makeOrder(id: number, points: number): Order {
  return {
    id,
    order_number: `ORD-${id}`,
    user_id: 1,
    status: "confirmed",
    payment_uuid: null,
    payment_status: "paid",
    paid_at: "2026-07-23T09:00:00Z",
    points_applied: points,
    subtotal: "150000",
    delivery_fee: "0",
    total: "150000",
    currency: "UZS",
    delivery_address: null,
    delivery_phone: "+998900000000",
    delivery_name: "Тест",
    customer_note: null,
    admin_note: null,
    created_at: "2026-07-23T08:00:00Z",
    updated_at: "2026-07-23T09:00:00Z",
    completed_at: null,
    items: [],
  }
}

function renderList(orders: Order[]) {
  getAllMock.mockResolvedValue({ items: orders, total: orders.length, page: 1, size: 20, pages: 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OrdersListPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("OrdersListPage — чем оплачен заказ", () => {
  beforeEach(() => {
    getAllMock.mockReset()
  })

  it("карточный заказ выглядит ровно как раньше — ни слова про баллы", async () => {
    renderList([makeOrder(1, 0)])

    await waitFor(() => expect(screen.getByText("ORD-1")).toBeInTheDocument())
    expect(screen.queryByTestId("payment-kind-1")).not.toBeInTheDocument()
  })

  it("полное покрытие баллами: видно, что денег не поступало", async () => {
    // Тот самый случай, ради которого PR и делается: заказ на 150 000 закрыт, на счёт не пришло
    // ни сума, а в списке он до сих пор выглядел как обычная оплата картой.
    renderList([makeOrder(2, 150_000)])

    const note = await screen.findByTestId("payment-kind-2")
    expect(note.textContent).toContain("денег не поступало")
  })

  it("частичное списание: видно и часть баллами, и сколько собрала карта", async () => {
    renderList([makeOrder(3, 17_300)])

    const note = await screen.findByTestId("payment-kind-3")
    // Intl.NumberFormat разделяет разряды НЕРАЗРЫВНЫМ пробелом (U+00A0) — нормализуем, иначе
    // сравнение с обычным пробелом падает на визуально одинаковых строках.
    const text = note.textContent?.replace(/\u00a0/g, " ")
    expect(text).toContain("17 300")
    expect(text).toContain("132 700")
    expect(text).not.toContain("денег не поступало")
  })
})
