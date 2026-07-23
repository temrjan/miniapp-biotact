import { describe, it, expect } from "vitest"
import { getPaymentKind, getCardAmount } from "./paymentKind"

/**
 * Денежная семантика: заказ может быть `paid`, при этом денег не поступало вовсе.
 * Эти тесты держат границу между «закрыт» и «оплачен деньгами».
 */
const order = (points: number, total: string) => ({ points_applied: points, total })

describe("getPaymentKind", () => {
  it("без баллов — обычная карта (карточный путь не меняется ни на слово)", () => {
    expect(getPaymentKind(order(0, "150000"))).toBe("card")
  })

  it("часть суммы баллами — частичное списание", () => {
    expect(getPaymentKind(order(17_300, "150000"))).toBe("partial-points")
  })

  it("баллы покрыли всю сумму — денег не поступало", () => {
    expect(getPaymentKind(order(150_000, "150000"))).toBe("full-points")
  })

  it("покрытие РОВНО на всю сумму считается полным, а не частичным", () => {
    // Граница `>=`, не `>`: при равенстве карта не участвует вообще, Multicard не вызывается.
    expect(getPaymentKind(order(150_000, "150000"))).not.toBe("partial-points")
  })

  it("total приходит строкой — сравнение идёт по числу, а не по строке", () => {
    // Без явного Number() сравнение опиралось бы на неявное приведение JS. Кейс подобран так,
    // что строковое сравнение дало бы другой ответ: "9000" > "150000" лексикографически.
    expect(getPaymentKind(order(9_000, "150000"))).toBe("partial-points")
  })
})

describe("getCardAmount", () => {
  it("остаток картой = сумма минус баллы", () => {
    expect(getCardAmount(order(17_300, "150000"))).toBe(132_700)
  })

  it("при полном покрытии карта собрала ноль", () => {
    expect(getCardAmount(order(150_000, "150000"))).toBe(0)
  })

  it("аномалия «баллов больше суммы» не даёт отрицательный остаток", () => {
    expect(getCardAmount(order(200_000, "150000"))).toBe(0)
  })
})
