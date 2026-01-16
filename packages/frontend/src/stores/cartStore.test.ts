import { describe, it, expect, beforeEach } from 'vitest'
import { useCartStore } from './cartStore'
import type { Product } from '@/types'

const mockProduct: Product = {
  id: '1',
  name: 'Test Product',
  description: 'Test description',
  price: 100000,
  imageUrl: '/test.jpg',
  categoryId: 'cat1',
  inStock: true,
}

describe('cartStore', () => {
  beforeEach(() => {
    useCartStore.getState().clearCart()
  })

  it('starts with empty cart', () => {
    const items = useCartStore.getState().items
    expect(items).toHaveLength(0)
  })

  it('adds item to cart', () => {
    useCartStore.getState().addItem(mockProduct)
    const items = useCartStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0].product.id).toBe('1')
    expect(items[0].quantity).toBe(1)
  })

  it('increases quantity when adding same item', () => {
    useCartStore.getState().addItem(mockProduct)
    useCartStore.getState().addItem(mockProduct)
    const items = useCartStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0].quantity).toBe(2)
  })

  it('updates item quantity', () => {
    useCartStore.getState().addItem(mockProduct)
    useCartStore.getState().updateQuantity('1', 5)
    const items = useCartStore.getState().items
    expect(items[0].quantity).toBe(5)
  })

  it('removes item from cart', () => {
    useCartStore.getState().addItem(mockProduct)
    useCartStore.getState().removeItem('1')
    expect(useCartStore.getState().items).toHaveLength(0)
  })

  it('clears cart', () => {
    useCartStore.getState().addItem(mockProduct)
    useCartStore.getState().addItem({ ...mockProduct, id: '2' })
    useCartStore.getState().clearCart()
    expect(useCartStore.getState().items).toHaveLength(0)
  })

  it('calculates total items', () => {
    useCartStore.getState().addItem(mockProduct)
    useCartStore.getState().addItem(mockProduct)
    useCartStore.getState().addItem({ ...mockProduct, id: '2' })
    const total = useCartStore.getState().items.reduce((sum, item) => sum + item.quantity, 0)
    expect(total).toBe(3)
  })

  it('calculates total amount', () => {
    useCartStore.getState().addItem(mockProduct)
    useCartStore.getState().addItem(mockProduct)
    const items = useCartStore.getState().items
    const totalAmount = items.reduce((sum, item) => sum + item.product.price * item.quantity, 0)
    expect(totalAmount).toBe(200000)
  })
})
