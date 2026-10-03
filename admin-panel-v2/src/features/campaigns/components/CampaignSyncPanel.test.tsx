import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query'
import { render, cleanup } from '@testing-library/react'
import { CampaignSyncPanel } from './CampaignSyncPanel'
import { campaign } from '../test/fixtures'
import { syncFixture } from '../test/syncFixture'
import { actor, renderCampaign, product } from '../test/render'
import { deferred, installHttp, refusal } from '../test/http'
import type { Reply } from '../test/http'
import type { CampaignSync } from '../model/campaignSync'
import { EXPIRED_TEXT } from '../model/campaignSync'
import { campaignController } from '../hooks/useCampaignActions'

let close: (() => void) | undefined
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); actor(); vi.spyOn(console, 'error').mockImplementation(() => {}) })
afterEach(() => { close?.(); close = undefined; cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })
function productsReply(url: string): Reply | null {
  if (url.startsWith('/products?')) return { status: 200, data: { items: [product()], total: 1, page: 1, size: 20, pages: 1 } }
  if (url.startsWith('/products/')) return { status: 200, data: product() }
  return null
}
function panel(dto: CampaignSync, handler: () => Reply | Promise<Reply> = () => ({ status: 200, data: dto } as Reply)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const http = installHttp(handler)
  const view = render(<QueryClientProvider client={client}><CampaignSyncPanel campaign={campaign({ state: dto.campaign_state })} /></QueryClientProvider>)
  close = () => { cleanup(); campaignController(client).dispose(); client.clear(); http.restore() }
  return { http, client, view }
}

describe('T13 independent synchronization display', () => {
  it.each([
    ['not_published', 'draft', 'Доставка начнётся после публикации акции.'],
    ['disabled', 'published', 'Отправка в магазин выключена.'],
    ['pending', 'published', 'Ожидается подтверждение доставки в магазин.'],
    ['expired', 'published', EXPIRED_TEXT],
    ['blocked', 'published', 'Доставка требует проверки ответственным за систему.'],
    ['pending', 'ended', 'Ожидается подтверждение завершения в магазине.'],
    ['confirmed', 'ended', 'Завершение в магазине подтверждено.'],
  ] as const)('shows %s %s', async (status, campaign_state, text) => {
    const observed = panel(syncFixture({ status, campaign_state, stop_confirmed: campaign_state === 'ended', stop_deadline: '2026-10-02T00:01:00.000000Z' }))
    expect(await screen.findByText(text)).toBeVisible()
    expect(observed.http.requests.every(r => r.method === 'get')).toBe(true)
    expect(screen.queryByText(/PRIVATE|QR|готова/)).not.toBeInTheDocument()
  })
  it('expires an already open confirmation without any read or write, even when wall clock moves backwards', async () => {
    let monotonic = 0
    vi.spyOn(performance, 'now').mockImplementation(() => monotonic)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const observed = panel(syncFixture())
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    screen.getByText(/Доставка подтверждена\. Срок подтверждения:/)
    vi.setSystemTime(new Date('2020-01-01'))
    monotonic = 61000
    // A rerender caused by the explicit deadline timer is sufficient; no query polling.
    await act(async () => { await vi.advanceTimersByTimeAsync(61000) })
    expect(screen.getByText(EXPIRED_TEXT)).toBeVisible()
    expect(observed.http.requests).toHaveLength(1)
  })
  it('updates pending End notice at its deadline without fabricating End acknowledgement', async () => {
    let monotonic = 0
    vi.spyOn(performance, 'now').mockImplementation(() => monotonic)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const observed = panel(syncFixture({ status: 'pending', campaign_state: 'ended', stop_deadline: '2026-10-02T00:00:01.000000Z' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    screen.getByText('Ожидается подтверждение завершения в магазине.')
    monotonic = 2000
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(screen.getByText('Предыдущая копия истекла. Подтверждение завершения ещё ожидается.')).toBeVisible()
    expect(screen.queryByText('Завершение в магазине подтверждено.')).not.toBeInTheDocument()
    expect(observed.http.requests).toHaveLength(1)
  })
  it('subtracts in-flight latency and does not restart expired data on a failed refresh', async () => {
    let monotonic = 0, reads = 0
    vi.spyOn(performance, 'now').mockImplementation(() => monotonic)
    const response = deferred<Reply>()
    const observed = panel(syncFixture(), () => ++reads === 1 ? response.promise : Promise.resolve(refusal(503, 'service_unavailable')))
    expect(screen.getByText('Проверяется доставка в магазин…')).toBeVisible()
    monotonic = 61000
    await act(async () => response.resolve({ status: 200, data: syncFixture() }))
    await screen.findByText(EXPIRED_TEXT)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Обновить синхронизацию' }))
    await screen.findByText('Не удалось проверить доставку. Повторите позже.')
    expect(observed.http.requests).toHaveLength(2)
  })
  it('keeps a saved campaign on sync 503 and retries only synchronization', async () => {
    let failed = true
    const observed = renderCampaign('/campaigns/1', r => productsReply(r.url) ?? (r.url.endsWith('/k1-sync') ? failed ? refusal(503, 'service_unavailable') : { status: 200, data: syncFixture({ campaign_state: 'draft', status: 'not_published' }) } : { status: 200, data: campaign() }))
    close = observed.close
    await screen.findByText('Не удалось проверить доставку. Повторите позже.')
    expect(screen.getByLabelText('Название *')).toHaveValue(campaign().name)
    failed = false
    await userEvent.setup().click(screen.getByRole('button', { name: 'Обновить синхронизацию' }))
    await screen.findByText('Доставка начнётся после публикации акции.')
    expect(observed.http!.requests.filter(r => r.url === '/admin/campaigns/1')).toHaveLength(1)
    expect(observed.http!.requests.every(r => r.method === 'get')).toBe(true)
  })
  it.each([401, 403])('clears protected campaign and sync data on %i', async status => {
    const observed = renderCampaign('/campaigns/1', r => productsReply(r.url) ?? (r.url.endsWith('/k1-sync') ? refusal(status, status === 401 ? 'invalid_admin_auth' : 'admin_forbidden') : { status: 200, data: campaign() }))
    close = observed.close
    await screen.findByRole('button', { name: 'Проверить полномочия' })
    expect(screen.queryByLabelText('Название *')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Доставка в магазин' })).not.toBeInTheDocument()
    expect(observed.client.getQueriesData({ queryKey: ['campaigns', 1] }).every(([, data]) => data === undefined)).toBe(true)
  })
  it('discards a late response after administrator session change', async () => {
    const late = deferred<Reply>()
    let reads = 0
    const observed = panel(syncFixture(), () => ++reads === 1 ? late.promise : Promise.resolve({ status: 200, data: syncFixture({ status: 'disabled' }) }))
    await waitFor(() => expect(observed.http.requests).toHaveLength(1))
    act(() => actor(2))
    await screen.findByText('Отправка в магазин выключена.')
    await act(async () => late.resolve({ status: 200, data: syncFixture() }))
    expect(screen.queryByText(/Доставка подтверждена/)).not.toBeInTheDocument()
    expect(observed.client.getQueriesData({ queryKey: ['campaigns', 1] })).toEqual([])
  })
  it('discards the old campaign response while navigation requests another campaign', async () => {
    const late = deferred<Reply>()
    let reads = 0
    const observed = panel(syncFixture(), () => ++reads === 1 ? late.promise : Promise.resolve({ status: 200, data: syncFixture({ campaign_id: '2', status: 'disabled' }) }))
    await waitFor(() => expect(observed.http.requests).toHaveLength(1))
    observed.view.rerender(<QueryClientProvider client={observed.client}><CampaignSyncPanel campaign={campaign({ id: 2, state: 'published' })} /></QueryClientProvider>)
    await screen.findByText('Отправка в магазин выключена.')
    await act(async () => late.resolve({ status: 200, data: syncFixture() }))
    expect(screen.queryByText(/Доставка подтверждена/)).not.toBeInTheDocument()
    expect(observed.http.requests.map(r => r.url)).toEqual(['/admin/campaigns/1/k1-sync', '/admin/campaigns/2/k1-sync'])
  })
  it('reads the Ended status after a confirmed End and keeps End independent of a sync failure', async () => {
    let ended = false
    const observed = renderCampaign('/campaigns/1', r => {
      if (r.url.endsWith('/k1-sync')) return ended ? { status: 200, data: syncFixture({ campaign_state: 'ended', stop_confirmed: true }) } : refusal(503, 'service_unavailable')
      if (r.method === 'post') ended = true
      return { status: 200, data: campaign({ state: ended ? 'ended' : 'published', updated_at: ended ? '2026-10-02T01:00:00Z' : campaign().updated_at }) }
    })
    close = observed.close
    await screen.findByText('Не удалось проверить доставку. Повторите позже.')
    const end = screen.getByRole('button', { name: 'Завершить акцию' })
    expect(end).toBeEnabled()
    await userEvent.setup().click(end)
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    await screen.findByText('Завершение в магазине подтверждено.')
    expect(observed.http!.requests.filter(r => r.method === 'post')).toHaveLength(1)
  })
  it('refetches on window focus with no polling', async () => {
    const observed = panel(syncFixture({ status: 'disabled' }))
    await screen.findByText('Отправка в магазин выключена.')
    act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
    await waitFor(() => expect(observed.http.requests).toHaveLength(2))
    focusManager.setFocused(undefined)
  })
  it('reads after confirmed Publish/End, while an uncertain write stays unresolved despite confirmed sync', async () => {
    let published = false
    const observed = renderCampaign('/campaigns/1', r => {
      const productReply = productsReply(r.url)
      if (productReply) return productReply
      if (r.url.endsWith('/k1-sync')) return { status: 200, data: syncFixture({ campaign_state: published ? 'published' : 'draft', status: published ? 'confirmed' : 'not_published' }) }
      if (r.method === 'post') {
        if (published) return refusal(503, 'campaign_result_unknown')
        published = true
        return { status: 200, data: campaign({ state: 'published', updated_at: '2026-10-02T01:00:00Z' }) }
      }
      return { status: 200, data: campaign({ state: published ? 'published' : 'draft', updated_at: published ? '2026-10-02T01:00:00Z' : campaign().updated_at }) }
    })
    close = observed.close
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Опубликовать' }))
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    await screen.findByText(/Доставка подтверждена/)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Завершить акцию' }))
    await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Подтвердить' }))
    await waitFor(() => expect(observed.controller.getSnapshot().status).toBe('unknown'))
    await userEvent.setup().click(screen.getByRole('button', { name: 'Обновить синхронизацию' }))
    await screen.findByText(/Доставка подтверждена/)
    expect(observed.controller.getSnapshot().status).toBe('unknown')
    expect(observed.http!.requests.filter(r => r.method === 'post')).toHaveLength(2)
  })
})
