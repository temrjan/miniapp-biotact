import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { actor, product, renderCampaign } from '../test/render'
import { campaign, campaignPage } from '../test/fixtures'
import { deferred, refusal } from '../test/http'
import type { CapturedRequest, Reply } from '../test/http'
import { attemptKey, storeAttempt } from '../model/campaignRecovery'
import { useAuthStore } from '@/features/auth/stores/authStore'

let close: (() => void) | undefined
function view(path: string, handler: (r: CapturedRequest) => Reply | Promise<Reply>) {
  const result = renderCampaign(path, r => r.url.startsWith('/products?') ?
    { status: 200, data: { items: [product()], total: 1, page: 1, size: 20, pages: 1 } } : handler(r))
  close = result.close
  return result
}
function marker(kind: 'create' | 'patch' = 'create') {
  storeAttempt(sessionStorage, { schema: 1, actorId: 1, attemptId: crypto.randomUUID(), kind,
    campaignId: kind === 'create' ? null : 1, baseVersion: kind === 'create' ? null : campaign().updated_at,
    sentAt: '2026-10-01T00:00:00Z', createName: kind === 'create' ? 'Retained name' : null })
}
describe('Campaign recovery and negative request channels in actual pages', () => {
  beforeEach(() => { sessionStorage.clear(); actor(); vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { close?.(); close = undefined; vi.restoreAllMocks(); useAuthStore.getState().clearAuth() })
  it('should refuse no-op form submit while a normal edit proves the PATCH observation channel', async () => {
    const observed = view('/campaigns/1', r => ({ status: 200, data: r.method === 'patch' ? campaign({ name: 'Edited', updated_at: '2026-10-01T00:00:00.123457Z' }) : campaign() }))
    const name = await screen.findByLabelText('Название *')
    fireEvent.submit(name.closest('form')!)
    expect(observed.http?.requests.filter(r => r.method === 'patch')).toHaveLength(0)
    await userEvent.setup().clear(name); await userEvent.setup().type(name, 'Edited')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Сохранить черновик' }))
    await waitFor(() => expect(observed.http?.requests.filter(r => r.method === 'patch')).toHaveLength(1))
    expect(observed.http?.requests.find(r => r.method === 'patch')?.body).toEqual({ expected_updated_at: campaign().updated_at, name: 'Edited' })
  })
  it('should restore same-actor pending marker after remount and open a blank draft only after duplicate warning', async () => {
    marker()
    const observed = view('/campaigns/new', () => ({ status: 200, data: campaignPage() }))
    expect(screen.getByLabelText('Название *')).toBeDisabled()
    expect(screen.getByText(/Retained name/)).toBeVisible()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Проверить список' }))
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Закрыть проверку и создать другой черновик' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('дубликатом')
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Отмена' }))
    expect(sessionStorage.getItem(attemptKey(1))).not.toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Закрыть проверку и создать другой черновик' }))
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    expect(screen.getByLabelText('Название *')).toHaveValue('')
    expect(screen.queryByRole('region', { name: 'Проверка результата' })).not.toBeInTheDocument()
    expect(observed.http?.requests.every(r => r.method === 'get')).toBe(true)
    expect(sessionStorage.getItem(attemptKey(1))).toBeNull()
  })
  it('should require fresh list for corrupt marker without displaying its claimed identity', async () => {
    sessionStorage.setItem(attemptKey(1), '{"campaignId":999,"createName":"SECRET_CORRUPT"}')
    const observed = view('/campaigns/new', () => ({ status: 200, data: campaignPage() }))
    expect(screen.queryByText(/SECRET_CORRUPT|999/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Закрыть повреждённую отметку' })).toBeDisabled()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Проверить список' }))
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Закрыть повреждённую отметку' }))
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    expect(sessionStorage.getItem(attemptKey(1))).toBeNull()
    expect(observed.http?.requests.every(r => r.method === 'get')).toBe(true)
  })
  it('should keep unchanged-version recovery blocked after malformed read until explicit acceptance', async () => {
    marker('patch')
    let valid = false
    const observed = view('/campaigns/1', () => ({ status: 200, data: valid ? campaign() : { ...campaign(), id: 2 } }))
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Проверить запись' }))
    await within(screen.getByRole('region', { name: 'Проверка результата' })).findByText('Не удалось получить данные акций.')
    expect(sessionStorage.getItem(attemptKey(1))).not.toBeNull()
    valid = true
    await userEvent.setup().click(screen.getByRole('button', { name: 'Проверить запись' }))
    const accept = await screen.findByRole('button', { name: 'Принять текущее состояние и продолжить' })
    expect(sessionStorage.getItem(attemptKey(1))).not.toBeNull()
    await userEvent.setup().click(accept)
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    expect(sessionStorage.getItem(attemptKey(1))).toBeNull()
    expect(observed.http?.requests.filter(r => r.method !== 'get')).toHaveLength(0)
  })
  it('should hide actual form and cached record on 403 and restore only after fresh authorization', async () => {
    let authorized = true
    const observed = view('/campaigns/1', r => authorized ? { status: 200, data: r.url.endsWith('/1') ? campaign() : campaignPage() } : refusal(403, 'admin_forbidden'))
    const name = await screen.findByLabelText('Название *')
    await userEvent.setup().type(name, 'PRIVATE_INPUT')
    authorized = false
    await userEvent.setup().click(screen.getByRole('button', { name: 'Обновить запись' }))
    await screen.findByRole('button', { name: 'Проверить полномочия' })
    expect(screen.queryByLabelText('Название *')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue(/PRIVATE_INPUT/)).not.toBeInTheDocument()
    expect(observed.client.getQueryData(['campaigns', 1, 'record', 1])).toBeUndefined()
    authorized = true
    await userEvent.setup().click(screen.getByRole('button', { name: 'Проверить полномочия' }))
    expect(await screen.findByLabelText('Название *')).toHaveValue(campaign().name)
    expect(observed.http?.requests.filter(r => r.method !== 'get')).toHaveLength(0)
  })
  it('should keep missing-record input readable and block its writes with a list link', async () => {
    const observed = view('/campaigns/1', r => r.method === 'patch' ? refusal(404, 'campaign_not_found') : { status: 200, data: campaign() })
    const name = await screen.findByLabelText('Название *')
    await userEvent.setup().type(name, 'retained')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Сохранить черновик' }))
    await screen.findByText('Акция не найдена. Вернитесь к списку.')
    expect(name).toHaveValue(campaign().name + 'retained')
    expect(screen.getByRole('link', { name: 'К списку акций' })).toHaveAttribute('href', '/campaigns')
    expect(screen.getByRole('button', { name: 'Опубликовать' })).toBeDisabled()
    fireEvent.submit(name.closest('form')!)
    expect(observed.http?.requests.filter(r => r.method === 'patch')).toHaveLength(1)
  })
  it('should associate server product refusal with the retained product field and keep diagnostics hidden', async () => {
    view('/campaigns/1', r => r.method === 'post' ? refusal(422, 'product_inactive') :
      r.url.startsWith('/products/') ? { status: 200, data: { ...product(21), is_active: false } } : { status: 200, data: campaign({ product_id: 21 }) })
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Опубликовать' }))
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    await waitFor(() => expect(screen.getByLabelText('Товар · ID')).toHaveAttribute('aria-invalid', 'true'))
    expect(screen.getByLabelText('Товар · ID')).toHaveValue('21')
    expect(screen.getByLabelText('Товар · ID')).toHaveAccessibleDescription('Товар отключён.')
    expect(screen.queryByText('PRIVATE_DIAGNOSTIC')).not.toBeInTheDocument()
  })
  it.each(['incomplete_campaign', 'invalid_campaign_dates', 'campaign_overlap', 'invalid_image_url'])('should preserve a saved Draft on %s publication refusal without a chained PATCH', async code => {
    const observed = view('/campaigns/1', r => r.method === 'post' ? refusal(code === 'campaign_overlap' ? 409 : 422, code) : { status: 200, data: campaign() })
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Опубликовать' }))
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    await waitFor(() => expect(observed.controller.getSnapshot().status).toBe('definiteError'))
    expect(screen.getByLabelText('Название *')).toHaveValue(campaign().name)
    expect(observed.http?.requests.filter(r => r.method !== 'get').map(r => [r.method, r.body])).toEqual([
      ['post', { expected_updated_at: campaign().updated_at }],
    ])
    expect(screen.queryByRole('button', { name: 'Завершить акцию' })).not.toBeInTheDocument()
    expect(screen.queryByText('PRIVATE_DIAGNOSTIC')).not.toBeInTheDocument()
    if (code === 'invalid_campaign_dates') expect(screen.getByLabelText('Начало · Ташкент · UTC+05:00')).toHaveAttribute('aria-invalid', 'true')
    if (code === 'invalid_image_url') expect(screen.getByLabelText('Изображение · HTTPS URL загрузки магазина')).toHaveAttribute('aria-invalid', 'true')
  })
  it('should retain a missing selected product ID until deliberate clearing and issue only product reads', async () => {
    const observed = view('/campaigns/1', r => r.url.startsWith('/products/') ? refusal(404, 'product_not_found') : { status: 200, data: campaign({ product_id: 21 }) })
    expect(await screen.findByText('Товар №21: название недоступно; ID сохранён.')).toBeVisible()
    expect(screen.getByLabelText('Товар · ID')).toHaveValue('21')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Очистить товар' }))
    expect(screen.getByLabelText('Товар · ID')).toHaveValue('')
    expect(observed.http?.requests.filter(r => r.url.startsWith('/products')).every(r => r.method === 'get')).toBe(true)
  })
  it('should keep Save and Publish blocked during real upload and discard late result after actor switch', async () => {
    const upload = deferred<Reply>()
    const observed = view('/campaigns/1', r => r.url.startsWith('/uploads/') ? upload.promise : { status: 200, data: campaign() })
    const name = await screen.findByLabelText('Название *')
    await userEvent.setup().type(name, 'edited')
    await userEvent.setup().upload(screen.getByLabelText('Загрузить изображение'), new File(['synthetic'], 'fixture.png', { type: 'image/png' }))
    await waitFor(() => expect(observed.http?.requests.some(r => r.url === '/uploads/products')).toBe(true))
    expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Опубликовать' })).toBeDisabled()
    fireEvent.submit(name.closest('form')!)
    expect(observed.http?.requests.filter(r => r.method === 'patch')).toHaveLength(0)
    act(() => actor(2))
    await act(async () => { upload.resolve({ status: 200, data: { url: 'https://shop.test/uploads/A-private.png', filename: 'A-private.png' } }) })
    expect(await screen.findByLabelText('Изображение · HTTPS URL загрузки магазина')).toHaveValue('')
  })
  it('should fetch the last page once when deletion made the requested page out of range', async () => {
    let shrink = false
    const observed = view('/campaigns', r => ({ status: 200, data: shrink ? campaignPage(Number(r.params.page) === 2 ? [] : [campaign()], Number(r.params.page), 1) :
      campaignPage([campaign()], Number(r.params.page), 21) }))
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Следующая страница' }))
    await screen.findByText('Страница 2 из 2 · Всего 21')
    shrink = true
    await userEvent.setup().click(screen.getByRole('button', { name: 'Обновить список' }))
    await screen.findByText('Страница 1 из 1 · Всего 1')
    expect(observed.http?.requests.filter(r => r.url === '/admin/campaigns').map(r => r.params.page)).toEqual([1, 2, 2, 1])
  })
})
