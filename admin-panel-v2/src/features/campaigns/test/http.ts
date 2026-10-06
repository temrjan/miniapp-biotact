import { AxiosError } from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { apiClient } from '@/shared/api/client'

export interface CapturedRequest { method: string; url: string; body: unknown; params: Record<string, unknown> }
export interface Reply { status: number; data: unknown; headers?: Record<string, string> }
export function installHttp(handler: (request: CapturedRequest) => Reply | Promise<Reply>) {
  const original = apiClient.defaults.adapter
  const requests: CapturedRequest[] = []
  apiClient.defaults.adapter = async config => {
    const request: CapturedRequest = { method: config.method ?? 'get', url: config.url ?? '',
      body: typeof config.data === 'string' ? JSON.parse(config.data) as unknown : config.data as unknown,
      params: (config.params ?? {}) as Record<string, unknown> }
    requests.push(request)
    const reply = await handler(request)
    const response = { ...reply, statusText: '', headers: reply.headers ?? {}, config }
    if (reply.status >= 400) throw new AxiosError('Synthetic HTTP refusal', 'ERR_BAD_RESPONSE', config, undefined, response)
    return response
  }
  return { requests, restore: () => { if (original === undefined) delete apiClient.defaults.adapter; else apiClient.defaults.adapter = original } }
}
export function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Deferred not initialized') }
  let reject: (error: unknown) => void = () => { throw new Error('Deferred not initialized') }
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
export const refusal = (status: number, code: string): Reply => ({ status, data: { detail: { code, message: 'PRIVATE_DIAGNOSTIC' } } })
// Retain the actual adapter argument type for boundary upload tests.
export type UploadRequest = AxiosRequestConfig
