import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { CustomersPage } from "./CustomersPage"
import { customersApi } from "../api/customersApi"

vi.mock("../api/customersApi", () => ({
  customersApi: {
    getAll: vi.fn(),
    getStats: vi.fn(),
    getById: vi.fn(),
  },
}))

const getAllMock = vi.mocked(customersApi.getAll)
const getStatsMock = vi.mocked(customersApi.getStats)

// Radix Select в jsdom: точечные полифилы (спека PR-2, check-F2 план-A).
// jsdom не имеет PointerEvent — Radix проверяет button===0 и pointerType==='mouse'
// на pointerdown; MouseEvent-конструктор pointer-поля не переносит — копируем руками.
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
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.setPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <CustomersPage />
    </QueryClientProvider>
  )
}

describe("CustomersPage — фильтр Biotact Club", () => {
  beforeEach(() => {
    getAllMock.mockReset()
    getStatsMock.mockReset()
    getAllMock.mockResolvedValue({ items: [], total: 0, page: 1, size: 12, pages: 0 })
    getStatsMock.mockResolvedValue({
      total_customers: 0,
      new_today: 0,
      with_phone: 0,
      with_purchases: 0,
      by_problem: { immunity: 0, gut: 0, stress: 0, skin: 0 },
    })
  })

  it("рендерит клубный фильтр со значением «Все клиенты»", async () => {
    renderPage()
    await waitFor(() => expect(getAllMock).toHaveBeenCalled())
    expect(screen.getByText("Все клиенты")).toBeInTheDocument()
  })

  it("выбор «Пришли по QR» со 2-й страницы сбрасывает на page=1 и шлёт club='qr'", async () => {
    // Непустая выдача на 3 страницы (для пагинации); клубный фильтр возвращает пусто —
    // заодно проверяем сообщение пустого состояния под активным фильтром.
    const oneCustomer = {
      telegram_id: 500,
      first_name: "Клиент",
      last_name: null,
      username: null,
      language_code: "ru",
      phone: null,
      problems: [],
      family: [],
      purchased_products: [],
      ai_notes: null,
      created_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-07-01T00:00:00Z",
    }
    getAllMock.mockImplementation((params = {}) =>
      Promise.resolve(
        params.club
          ? { items: [], total: 0, page: 1, size: 12, pages: 0 }
          : { items: [oneCustomer], total: 36, page: params.page ?? 1, size: 12, pages: 3 }
      )
    )

    renderPage()
    await waitFor(() => expect(getAllMock).toHaveBeenCalled())

    // Реально уходим на 2-ю страницу — иначе сброс page=1 нечем отличить от дефолта.
    fireEvent.click(await screen.findByRole("button", { name: "Вперёд" }))
    await waitFor(() =>
      expect(getAllMock).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }))
    )

    fireEvent.pointerDown(screen.getByText("Все клиенты"), {
      button: 0,
      ctrlKey: false,
      pointerId: 1,
      pointerType: "mouse",
    })
    const option = await screen.findByText("Пришли по QR")
    fireEvent.pointerUp(option, { button: 0 })
    fireEvent.click(option)

    await waitFor(() =>
      expect(getAllMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ club: "qr", page: 1 })
      )
    )
    // Пустой результат под активным фильтром — «не найдены», а не «пока нет клиентов».
    expect(await screen.findByText("Клиенты не найдены")).toBeInTheDocument()
  })

  it("выбор «Без анкеты (есть баллы)» шлёт club='no_survey'", async () => {
    renderPage()
    await waitFor(() => expect(getAllMock).toHaveBeenCalled())

    fireEvent.pointerDown(screen.getByText("Все клиенты"), {
      button: 0,
      ctrlKey: false,
      pointerId: 1,
      pointerType: "mouse",
    })
    const option = await screen.findByText("Без анкеты (есть баллы)")
    fireEvent.pointerUp(option, { button: 0 })
    fireEvent.click(option)

    await waitFor(() =>
      expect(getAllMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ club: "no_survey", page: 1 })
      )
    )
  })
})
