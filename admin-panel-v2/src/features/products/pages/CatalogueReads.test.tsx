import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { apiClient } from '@/shared/api/client'
import { isRecord } from '@/shared/api/catalogue'
import { categoriesApi } from '@/features/categories/api/categoriesApi'
import { bannersApi } from '@/features/banners/api/bannersApi'
import { CategoriesListPage } from '@/features/categories/pages/CategoriesListPage'
import { BannersListPage } from '@/features/banners/pages/BannersListPage'
import { ProductsListPage } from './ProductsListPage'
import { ProductCreatePage } from './ProductCreatePage'

const clients: QueryClient[] = []
const categories = Array.from({ length: 102 }, (_, i) => ({ id: i + 1, name: `Category ${i + 1}`,
  name_uz: null, slug: `category-${i + 1}`, is_active: true, sort_order: 0 }))
const banners = Array.from({ length: 102 }, (_, i) => ({ id: i + 1, title: `Banner ${i + 1}`,
  is_active: i < 100, sort_order: i, created_at: '2026-10-01', updated_at: '2026-10-01' }))
function mount(element: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  clients.push(client)
  return render(<QueryClientProvider client={client}><MemoryRouter>{element}</MemoryRouter></QueryClientProvider>)
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  vi.spyOn(apiClient, 'get').mockImplementation(async (endpoint, config) => {
    const url = new URL(endpoint, 'http://admin.test')
    const params = new URLSearchParams(url.search)
    for (const [key, value] of Object.entries(config?.params ?? {})) params.set(key, String(value))
    const page = Number(params.get('page') || 1)
    const size = Number(params.get('size') || 20)
    if (url.pathname === '/categories') return { data: {
      items: categories.slice((page - 1) * size, page * size), total: 102, page, size, pages: Math.ceil(102 / size),
    } }
    if (url.pathname === '/products') return { data: {
      items: [{ id: page, name: `Product ${page}`, is_active: true, stock_quantity: 4, price: '10.25', currency: 'UZS' }],
      total: 40, page, size, pages: 2,
    } }
    if (url.pathname === '/banners') {
      const skip = Number(params.get('skip') || 0)
      return { data: { items: banners.slice(skip, skip + Number(params.get('limit') || 100)), total: 102 } }
    }
    if (url.pathname === '/settings/banner_interval') return { data: { value: '3000' } }
    throw new Error(`Unexpected GET ${endpoint}`)
  })
})
afterEach(() => { cleanup(); clients.splice(0).forEach(c => c.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('product form category choices include a category beyond 100 without changing the paged list API', async () => {
  mount(<ProductCreatePage />)
  await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith('/categories', { params: { page: 2, size: 100, active_only: false } }))
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' })
  const list = await screen.findByRole('listbox')
  const choice = within(list).getByText('Category 102')
  expect(choice.closest('[role=option]')).toBeInTheDocument()
  fireEvent.keyDown(choice, { key: 'Enter' })
  expect(screen.getByRole('combobox')).toHaveTextContent('Category 102')
  vi.mocked(apiClient.get).mockClear()
  await categoriesApi.getAll({ page: 2, size: 20, active_only: false })
  expect(apiClient.get).toHaveBeenCalledTimes(1)
})

it('banner administration renders inactive entries after the first 100', async () => {
  mount(<BannersListPage />)
  expect(await screen.findByText('Banner 102')).toBeInTheDocument()
  expect(screen.getAllByText('Скрыт')).toHaveLength(2)
  expect(apiClient.get).toHaveBeenCalledWith('/banners', { params: { skip: 100, limit: 100 } })
})

it('category search keeps the server pager on an empty local subset and reaches the matching page', async () => {
  mount(<CategoriesListPage />)
  await screen.findByText('Category 1')
  fireEvent.change(screen.getByPlaceholderText('Поиск по названию или slug...'), { target: { value: 'Category 21' } })
  expect(screen.getByText('Нет категорий')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Вперёд' }))
  expect(await screen.findByText('Category 21')).toBeInTheDocument()
  expect(apiClient.get).toHaveBeenLastCalledWith('/categories', { params: { page: 2, size: 20, active_only: false } })
})

it('inactive products keep their server pager when the current page has only active rows', async () => {
  mount(<ProductsListPage />)
  await screen.findByText('Product 1')
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' })
  fireEvent.keyDown(await screen.findByText('Только неактивные'), { key: 'Enter' })
  await screen.findByText('Нет товаров')
  fireEvent.click(screen.getByRole('button', { name: 'Вперёд' }))
  await waitFor(() => expect(apiClient.get).toHaveBeenLastCalledWith(expect.stringContaining('page=2')))
  expect(await screen.findByText('Нет товаров')).toBeInTheDocument()
})

it.each(['categories', 'banners'])('complete %s reads reject a later malformed page instead of partial success', async kind => {
  const original = vi.mocked(apiClient.get).getMockImplementation()
  if (!original) throw new Error('Missing fixture')
  vi.mocked(apiClient.get).mockImplementation(async (endpoint, config) => {
    if (isRecord(config?.params) && (config.params.page === 2 || config.params.skip === 100)) return { data: { items: [{}], total: 102 } }
    return original(endpoint, config)
  })
  await expect(kind === 'categories' ? categoriesApi.getChoices() : bannersApi.getAll()).rejects.toThrow()
})
