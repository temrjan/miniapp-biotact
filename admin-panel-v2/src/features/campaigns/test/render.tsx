import { render, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { PrivateRoute } from '@/app/routes/PrivateRoute'
import { useAuthStore } from '@/features/auth/stores/authStore'
import { CampaignCreatePage } from '../pages/CampaignCreatePage'
import { CampaignRecordPage } from '../pages/CampaignRecordPage'
import { CampaignsListPage } from '../pages/CampaignsListPage'
import { campaignController } from '../hooks/useCampaignActions'
import { installHttp } from './http'
import type { CapturedRequest, Reply } from './http'

export function renderCampaign(path = '/campaigns/new', handler?: (request: CapturedRequest) => Reply | Promise<Reply>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const http = handler ? installHttp(handler) : null
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/login" element={<p>Login destination</p>} />
    <Route path="/campaigns" element={<PrivateRoute><CampaignsListPage /></PrivateRoute>} />
    <Route path="/campaigns/new" element={<PrivateRoute><CampaignCreatePage /></PrivateRoute>} />
    <Route path="/campaigns/:id" element={<PrivateRoute><CampaignRecordPage /></PrivateRoute>} />
  </Routes></MemoryRouter></QueryClientProvider>)
  return { client, http, controller: campaignController(client), ...view, close: () => { cleanup(); campaignController(client).dispose(); client.clear(); http?.restore() } }
}
export function actor(id = 1, token = 'synthetic-session') {
  useAuthStore.getState().setAuth({ id, telegram_id: id, created_at: '2026-01-01T00:00:00Z' }, token)
}
export const product = (id = 1) => ({ id, name: `Synthetic product ${id}`, is_active: true, is_in_stock: true })
