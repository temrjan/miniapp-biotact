import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, onlineManager } from '@tanstack/react-query'
import { useAuthStore } from '@/features/auth/stores/authStore'
import { CampaignController } from './useCampaignActions'
import { campaign, campaignPage } from '../test/fixtures'
import { deferred, installHttp, refusal } from '../test/http'
import { attemptKey, readAttempt, storeAttempt } from '../model/campaignRecovery'
import { campaignsApi } from '../api/campaignsApi'

const controllers: CampaignController[] = []
const restores: (() => void)[] = []
function login(id = 1, token = 'synthetic-session') {
  useAuthStore.getState().setAuth({ id, telegram_id: id, created_at: '2026-01-01T00:00:00Z' }, token)
}
function setup(handler: Parameters<typeof installHttp>[0] = () => ({ status: 201, data: campaign() })) {
  const http = installHttp(handler); restores.push(http.restore)
  const client = new QueryClient()
  const controller = new CampaignController(client); controllers.push(controller)
  return { http, client, controller }
}
describe('Campaign tab controller at real HTTP and storage boundaries', () => {
  beforeEach(() => { sessionStorage.clear(); login(); onlineManager.setOnline(true); vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { controllers.splice(0).forEach(c => c.dispose()); restores.splice(0).forEach(r => r()); vi.restoreAllMocks(); onlineManager.setOnline(true); useAuthStore.getState().clearAuth() })
  it('should synchronously guard rapid submissions and persist before actual dispatch', async () => {
    const response = deferred<{ status: number; data: unknown }>()
    const { controller, http } = setup(() => {
      expect(readAttempt(sessionStorage, 1)).toMatchObject({ kind: 'create', createName: 'one' })
      return response.promise
    })
    const first = controller.execute({ kind: 'create', input: { name: 'one' } })
    expect(controller.getSnapshot().status).toBe('pending')
    await expect(controller.execute({ kind: 'create', input: { name: 'two' } })).resolves.toBeNull()
    response.resolve({ status: 201, data: campaign() })
    expect(await first).toEqual(campaign())
    expect(http.requests.filter(r => r.method === 'post')).toHaveLength(1)
    expect(readAttempt(sessionStorage, 1)).toBeNull()
  })
  it.each(['browser', 'manager'])('should send no queued write when %s is offline and later reconnects', async channel => {
    const { controller, http, client } = setup()
    if (channel === 'browser') vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    else onlineManager.setOnline(false)
    expect(await controller.execute({ kind: 'create', input: { name: 'x' } })).toBeNull()
    expect(controller.getSnapshot().message).toContain('не отправлено')
    vi.restoreAllMocks(); onlineManager.setOnline(true)
    expect(http.requests).toHaveLength(0)
    expect(client.getMutationCache().getAll()).toHaveLength(0)
    expect(await controller.execute({ kind: 'create', input: { name: 'x' } })).toEqual(campaign())
    expect(http.requests).toHaveLength(1)
  })
  it.each(['create', 'patch', 'publish', 'end'] as const)('should keep an unknown %s blocked and require fresh read plus manual reconciliation', async kind => {
    let failed = true
    const observed = campaign({ state: kind === 'end' ? 'ended' : kind === 'publish' ? 'published' : 'draft', updated_at: '2026-10-01T00:00:00.123457Z' })
    const { controller, http, client } = setup(r => r.method !== 'get' ? refusal(503, 'service_unavailable') :
      failed ? refusal(503, 'campaign_service_unavailable') : { status: 200, data: r.url.endsWith('/1') ? observed : campaignPage([observed]) })
    client.setQueryData(['campaigns', 1, 'record', 1], observed)
    const saved = campaign({ state: kind === 'end' ? 'published' : 'draft' })
    const intent = kind === 'create' ? { kind, input: { name: 'x' } } : kind === 'patch' ? { kind, saved, input: { expected_updated_at: saved.updated_at, name: 'x' } } : { kind, saved }
    await controller.execute(intent)
    expect(controller.getSnapshot().status).toBe('unknown')
    expect(controller.resolve()).toBe(false)
    await controller.inspect()
    expect(controller.getSnapshot().status).toBe('unknown')
    expect(readAttempt(sessionStorage, 1)).not.toBeNull()
    failed = false
    await controller.inspect()
    expect(controller.getSnapshot().status).toBe('awaitingDecision')
    expect(http.requests.filter(r => r.method === 'get')).toHaveLength(2)
    expect(await controller.execute(intent)).toBeNull()
    if (kind === 'create') await controller.inspect(1, 1)
    expect(controller.resolve()).toEqual(observed)
    expect(readAttempt(sessionStorage, 1)).toBeNull()
    expect(http.requests.filter(r => r.method !== 'get')).toHaveLength(1)
  })
  it.each(['campaign_changed', 'invalid_campaign_state', 'immutable_campaign', 'campaign_not_found', 'invalid_request', 'campaign_overlap', 'shop_unavailable', 'campaign_service_unavailable'])('should retain a definite refusal %s without claiming success', async code => {
    const status = code === 'campaign_not_found' ? 404 : ['invalid_request'].includes(code) ? 422 : ['shop_unavailable', 'campaign_service_unavailable'].includes(code) ? 503 : 409
    const { controller } = setup(r => r.method === 'get' ? { status: 200, data: campaign() } : refusal(status, code))
    await controller.execute({ kind: 'patch', saved: campaign(), input: { expected_updated_at: campaign().updated_at, name: 'x' } })
    expect(readAttempt(sessionStorage, 1)).toBeNull()
    expect(controller.getSnapshot().status).toBe(['campaign_changed', 'invalid_campaign_state', 'immutable_campaign'].includes(code) ? 'conflict' : 'definiteError')
    if (controller.getSnapshot().status === 'conflict') {
      await controller.inspect(); expect(controller.resolve()).toEqual(campaign())
    }
  })
  it('should block forbidden transitions, mismatched versions and logged-out dispatch', async () => {
    const { controller, http } = setup()
    await controller.execute({ kind: 'end', saved: campaign() })
    await controller.execute({ kind: 'publish', saved: campaign({ state: 'ended' }) })
    await controller.execute({ kind: 'patch', saved: campaign(), input: { expected_updated_at: 'wrong', name: 'x' } })
    useAuthStore.getState().clearAuth()
    await controller.execute({ kind: 'create', input: { name: 'x' } })
    expect(http.requests).toHaveLength(0)
  })
  it('should preserve actor A marker across logout, restore on re-login and ignore late A completion', async () => {
    const response = deferred<{ status: number; data: unknown }>()
    const entered = deferred<boolean>()
    const { controller, http } = setup(r => {
      if (r.method === 'get') return { status: 200, data: campaignPage() }
      entered.resolve(true); return response.promise
    })
    const old = controller.execute({ kind: 'create', input: { name: 'A' } })
    await entered.promise
    useAuthStore.getState().clearAuth(); login(2)
    expect(controller.getSnapshot().attempt).toBeNull()
    response.resolve({ status: 201, data: campaign() }); expect(await old).toBeNull()
    expect(readAttempt(sessionStorage, 1)).not.toBeNull()
    login(1, 'new-session')
    expect(controller.getSnapshot().status).toBe('unknown')
    await controller.inspect(); expect(controller.resolve()).toBeNull()
    expect(http.requests.filter(r => r.method === 'post')).toHaveLength(1)
  })
  it('should reject a late actor read and remove actor-owned cache on session change', async () => {
    const response = deferred<{ status: number; data: unknown }>()
    const { controller, client } = setup(() => response.promise)
    client.setQueryData(['campaigns', 1, 'record', 1], campaign())
    const read = controller.read(() => campaignsApi.get(1))
    login(2)
    response.resolve({ status: 200, data: campaign() })
    await expect(read).rejects.toThrow()
    expect(client.getQueryData(['campaigns', 1, 'record', 1])).toBeUndefined()
  })
  it.each(['admin_forbidden', 'invalid_admin_auth'])('should hide privileged state on %s and require successful fresh authorization', async code => {
    let authorized = false
    const { controller, client } = setup(() => authorized ? { status: 200, data: campaignPage() } : refusal(code === 'admin_forbidden' ? 403 : 401, code))
    client.setQueryData(['campaigns', 1, 'record', 1], campaign())
    await expect(controller.read(() => campaignsApi.list())).rejects.toThrow()
    expect(controller.getSnapshot().status).toBe('accessDenied')
    expect(client.getQueryData(['campaigns', 1, 'record', 1])).toBeUndefined()
    await controller.authorize(); expect(controller.getSnapshot().status).toBe('accessDenied')
    authorized = true; await controller.authorize(); expect(controller.getSnapshot().status).toBe('idle')
  })
  it('should block corrupt markers and storage failures without a write, requiring list inspection before resolution', async () => {
    sessionStorage.setItem(attemptKey(1), '{PRIVATE')
    const { controller, http } = setup(() => ({ status: 200, data: campaignPage() }))
    expect(controller.getSnapshot().attempt).toBe('corrupt')
    await controller.execute({ kind: 'create', input: { name: 'x' } })
    expect(http.requests).toHaveLength(0)
    await controller.inspect(); expect(controller.resolve()).toBeNull()
    const quota = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('quota') })
    await controller.execute({ kind: 'create', input: { name: 'x' } })
    expect(http.requests.filter(r => r.method !== 'get')).toHaveLength(0)
    expect(controller.getSnapshot().status).toBe('unknown')
    quota.mockRestore(); await controller.inspect(); expect(controller.resolve()).toBeNull()
  })
  it('should keep guard on marker-removal failure and refuse a completion that sees a newer attempt', async () => {
    const { controller } = setup()
    const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('denied') })
    await controller.execute({ kind: 'create', input: { name: 'x' } })
    expect(controller.getSnapshot().status).toBe('unknown')
    remove.mockRestore()
  })
  it('should refuse pre-dispatch connectivity loss without sending or retaining a queued mutation', async () => {
    const { controller, http } = setup()
    const write = controller.execute({ kind: 'create', input: { name: 'x' } })
    onlineManager.setOnline(false)
    await write
    expect(http.requests).toHaveLength(0)
    expect(readAttempt(sessionStorage, 1)).toBeNull()
    expect(controller.getSnapshot().status).toBe('definiteError')
    onlineManager.setOnline(true)
    expect(await controller.execute({ kind: 'create', input: { name: 'x' } })).toEqual(campaign())
    expect(http.requests).toHaveLength(1)
  })
  it.each([true, false])('should keep a newer marker when an old %s completion arrives', async success => {
    const response = deferred<{ status: number; data: unknown }>(), entered = deferred<boolean>()
    const { controller } = setup(() => { entered.resolve(true); return response.promise })
    const write = controller.execute({ kind: 'create', input: { name: 'old' } })
    await entered.promise
    const newer = { schema: 1 as const, actorId: 1, attemptId: crypto.randomUUID(), kind: 'create' as const,
      campaignId: null, baseVersion: null, sentAt: '2026-10-01T00:00:00Z', createName: 'new' }
    sessionStorage.setItem(attemptKey(1), JSON.stringify(newer))
    response.resolve(success ? { status: 201, data: campaign() } : refusal(422, 'invalid_request'))
    await write
    expect(readAttempt(sessionStorage, 1)).toEqual(newer)
    expect(controller.getSnapshot().status).toBe('unknown')
    expect(controller.resolve()).toBe(false)
  })
  it('should retain the marker when manual reconciliation cannot clear storage and when authorization returns', async () => {
    const { controller } = setup(r => r.method === 'post' ? refusal(503, 'campaign_result_unknown') : { status: 200, data: campaignPage() })
    await controller.execute({ kind: 'create', input: { name: 'x' } })
    await controller.inspect()
    const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('denied') })
    expect(controller.resolve()).toBe(false)
    expect(controller.getSnapshot().message).toContain('Отправка остаётся заблокирована')
    remove.mockRestore()
    await controller.authorize()
    expect(controller.getSnapshot().status).toBe('unknown')
    expect(readAttempt(sessionStorage, 1)).not.toBeNull()
  })
  it('should restore access-denied unknown recovery only through a fresh authorization read', async () => {
    let permitted = true
    const { controller, http } = setup(r => r.method === 'post' ? refusal(503, 'campaign_result_unknown') :
      permitted ? { status: 200, data: campaignPage() } : refusal(403, 'admin_forbidden'))
    await controller.execute({ kind: 'create', input: { name: 'x' } })
    permitted = false; await controller.inspect()
    expect(controller.getSnapshot().status).toBe('accessDenied')
    expect(readAttempt(sessionStorage, 1)).not.toBeNull()
    permitted = true; await controller.authorize()
    expect(controller.getSnapshot().status).toBe('unknown')
    await controller.inspect()
    expect(controller.resolve()).toBeNull()
    expect(http.requests.filter(r => r.method === 'post')).toHaveLength(1)
  })
  it('should block unavailable storage before HTTP and reject a delayed inspection after actor change', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('denied') })
    const { controller, http } = setup()
    expect(controller.getSnapshot().message).toContain('Хранилище вкладки недоступно')
    await controller.execute({ kind: 'create', input: { name: 'x' } })
    expect(http.requests).toHaveLength(0)
    get.mockRestore()
    storeAttempt(sessionStorage, { schema: 1, actorId: 2, attemptId: crypto.randomUUID(), kind: 'patch', campaignId: 1,
      baseVersion: campaign().updated_at, sentAt: '2026-10-01T00:00:00Z', createName: null })
    login(2)
    const response = deferred<{ status: number; data: unknown }>()
    const boundary = installHttp(() => response.promise); restores.push(boundary.restore)
    const inspection = controller.inspect()
    login(3)
    response.resolve({ status: 200, data: campaign() }); await inspection
    expect(controller.getSnapshot().record).toBeNull()
    expect(controller.getSnapshot().attempt).toBeNull()
    expect(readAttempt(sessionStorage, 2)).not.toBeNull()
  })
})
