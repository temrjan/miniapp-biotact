import { describe, it, expect } from 'vitest'

// ═══════════════════════════════════════════════════════════════
// Utility Functions to Test
// ═══════════════════════════════════════════════════════════════

/**
 * Валидация телефона для Узбекистана
 * Формат: +998XXXXXXXXX (всего 13 символов)
 */
export function validatePhone(phone: string): boolean {
  const phoneRegex = /^\+998\d{9}$/
  return phoneRegex.test(phone)
}

/**
 * Форматирование телефона для отображения
 * +998901234567 → +998 90 123 45 67
 */
export function formatPhoneDisplay(phone: string): string {
  if (!phone || phone.length !== 13) return phone

  const match = phone.match(/^\+(\d{3})(\d{2})(\d{3})(\d{2})(\d{2})$/)
  if (!match) return phone

  return `+${match[1]} ${match[2]} ${match[3]} ${match[4]} ${match[5]}`
}

/**
 * Расчет стоимости доставки
 */
export function calculateDeliveryFee(
  deliveryType: 'delivery' | 'pickup',
  cartTotal: number
): number {
  if (deliveryType === 'pickup') return 0

  // Бесплатная доставка при заказе от 100,000 сум
  if (cartTotal >= 100000) return 0

  // Иначе доставка 15,000 сум
  return 15000
}

/**
 * Расчет итоговой суммы заказа
 */
export function calculateOrderTotal(
  cartTotal: number,
  deliveryFee: number
): number {
  return cartTotal + deliveryFee
}

/**
 * Проверка минимальной суммы заказа
 */
export function isOrderValid(cartTotal: number, _deliveryType: 'delivery' | 'pickup'): boolean {
  const MIN_ORDER_AMOUNT = 10000 // 10,000 сум минимальный заказ

  return cartTotal >= MIN_ORDER_AMOUNT
}

/**
 * Форматирование адреса для отображения
 */
export function formatAddress(address: {
  street?: string | null
  house?: string | null
  apartment?: string | null
  entrance?: string | null
  floor?: string | null
}): string {
  const parts: string[] = []

  if (address.street) parts.push(address.street)
  if (address.house) parts.push(`д. ${address.house}`)
  if (address.entrance) parts.push(`под. ${address.entrance}`)
  if (address.floor) parts.push(`эт. ${address.floor}`)
  if (address.apartment) parts.push(`кв. ${address.apartment}`)

  return parts.join(', ')
}

// ═══════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════

describe('Checkout Utilities', () => {

  // ═══════════════════════════════════════════════════════════════
  // Phone Validation
  // ═══════════════════════════════════════════════════════════════

  describe('validatePhone', () => {
    it('accepts valid Uzbekistan phone number', () => {
      expect(validatePhone('+998901234567')).toBe(true)
      expect(validatePhone('+998991234567')).toBe(true)
      expect(validatePhone('+998771234567')).toBe(true)
    })

    it('rejects phone without +998 prefix', () => {
      expect(validatePhone('901234567')).toBe(false)
      expect(validatePhone('998901234567')).toBe(false)
    })

    it('rejects phone with wrong number of digits', () => {
      expect(validatePhone('+99890123456')).toBe(false) // 8 digits
      expect(validatePhone('+9989012345678')).toBe(false) // 10 digits
    })

    it('rejects phone with non-digits', () => {
      expect(validatePhone('+998 90 123 45 67')).toBe(false)
      expect(validatePhone('+998-90-123-45-67')).toBe(false)
      expect(validatePhone('+998(90)1234567')).toBe(false)
    })

    it('rejects empty or invalid input', () => {
      expect(validatePhone('')).toBe(false)
      expect(validatePhone('+998')).toBe(false)
      expect(validatePhone('invalid')).toBe(false)
    })

    it('rejects phone from other countries', () => {
      expect(validatePhone('+79991234567')).toBe(false) // Russia
      expect(validatePhone('+77001234567')).toBe(false) // Kazakhstan
      expect(validatePhone('+12025551234')).toBe(false) // USA
    })
  })

  // ═══════════════════════════════════════════════════════════════
  // Phone Formatting
  // ═══════════════════════════════════════════════════════════════

  describe('formatPhoneDisplay', () => {
    it('formats valid phone number', () => {
      expect(formatPhoneDisplay('+998901234567')).toBe('+998 90 123 45 67')
      expect(formatPhoneDisplay('+998991234567')).toBe('+998 99 123 45 67')
    })

    it('returns original for invalid input', () => {
      expect(formatPhoneDisplay('+99890123456')).toBe('+99890123456')
      expect(formatPhoneDisplay('')).toBe('')
      expect(formatPhoneDisplay('invalid')).toBe('invalid')
    })

    it('handles missing input', () => {
      expect(formatPhoneDisplay('')).toBe('')
    })
  })

  // ═══════════════════════════════════════════════════════════════
  // Delivery Fee Calculation
  // ═══════════════════════════════════════════════════════════════

  describe('calculateDeliveryFee', () => {
    it('returns 0 for pickup', () => {
      expect(calculateDeliveryFee('pickup', 50000)).toBe(0)
      expect(calculateDeliveryFee('pickup', 200000)).toBe(0)
    })

    it('returns 0 for free delivery threshold (100,000+)', () => {
      expect(calculateDeliveryFee('delivery', 100000)).toBe(0)
      expect(calculateDeliveryFee('delivery', 150000)).toBe(0)
      expect(calculateDeliveryFee('delivery', 1000000)).toBe(0)
    })

    it('returns 15,000 for delivery under threshold', () => {
      expect(calculateDeliveryFee('delivery', 99999)).toBe(15000)
      expect(calculateDeliveryFee('delivery', 50000)).toBe(15000)
      expect(calculateDeliveryFee('delivery', 10000)).toBe(15000)
    })

    it('handles edge case at threshold', () => {
      expect(calculateDeliveryFee('delivery', 99999)).toBe(15000)
      expect(calculateDeliveryFee('delivery', 100000)).toBe(0)
      expect(calculateDeliveryFee('delivery', 100001)).toBe(0)
    })
  })

  // ═══════════════════════════════════════════════════════════════
  // Order Total Calculation
  // ═══════════════════════════════════════════════════════════════

  describe('calculateOrderTotal', () => {
    it('calculates total with delivery', () => {
      expect(calculateOrderTotal(50000, 15000)).toBe(65000)
      expect(calculateOrderTotal(85000, 15000)).toBe(100000)
    })

    it('calculates total without delivery (pickup)', () => {
      expect(calculateOrderTotal(50000, 0)).toBe(50000)
      expect(calculateOrderTotal(100000, 0)).toBe(100000)
    })

    it('calculates total for free delivery', () => {
      expect(calculateOrderTotal(150000, 0)).toBe(150000)
    })

    it('handles zero cart', () => {
      expect(calculateOrderTotal(0, 0)).toBe(0)
      expect(calculateOrderTotal(0, 15000)).toBe(15000)
    })
  })

  // ═══════════════════════════════════════════════════════════════
  // Order Validation
  // ═══════════════════════════════════════════════════════════════

  describe('isOrderValid', () => {
    it('accepts order above minimum', () => {
      expect(isOrderValid(10000, 'delivery')).toBe(true)
      expect(isOrderValid(50000, 'delivery')).toBe(true)
      expect(isOrderValid(100000, 'pickup')).toBe(true)
    })

    it('rejects order below minimum', () => {
      expect(isOrderValid(9999, 'delivery')).toBe(false)
      expect(isOrderValid(5000, 'pickup')).toBe(false)
      expect(isOrderValid(0, 'delivery')).toBe(false)
    })

    it('handles edge case at minimum', () => {
      expect(isOrderValid(9999, 'delivery')).toBe(false)
      expect(isOrderValid(10000, 'delivery')).toBe(true)
      expect(isOrderValid(10001, 'delivery')).toBe(true)
    })
  })

  // ═══════════════════════════════════════════════════════════════
  // Address Formatting
  // ═══════════════════════════════════════════════════════════════

  describe('formatAddress', () => {
    it('formats full address', () => {
      const address = {
        street: 'Мирзо-Улугбека',
        house: '101',
        entrance: '2',
        floor: '5',
        apartment: '25',
      }
      expect(formatAddress(address)).toBe(
        'Мирзо-Улугбека, д. 101, под. 2, эт. 5, кв. 25'
      )
    })

    it('formats partial address', () => {
      const address = {
        street: 'Амир Темур',
        house: '15',
        apartment: '10',
        entrance: null,
        floor: null,
      }
      expect(formatAddress(address)).toBe('Амир Темур, д. 15, кв. 10')
    })

    it('handles minimal address', () => {
      const address = {
        street: 'Навои',
        house: null,
        apartment: null,
        entrance: null,
        floor: null,
      }
      expect(formatAddress(address)).toBe('Навои')
    })

    it('handles empty address', () => {
      const address = {
        street: null,
        house: null,
        apartment: null,
        entrance: null,
        floor: null,
      }
      expect(formatAddress(address)).toBe('')
    })

    it('omits null fields', () => {
      const address = {
        street: 'Тестовая',
        house: '1',
        entrance: null,
        floor: null,
        apartment: '5',
      }
      expect(formatAddress(address)).toBe('Тестовая, д. 1, кв. 5')
    })
  })

  // ═══════════════════════════════════════════════════════════════
  // Integration Tests (Critical Business Logic)
  // ═══════════════════════════════════════════════════════════════

  describe('Checkout Flow Integration', () => {
    it('calculates correct total for pickup order', () => {
      const cartTotal = 50000
      const deliveryType = 'pickup'

      const deliveryFee = calculateDeliveryFee(deliveryType, cartTotal)
      const total = calculateOrderTotal(cartTotal, deliveryFee)
      const isValid = isOrderValid(cartTotal, deliveryType)

      expect(deliveryFee).toBe(0)
      expect(total).toBe(50000)
      expect(isValid).toBe(true)
    })

    it('calculates correct total for delivery under threshold', () => {
      const cartTotal = 50000
      const deliveryType = 'delivery'

      const deliveryFee = calculateDeliveryFee(deliveryType, cartTotal)
      const total = calculateOrderTotal(cartTotal, deliveryFee)
      const isValid = isOrderValid(cartTotal, deliveryType)

      expect(deliveryFee).toBe(15000)
      expect(total).toBe(65000)
      expect(isValid).toBe(true)
    })

    it('calculates correct total for delivery above threshold (free)', () => {
      const cartTotal = 150000
      const deliveryType = 'delivery'

      const deliveryFee = calculateDeliveryFee(deliveryType, cartTotal)
      const total = calculateOrderTotal(cartTotal, deliveryFee)
      const isValid = isOrderValid(cartTotal, deliveryType)

      expect(deliveryFee).toBe(0)
      expect(total).toBe(150000)
      expect(isValid).toBe(true)
    })

    it('validates phone and formats correctly', () => {
      const phone = '+998901234567'

      const isValid = validatePhone(phone)
      const formatted = formatPhoneDisplay(phone)

      expect(isValid).toBe(true)
      expect(formatted).toBe('+998 90 123 45 67')
    })

    it('rejects invalid order (below minimum)', () => {
      const cartTotal = 5000
      const deliveryType = 'delivery'

      const isValid = isOrderValid(cartTotal, deliveryType)

      expect(isValid).toBe(false)
    })
  })
})
