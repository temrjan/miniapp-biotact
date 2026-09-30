import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { campaign, campaignPage } from '../test/fixtures'
import { actor, product, renderCampaign } from '../test/render'
import { deferred, refusal } from '../test/http'
import type { CapturedRequest, Reply } from '../test/http'
import { useAuthStore } from '@/features/auth/stores/authStore'
import { attemptKey } from '../model/campaignRecovery'

let close: (() => void) | undefined
function page(path: string, handler: (request: CapturedRequest) => Reply | Promise<Reply>) {
  const result = renderCampaign(path, r => {
    if (r.url.startsWith('/products?')) {
      const url = new URL(r.url, 'https://synthetic.test')
      const n = Number(url.searchParams.get('page') ?? 1)
      return { status: 200, data: { items: [product(n === 2 ? 21 : 1)], total: 21, page: n, size: 20, pages: 2 } }
    }
    if (r.url.startsWith('/products/')) return { status: 200, data: product(Number(r.url.split('/')[2]?.split('?')[0])) }
    return handler(r)
  })
  close = result.close
  return result
}
describe('Campaign real route/form/controller journeys', () => {
  beforeEach(() => { sessionStorage.clear(); actor(); vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { close?.(); close = undefined; vi.restoreAllMocks(); useAuthStore.getState().clearAuth() })
  it('should save a name-only Draft once after Enter and expose separate saved-record publication', async () => {
    const response = deferred<Reply>()
    const view = page('/campaigns/new', r => r.method === 'get' ? { status: 200, data: r.url.endsWith('/1') ? campaign({ name: 'Draft' }) : campaignPage() } : response.promise)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Название *'), 'Draft')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: 'Опубликовать' })).not.toBeInTheDocument()
    await user.keyboard('{Enter}{Enter}')
    await waitFor(() => expect(view.http?.requests.filter(r => r.method === 'post')).toHaveLength(1))
    expect(screen.getByLabelText('Название *')).toBeDisabled()
    response.resolve({ status: 201, data: campaign({ name: 'Draft' }) })
    expect(await screen.findByRole('button', { name: 'Опубликовать' })).toBeEnabled()
    expect(view.http?.requests.find(r => r.method === 'post')?.body).toMatchObject({ name: 'Draft', product_id: null })
  })
  it('should omit unchanged dates, send null clearing, revert edits and emit no no-op PATCH', async () => {
    let record = campaign({ image_url: 'https://shop.test/uploads/a.webp', start_at: '2026-10-01T00:00:00.123456Z', end_at: '2026-10-02T00:00:00.123456Z' })
    const view = page('/campaigns/1', r => {
      if (r.method === 'patch') { record = { ...record, image_url: null, updated_at: '2026-10-01T00:00:00.123457Z' }; return { status: 200, data: record } }
      return { status: 200, data: r.url.endsWith('/1') ? record : campaignPage([record]) }
    })
    const user = userEvent.setup()
    const name = await screen.findByLabelText('Название *')
    expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
    await user.clear(name); await user.type(name, 'changed')
    expect(screen.getByRole('button', { name: 'Опубликовать' })).toBeDisabled()
    await user.clear(name); await user.type(name, record.name)
    expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Удалить изображение 1' }))
    await user.click(screen.getByRole('button', { name: 'Сохранить черновик' }))
    await waitFor(() => expect(view.http?.requests.filter(r => r.method === 'patch')).toHaveLength(1))
    expect(view.http?.requests.find(r => r.method === 'patch')?.body).toEqual({ expected_updated_at: '2026-10-01T00:00:00.123456+00:00', image_url: null })
    // Native datetime-local normalizes zero seconds; the exact wire is checked by the model tests.
    expect(screen.getByLabelText('Начало · Ташкент · UTC+05:00')).toHaveValue('2026-10-01T05:00')
  })
  it('should confirm saved publication and End separately and retain immutable history', async () => {
    let record = campaign()
    const view = page('/campaigns/1', r => {
      if (r.method === 'post') {
        record = campaign({ state: r.url.endsWith('/publish') ? 'published' : 'ended', updated_at: r.url.endsWith('/publish') ? '2026-10-01T00:00:00.123457Z' : '2026-10-01T00:00:00.123458Z', published_at: '2026-10-01T00:00:00Z', ended_at: r.url.endsWith('/end') ? '2026-10-01T00:00:01Z' : null })
      }
      return { status: 200, data: record }
    })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Опубликовать' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Отмена' }))
    expect(view.http?.requests.filter(r => r.method === 'post')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Опубликовать' }))
    expect(screen.getByRole('dialog')).toHaveTextContent(record.name)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    const end = await screen.findByRole('button', { name: 'Завершить акцию' })
    expect(screen.getByLabelText('Название *')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Сохранить черновик' })).not.toBeInTheDocument()
    await user.click(end); await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    await screen.findByText(/№1 · Завершена/)
    expect(screen.queryByRole('button', { name: 'Завершить акцию' })).not.toBeInTheDocument()
    expect(view.http?.requests.filter(r => r.method === 'post').map(r => [r.url, r.body])).toEqual([
      ['/admin/campaigns/1/publish', { expected_updated_at: campaign().updated_at }],
      ['/admin/campaigns/1/end', { expected_updated_at: '2026-10-01T00:00:00.123457Z' }],
    ])
    expect(view.http?.requests.some(r => r.method === 'delete' || r.method === 'patch')).toBe(false)
  })
  it('should preserve edits on conflict, fetch separate actual record and adopt only after confirmation', async () => {
    const old = campaign(), current = campaign({ name: 'Other admin', state: 'published', updated_at: '2026-10-01T00:00:00.123457Z' })
    let reads = 0
    const view = page('/campaigns/1', r => r.method === 'patch' ? refusal(409, 'campaign_changed') : { status: 200, data: ++reads === 1 ? old : current })
    const user = userEvent.setup()
    const name = await screen.findByLabelText('Название *')
    await user.clear(name); await user.type(name, 'My input')
    await user.click(screen.getByRole('button', { name: 'Сохранить черновик' }))
    await screen.findByText('Акция изменена. Проверьте актуальную запись.')
    expect(name).toHaveValue('My input')
    await user.click(screen.getByRole('button', { name: 'Проверить запись' }))
    expect(await screen.findByRole('article', { name: 'Запись №1' })).toHaveTextContent('Other admin')
    expect(name).toHaveValue('My input')
    await user.click(screen.getByRole('button', { name: 'Принять актуальную запись' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('изменения будут потеряны')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    expect(name).toHaveValue('Other admin')
    expect(name).toBeDisabled()
    expect(view.http?.requests.filter(r => r.method === 'patch')).toHaveLength(1)
  })
  it('should restore unresolved create after remount, inspect unfiltered pages and choose an ID without name guessing', async () => {
    const view = page('/campaigns/new', r => r.method === 'post' ? refusal(503, 'campaign_result_unknown') :
      { status: 200, data: r.url.endsWith('/2') ? campaign({ id: 2, name: 'Equal name' }) : campaignPage([campaign({ name: 'Equal name' }), campaign({ id: 2, name: 'Equal name' })]) })
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Название *'), 'Equal name')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Сохранить черновик' }))
    await screen.findByText('Результат действия не подтверждён.')
    expect(sessionStorage.getItem(attemptKey(1))).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Закрыть проверку и создать другой черновик' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Проверить список' }))
    await user.click(await screen.findByRole('button', { name: 'Проверить запись №2' }))
    await user.click(await screen.findByRole('button', { name: 'Использовать запись №2 и закрыть проверку' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    expect(await screen.findByText(/№2 · Черновик/)).toBeVisible()
    expect(view.http?.requests.filter(r => r.method === 'post')).toHaveLength(1)
    expect(view.http?.requests.filter(r => r.url === '/admin/campaigns').every(r => !('state' in r.params))).toBe(true)
  })
  it('should hide malformed reads and unavailable connection, preserving ordinary navigation', async () => {
    page('/campaigns', () => refusal(503, 'campaign_service_unavailable'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Подключение акций недоступно')
    expect(screen.queryByText('Акций пока нет.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Создать черновик' })).toBeDisabled()
  })
  it('should reset filters, request page 2 and keep stored Published state after date expiry', async () => {
    const view = page('/campaigns', r => ({ status: 200, data: campaignPage([campaign({ name: `Page ${String(r.params.page)}`, state: 'published', end_at: '2020-01-01T00:00:00Z' })], Number(r.params.page), 21) }))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Следующая страница' }))
    await screen.findByText(/Page 2/)
    await user.selectOptions(screen.getByLabelText('Состояние'), 'draft')
    await screen.findByText(/Page 1/)
    expect(view.http?.requests.at(-1)?.params).toEqual({ page: 1, size: 20, state: 'draft' })
    expect(screen.getByRole('article', { name: 'Запись №1' })).toHaveTextContent('Опубликована')
  })
  it('should hide the previous page during an actual delayed page fetch and distinguish empty from loading', async () => {
    const delayed = deferred<Reply>()
    const observed = page('/campaigns', r => r.params.page === 2 ? delayed.promise : { status: 200, data: campaignPage([campaign({ name: 'Old row' })], 1, 21) })
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Следующая страница' }))
    expect(screen.getByRole('status')).toHaveTextContent('Загрузка страницы')
    expect(screen.queryByText(/Old row/)).not.toBeInTheDocument()
    await act(async () => { delayed.resolve({ status: 200, data: campaignPage([], 2, 0) }) })
    // Empty out-of-range page requests page 1, and its real reply restores the row.
    await screen.findByText(/Old row/)
    expect(observed.http?.requests.map(r => r.params.page)).toEqual([1, 2, 1])
  })
  it('should select product beyond first page and perform no product mutation', async () => {
    const view = page('/campaigns/new', () => ({ status: 200, data: campaignPage() }))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Следующие товары' }))
    await user.click(await screen.findByRole('button', { name: 'Synthetic product 21 · №21' }))
    expect(screen.getByLabelText('Товар · ID')).toHaveValue('21')
    expect(await screen.findByText('Synthetic product 21 · №21', { selector: 'p' })).toBeVisible()
    expect(view.http?.requests.every(r => r.method === 'get')).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Очистить товар' }))
    expect(screen.getByLabelText('Товар · ID')).toHaveValue('')
  })
  it.each(['/campaigns/abc', '/campaigns/0', '/campaigns/9007199254740992'])('should refuse malformed deep link %s before detail HTTP', async path => {
    const view = page(path, () => ({ status: 200, data: campaign() }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Некорректный ID')
    expect(view.http?.requests).toHaveLength(0)
  })
  it('should redirect anonymous deep link and guard an actor switch from late rows', async () => {
    useAuthStore.getState().clearAuth()
    const view = page('/campaigns/1', () => ({ status: 200, data: campaign() }))
    expect(await screen.findByText('Login destination')).toBeVisible()
    expect(view.http?.requests).toHaveLength(0)
  })
  it('should hide actor A input immediately on token or actor change', async () => {
    page('/campaigns/1', () => ({ status: 200, data: campaign() }))
    const name = await screen.findByLabelText('Название *')
    await userEvent.setup().type(name, 'PRIVATE_A')
    act(() => { actor(2, 'actor-B') })
    await waitFor(() => expect(screen.queryByDisplayValue(/PRIVATE_A/)).not.toBeInTheDocument())
  })
})
