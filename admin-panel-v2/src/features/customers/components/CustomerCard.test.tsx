import { render, screen } from "@testing-library/react"
import { describe, it, expect } from "vitest"
import { CustomerCard } from "./CustomerCard"
import type { Customer, CustomerLoyalty } from "../types/customer.types"

const baseCustomer: Customer = {
  id: 100,
  telegram_id: 100,
  first_name: "Тимур",
  last_name: null,
  username: "temrjan",
  language_code: "ru",
  phone: "+998770000000",
  problems: [],
  family: [],
  purchased_products: [],
  ai_notes: null,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
}

const clubQr: CustomerLoyalty = {
  source: "qr",
  status: "newbie",
  lifetime_activations: 2,
  gender: "male",
  age: 47,
  city: "Ташкент",
  balance: 5500,
}

describe("CustomerCard — Biotact Club", () => {
  it("бейдж «Biotact Club · QR» для пришедшего по QR", () => {
    render(<CustomerCard customer={{ ...baseCustomer, loyalty: clubQr }} />)
    expect(screen.getByText("Biotact Club · QR")).toBeInTheDocument()
  })

  it("бейдж «Biotact Club» без пометки для direct и для source=null", () => {
    const { rerender } = render(
      <CustomerCard
        customer={{ ...baseCustomer, loyalty: { ...clubQr, source: "direct" } }}
      />
    )
    expect(screen.getByText("Biotact Club")).toBeInTheDocument()

    rerender(
      <CustomerCard customer={{ ...baseCustomer, loyalty: { ...clubQr, source: null } }} />
    )
    expect(screen.getByText("Biotact Club")).toBeInTheDocument()
  })

  it("не-участник: ни бейджа, ни клубных строк (карточка как раньше)", () => {
    render(<CustomerCard customer={{ ...baseCustomer, loyalty: null }} />)
    expect(screen.queryByText(/Biotact Club/)).not.toBeInTheDocument()
    expect(screen.queryByText(/активаций/)).not.toBeInTheDocument()
  })

  it("отсутствие поля loyalty (старый API в окне деплоя) = не-участник", () => {
    render(<CustomerCard customer={baseCustomer} />)
    expect(screen.queryByText(/Biotact Club/)).not.toBeInTheDocument()
  })

  it("строка анкеты: город · пол · возраст", () => {
    render(<CustomerCard customer={{ ...baseCustomer, loyalty: clubQr }} />)
    expect(screen.getByText("Ташкент · муж · 47")).toBeInTheDocument()
  })

  it("частичная анкета — только заполненная часть, без пустых разделителей", () => {
    render(
      <CustomerCard
        customer={{ ...baseCustomer, loyalty: { ...clubQr, gender: null, age: null } }}
      />
    )
    expect(screen.getByText("Ташкент")).toBeInTheDocument()
  })

  it("строка статуса: Новичок, активации и баланс в ru-RU формате", () => {
    render(<CustomerCard customer={{ ...baseCustomer, loyalty: clubQr }} />)
    expect(screen.getByText("Новичок · активаций: 2 · баллов: 5 500")).toBeInTheDocument()
  })

  it("статус «Друг» рендерится по своей метке", () => {
    render(
      <CustomerCard
        customer={{ ...baseCustomer, loyalty: { ...clubQr, status: "biotact_friend" } }}
      />
    )
    expect(screen.getByText(/^Друг · активаций/)).toBeInTheDocument()
  })

  it("неизвестные значения статуса и пола с провода падают в сырое эхо (unconstrained БД)", () => {
    render(
      <CustomerCard
        customer={{
          ...baseCustomer,
          loyalty: {
            ...clubQr,
            // Касты нарочно: ответ сервера — эхо БД, провод может принести неизвестное значение.
            status: "vip" as CustomerLoyalty["status"],
            gender: "other" as CustomerLoyalty["gender"],
          },
        }}
      />
    )
    expect(screen.getByText(/^vip · активаций/)).toBeInTheDocument()
    expect(screen.getByText("Ташкент · other · 47")).toBeInTheDocument()
  })
})
