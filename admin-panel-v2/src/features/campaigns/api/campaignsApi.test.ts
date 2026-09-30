import { afterEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import { apiClient } from '@/shared/api/client'
import { campaignsApi } from './campaignsApi'
import { campaign, campaignPage } from '../test/fixtures'
import { ERROR_STATUS } from '../model/campaignContract'

describe('Fixed campaign HTTP operations', () => {
  afterEach(() => vi.restoreAllMocks())
  it('should use fixed read and write paths and validate exact statuses and versions', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ status: 200, data: campaignPage() }).mockResolvedValueOnce({ status: 200, data: campaign() })
    const newer = campaign({ updated_at: '2026-10-01T00:00:00.123457Z' })
    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({ status: 201, data: campaign() })
      .mockResolvedValueOnce({ status: 200, data: { ...newer, state: 'published' } })
      .mockResolvedValueOnce({ status: 200, data: { ...newer, state: 'ended' } })
    vi.spyOn(apiClient, 'patch').mockResolvedValue({ status: 200, data: newer })
    const signal = new AbortController().signal
    await campaignsApi.list(1, 'draft', signal); await campaignsApi.get(1, signal)
    await campaignsApi.create({ name: 'x' })
    await campaignsApi.patch(1, { expected_updated_at: campaign().updated_at, image_url: null })
    await campaignsApi.publish(1, campaign().updated_at); await campaignsApi.end(1, campaign().updated_at)
    expect(apiClient.get).toHaveBeenCalledWith('/admin/campaigns', { params: { page: 1, size: 20, state: 'draft' }, signal })
    expect(apiClient.patch).toHaveBeenCalledWith('/admin/campaigns/1', { expected_updated_at: campaign().updated_at, image_url: null })
    expect(apiClient.post).toHaveBeenLastCalledWith('/admin/campaigns/1/end', { expected_updated_at: campaign().updated_at })
  })
  it.each(Object.entries(ERROR_STATUS))('should classify code %s using the write-specific unknown policy', async (code, status) => {
    const error = new AxiosError('PRIVATE', 'ERR_BAD_RESPONSE')
    error.response = { status, data: { detail: { code, message: 'PRIVATE' } }, statusText: '', headers: {}, config: { headers: new AxiosHeaders() } }
    vi.spyOn(apiClient, 'post').mockRejectedValue(error)
    await expect(campaignsApi.create({ name: 'x' })).rejects.toMatchObject({ code, unknownResult: ['service_unavailable', 'campaign_result_unknown'].includes(code) })
  })
  it('should classify transport failure, malformed success, wrong status and non-advancing versions as unknown', async () => {
    const post = vi.spyOn(apiClient, 'post').mockRejectedValueOnce(new Error('PRIVATE'))
      .mockResolvedValueOnce({ status: 200, data: campaign() }).mockResolvedValueOnce({ status: 201, data: {} })
    for (let i = 0; i < 3; i++) await expect(campaignsApi.create({ name: 'x' })).rejects.toMatchObject({ unknownResult: true })
    expect(post).toHaveBeenCalledTimes(3)
    vi.spyOn(apiClient, 'patch').mockResolvedValue({ status: 200, data: campaign() })
    await expect(campaignsApi.patch(1, { expected_updated_at: campaign().updated_at, name: 'x' })).rejects.toMatchObject({ unknownResult: true })
    vi.spyOn(apiClient, 'get').mockResolvedValue({ status: 200, data: campaignPage() })
    await expect(campaignsApi.get(1)).rejects.toMatchObject({ unknownResult: false })
  })
  it.each(['ECONNABORTED', 'ERR_CANCELED', 'ERR_NETWORK'])('should keep dispatched timeout/cancellation/network failure %s unknown', async code => {
    vi.spyOn(apiClient, 'post').mockRejectedValue(new AxiosError('Synthetic transport fault', code))
    await expect(campaignsApi.publish(1, campaign().updated_at)).rejects.toMatchObject({ code: null, unknownResult: true })
  })
})
