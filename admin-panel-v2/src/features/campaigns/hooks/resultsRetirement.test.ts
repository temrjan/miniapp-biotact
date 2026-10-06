import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import { CampaignController } from './useCampaignActions'
import { campaignsApi } from '../api/campaignsApi'
import { actor } from '../test/render'
import { installHttp, deferred, refusal } from '../test/http'
import type { Reply } from '../test/http'
import { campaignPage } from '../test/fixtures'
import { resultFixture } from '../test/resultsFixture'
import { useAuthStore } from '@/features/auth/stores/authStore'

let close: (() => void) | undefined
beforeEach(() => { sessionStorage.clear(); actor(); vi.spyOn(console, 'error').mockImplementation(() => {}) })
afterEach(() => { close?.(); vi.restoreAllMocks(); useAuthStore.getState().clearAuth() })
it('rejects an old result after denial and later authorized re-entry', async () => {
  const delayed = deferred<Reply>(), entered = deferred<boolean>()
  const http = installHttp(r => {
    if (r.url.endsWith('/results')) { entered.resolve(true); return delayed.promise }
    if (r.url.endsWith('/999')) return refusal(403, 'admin_forbidden')
    return { status: 200, data: campaignPage() }
  })
  const client = new QueryClient(), controller = new CampaignController(client)
  close = () => { controller.dispose(); client.clear(); http.restore() }
  const old = controller.read(() => campaignsApi.results(1))
  await entered.promise
  await expect(controller.read(() => campaignsApi.get(999))).rejects.toThrow()
  expect(controller.getSnapshot().status).toBe('accessDenied')
  await controller.authorize()
  expect(controller.getSnapshot().status).toBe('idle')
  delayed.resolve({ status: 200, data: resultFixture(), headers: { 'cache-control': 'no-store' } })
  await expect(old).rejects.toThrow()
})
