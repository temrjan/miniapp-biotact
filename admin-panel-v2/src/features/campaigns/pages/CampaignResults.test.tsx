import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { useAuthStore } from '@/features/auth/stores/authStore'
import { CampaignRecordPage } from './CampaignRecordPage'
import { actor, product, renderCampaign } from '../test/render'
import { campaign, campaignPage } from '../test/fixtures'
import { resultFixture, resultTotals } from '../test/resultsFixture'
import { deferred, refusal } from '../test/http'
import type { Reply } from '../test/http'
import { syncFixture } from '../test/syncFixture'

let close: (() => void) | undefined
const record = campaign({ state: 'published', product_id: 39, published_at: '2026-10-01T00:00:00Z' })
function page(results: () => Reply | Promise<Reply>) {
  const view = renderCampaign('/campaigns/1', r => {
    if (r.url.endsWith('/results')) return results()
    if (r.url.endsWith('/k1-sync')) return { status: 200, data: syncFixture() }
    if (r.url.startsWith('/products')) return { status: 200, data: product(39) }
    if (r.url === '/admin/campaigns') return { status: 200, data: campaignPage([record]) }
    return { status: 200, data: record }
  }); close = view.close; return view
}
const success = (data: unknown = resultFixture()): Reply => ({ status: 200, data, headers: { 'cache-control': 'no-store' } })
const results = () => within(screen.getByRole('region', { name: 'Результаты' }))
describe('Mounted administrator Results journey', () => {
  beforeEach(() => { sessionStorage.clear(); actor(); vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { close?.(); close = undefined; vi.restoreAllMocks(); useAuthStore.getState().clearAuth() })
  it('shows exact rows, disclosure/time, clears stale numbers while refreshing and after failure', async () => {
    const delayed = deferred<Reply>(); let reads = 0
    const view = page(() => ++reads === 1 ? success() : reads === 2 ? delayed.promise : refusal(503, 'service_unavailable'))
    await screen.findByText('Employee A')
    expect(results().getByRole('row', { name: 'Employee A 3 1 2' })).toBeVisible()
    expect(results().getByRole('row', { name: 'Employee C 0 0 0' })).toBeVisible()
    expect(results().getByRole('row', { name: 'Всего по акции 5 1 4' })).toBeVisible()
    expect(results().getByText(/Прочитано: 2026-10-05 17:00:00 · Ташкент · UTC\+05:00/)).toBeVisible()
    expect(results().getByText(/Данные получены порталом/)).toBeVisible()
    await userEvent.setup().click(results().getByRole('button', { name: 'Обновить результаты' }))
    expect(results().queryByRole('table')).not.toBeInTheDocument()
    expect(results().queryByText(/Прочитано:/)).not.toBeInTheDocument()
    expect(results().getByRole('button')).toBeDisabled()
    const changed = resultFixture(); changed.totals = resultTotals('5','3','2'); const a = changed.items[0]
    if (!a) throw new Error('Fixture missing'); a.totals = resultTotals('3','3','0')
    await act(async () => { delayed.resolve(success(changed)) })
    expect(await results().findByRole('row', { name: 'Всего по акции 5 3 2' })).toBeVisible()
    await userEvent.setup().click(results().getByRole('button'))
    expect(await results().findByRole('alert')).toHaveTextContent('Не удалось получить результаты. Повторите позже.')
    expect(results().queryByRole('table')).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Завершить акцию' })).toBeEnabled()
    expect(view.controller.getSnapshot().status).toBe('idle')
  })
  it.each(['preparing', 'empty', 'incomplete', 'inconsistent', 'unavailable', 'big'] as const)('shows honest %s', async state => {
    const value = resultFixture()
    if (state === 'preparing') { value.state = state; value.items = []; value.totals = null }
    else if (state === 'empty') { value.items = []; value.totals = resultTotals('0','0','0') }
    else if (state === 'big') {
      value.items = value.items.slice(0, 1); const a = value.items[0]; if (!a) throw new Error('Fixture missing')
      a.totals = resultTotals('9007199254740993','1','9007199254740992'); value.totals = a.totals
    } else {
      value.state = state; value.totals = null; const a = value.items[0]; if (!a) throw new Error('Fixture missing'); a.state = state; a.totals = null
    }
    page(() => success(value))
    await waitFor(() => expect(results().queryByText('Загрузка результатов…')).not.toBeInTheDocument())
    if (state === 'preparing') expect(results().getByRole('status')).toHaveTextContent('Результаты ещё не подготовлены.')
    else if (state === 'empty') expect(results().getByRole('status')).toHaveTextContent('Участников этой акции пока нет.')
    else if (state === 'big') expect(results().getAllByText('9007199254740993')).toHaveLength(2)
    else { expect(results().getByText('Общий итог недоступен: не все строки проверены.')).toBeVisible(); expect(results().getAllByText('—')).toHaveLength(3); expect(results().queryByText('Всего по акции')).not.toBeInTheDocument() }
  })
  it.each(['no-store', 'malformed'])('hides %s response', async failure => {
    page(() => failure === 'no-store' ? { status: 200, data: resultFixture() } : success({ ...resultFixture(), totals: null }))
    expect(await screen.findByText('Не удалось получить результаты. Повторите позже.')).toBeVisible()
    expect(results().queryByRole('table')).not.toBeInTheDocument()
  })
  it.each([401, 403])('retires %i generation through same-auth remount and fresh authorization', async status => {
    let denied = false
    const view = page(() => denied ? refusal(status, status === 401 ? 'invalid_admin_auth' : 'admin_forbidden') : success())
    await screen.findByText('Employee A'); denied = true
    await userEvent.setup().click(results().getByRole('button'))
    await waitFor(() => expect(view.controller.getSnapshot().status).toBe('accessDenied'))
    const session = view.controller.getSnapshot().session
    const remount = <QueryClientProvider client={view.client}><MemoryRouter initialEntries={['/campaigns/1']}><Routes><Route path="/campaigns/:id" element={<CampaignRecordPage />} /></Routes></MemoryRouter></QueryClientProvider>
    view.rerender(remount)
    expect(screen.queryByText('Employee A')).not.toBeInTheDocument()
    expect(view.controller.getSnapshot().session).toBe(session)
    denied = false
    await act(async () => { await view.controller.authorize() })
    expect(await screen.findByText('Employee A')).toBeVisible()
    expect(view.controller.getSnapshot().session).toBeGreaterThan(session)
  })
})
