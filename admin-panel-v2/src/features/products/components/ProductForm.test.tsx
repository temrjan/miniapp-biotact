import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/shared/api/client'
import { ProductCreatePage } from '../pages/ProductCreatePage'
import { ProductEditPage } from '../pages/ProductEditPage'

const product = {
  id: 1, name: 'Test product', slug: 'test-product', price: '10000.00',
  old_price: '12000.00', is_sale: true, sale_price: '8000.00', currency: 'UZS',
  stock_quantity: 10, is_in_stock: true, is_active: true, is_featured: false,
  sort_order: 0, images: [],
}

function renderPage(edit = false, overrides = {}) {
  vi.spyOn(apiClient, 'get').mockImplementation(async (url) => ({
    data: url.includes('/categories') ? { items: [], total: 0 } : url.includes('/sizes') ? {
      product_id: 1, merchant_is_in_stock: true,
      sizes: ['L', 'XL', '2XL'].map((size) => ({ size, available_quantity: 0, revision: 7 })),
    } : { ...product, ...overrides },
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[edit ? '/products/1/edit' : '/products/new']}>
        <Routes>
          <Route path="/products/new" element={<ProductCreatePage />} />
          <Route path="/products/:id/edit" element={<ProductEditPage />} />
          <Route path="/products" element={<p>Products saved</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

async function fillBase(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Название \(RU\)/), 'New product')
  await user.type(screen.getByLabelText(/^Цена \*/), '10000')
}

function serverError(detail: unknown) {
  return { isAxiosError: true, response: { status: 422, data: { detail } } }
}

describe('Product sale form (R7)', () => {
  beforeEach(() => {
    // jsdom has no layout observer; keep the real Radix checkbox.
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      unobserve() {}
      disconnect() {}
    })
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: product })
    vi.spyOn(apiClient, 'patch').mockResolvedValue({ data: product })
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('keeps the merchant flag from protected inventory while saving depleted sized metadata separately', async () => {
    const user = renderPage(true, { requires_size: true, stock_quantity: null, is_in_stock: false });
    expect(await screen.findByRole('checkbox', { name: 'В наличии' })).toBeChecked();
    expect(screen.queryByLabelText('Количество на складе')).not.toBeInTheDocument();
    expect(screen.getByLabelText('XL', { exact: true })).toHaveValue(0);
    await user.click(screen.getByRole('button', { name: 'Обновить' }));
    expect(await screen.findByText('Описание и цена сохранены.')).toBeVisible();
    expect(apiClient.patch).toHaveBeenCalledTimes(1);
    const call = vi.mocked(apiClient.patch).mock.calls[0];
    expect(call?.[0]).not.toContain('/sizes');
    expect(call?.[1]).toMatchObject({ is_in_stock: true });
    expect(call?.[1]).not.toHaveProperty('stock_quantity');
  });

  it('should show a field error and prevent saving when a sale has no price', async () => {
    const user = renderPage()
    await fillBase(user)
    await user.click(screen.getByRole('checkbox', { name: 'Акция' }))
    await user.click(screen.getByRole('button', { name: 'Создать' }))
    expect(await screen.findByText('Введите цену по акции')).toBeVisible()
    expect(screen.getByLabelText(/Цена по акции/)).toHaveAccessibleDescription('Введите цену по акции')
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it.each(['0', '-1', '10000', '10001', '8000.001'])(
    'should reject invalid sale price %s before saving', async (price) => {
      const user = renderPage(true)
      const input = await screen.findByLabelText(/Цена по акции/)
      await user.clear(input)
      await user.type(input, price)
      await user.click(screen.getByRole('button', { name: 'Обновить' }))
      expect(await screen.findByRole('alert')).toHaveTextContent(/Цена по акции/)
      expect(apiClient.patch).not.toHaveBeenCalled()
    },
  )

  it('should save decimal sale pricing when creating a product', async () => {
    const user = renderPage()
    await fillBase(user)
    await user.click(screen.getByRole('checkbox', { name: 'Акция' }))
    await user.type(screen.getByLabelText(/Цена по акции/), '8000.25')
    await user.click(screen.getByRole('button', { name: 'Создать' }))
    expect(await screen.findByText('Products saved')).toBeVisible()
    expect(apiClient.post).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      price: 10000, is_sale: true, sale_price: 8000.25,
    }))
  })

  it('should save an ordinary product when sale pricing is empty', async () => {
    const user = renderPage()
    await fillBase(user)
    await user.click(screen.getByRole('button', { name: 'Создать' }))
    expect(await screen.findByText('Products saved')).toBeVisible()
    expect(apiClient.post).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      is_sale: false, sale_price: null,
    }))
  })

  it('should retain the stored sale price when disabling and saving a sale', async () => {
    const user = renderPage(true)
    expect(await screen.findByLabelText(/Цена по акции/)).toHaveValue(8000)
    await user.click(screen.getByRole('checkbox', { name: 'Акция' }))
    await user.click(screen.getByRole('button', { name: 'Обновить' }))
    expect(await screen.findByText('Products saved')).toBeVisible()
    expect(apiClient.patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      is_sale: false, sale_price: 8000, old_price: 12000,
    }))
  })

  it('should allow a dormant sale price above the ordinary price when the sale is off', async () => {
    const user = renderPage(true, { is_sale: false, price: '7000.00' })
    expect(await screen.findByLabelText(/Цена по акции/)).toHaveValue(8000)
    await user.click(screen.getByRole('button', { name: 'Обновить' }))
    expect(await screen.findByText('Products saved')).toBeVisible()
    expect(apiClient.patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      price: 7000, is_sale: false, sale_price: 8000,
    }))
  })

  it('should retain an edited price when toggling a sale off and on', async () => {
    const user = renderPage(true, { is_sale: false })
    const input = await screen.findByLabelText(/Цена по акции/)
    expect(input).toHaveValue(8000)
    await user.click(screen.getByRole('checkbox', { name: 'Акция' }))
    await user.clear(input)
    await user.type(input, '7500.5')
    await user.click(screen.getByRole('checkbox', { name: 'Акция' }))
    await user.click(screen.getByRole('checkbox', { name: 'Акция' }))
    expect(input).toHaveValue(7500.5)
    await user.click(screen.getByRole('button', { name: 'Обновить' }))
    expect(await screen.findByText('Products saved')).toBeVisible()
    expect(apiClient.patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      is_sale: true, sale_price: 7500.5,
    }))
  })

  it.each([false, true])('should display a server sale invariant error at the field (edit=%s) and allow correction', async (edit) => {
    const save = edit ? vi.mocked(apiClient.patch) : vi.mocked(apiClient.post)
    save.mockRejectedValueOnce(serverError({ code: 'sale_invariant_violation' }))
    const user = renderPage(edit)
    if (!edit) {
      await fillBase(user)
      await user.click(screen.getByRole('checkbox', { name: 'Акция' }))
      await user.type(screen.getByLabelText(/Цена по акции/), '8000')
    }
    const input = await screen.findByLabelText(/Цена по акции/)
    await user.click(screen.getByRole('button', { name: edit ? 'Обновить' : 'Создать' }))
    const message = 'Цена по акции должна быть больше 0 и меньше обычной цены. Проверьте обе цены.'
    expect(await screen.findByText(message)).toBeVisible()
    expect(input).toHaveAccessibleDescription(message)
    expect(save).toHaveBeenCalledTimes(1)
    await user.clear(input)
    await user.type(input, '7000')
    await user.click(screen.getByRole('button', { name: edit ? 'Обновить' : 'Создать' }))
    expect(await screen.findByText('Products saved')).toBeVisible()
    expect(save).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ sale_price: 7000 }))
  })

  it('should show merged-state rejection beside the sale price after lowering the ordinary price', async () => {
    vi.mocked(apiClient.patch).mockRejectedValueOnce(serverError({ code: 'sale_invariant_violation' }))
    const user = renderPage(true)
    await screen.findByLabelText(/Цена по акции/)
    const regular = screen.getByLabelText(/^Цена \*/)
    await user.clear(regular)
    await user.type(regular, '9000')
    await user.click(screen.getByRole('button', { name: 'Обновить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Проверьте обе цены.')
    await waitFor(() => expect(apiClient.patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ price: 9000, sale_price: 8000 })))
  })
})
