import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { AxiosError, AxiosHeaders } from 'axios'
import type { PropsWithChildren } from 'react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { authApi } from '../api/authApi'
import { useAuthStore } from '../stores/authStore'
import { useAuth, useLogin, useLogout, useMe } from './useAuth'
import type { User } from '@/shared/types'

const navigate = vi.fn()
vi.mock('react-router-dom', async () => ({
  ...await vi.importActual<typeof import('react-router-dom')>('react-router-dom'),
  useNavigate: () => navigate,
}))

const user: User = { id: 901, telegram_id: 990901, first_name: 'Synthetic',
  is_admin: true, created_at: '2026-10-07T00:00:00Z' }
let queryClient: QueryClient
function Wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider>
}
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  useAuthStore.getState().clearAuth()
  navigate.mockClear()
})
afterEach(() => { queryClient.clear(); vi.restoreAllMocks() })

it('does not log a rejected credential-bearing Axios error through the real login hook', async () => {
  const canary = 'synthetic-console-canary'
  const failure = new AxiosError('Login refused', 'ERR_BAD_REQUEST', {
    headers: new AxiosHeaders(), data: JSON.stringify({ username: 'fictional', password: canary }),
  })
  const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  vi.spyOn(authApi, 'login').mockRejectedValue(failure)
  const { result } = renderHook(useLogin, { wrapper: Wrapper })
  await act(async () => { await result.current.mutateAsync({ username: 'fictional', password: canary }).catch(() => undefined) })
  await waitFor(() => expect(result.current.isError).toBe(true))
  expect(error).not.toHaveBeenCalled()
  expect(useAuthStore.getState().isAuthenticated()).toBe(false)
  expect(useAuthStore.getState().token).toBeNull()
  expect(navigate).not.toHaveBeenCalled()
})

it('retains successful login, auth state and navigation', async () => {
  vi.spyOn(authApi, 'login').mockResolvedValue({ user, access_token: 'synthetic-token', token_type: 'bearer' })
  const { result } = renderHook(() => ({ login: useLogin(), auth: useAuth() }), { wrapper: Wrapper })
  expect(result.current.auth.isAuthenticated).toBe(false)
  await act(async () => { await result.current.login.mutateAsync({ username: 'fictional', password: 'synthetic-password' }) })
  expect(result.current.auth.user).toEqual(user)
  expect(result.current.auth.token).toBe('synthetic-token')
  expect(result.current.auth.isAuthenticated).toBe(true)
  expect(navigate).toHaveBeenCalledWith('/dashboard')
})

it('loads me only with a token and clears auth and queries on logout', async () => {
  const me = vi.spyOn(authApi, 'me').mockResolvedValue(user)
  const { result } = renderHook(() => ({ me: useMe(), auth: useAuth(), logout: useLogout() }), { wrapper: Wrapper })
  expect(me).not.toHaveBeenCalled()
  act(() => { result.current.auth.setAuth(user, 'synthetic-token') })
  await waitFor(() => expect(result.current.me.data).toEqual(user))
  expect(me).toHaveBeenCalledTimes(1)
  await act(async () => { await result.current.logout.mutateAsync() })
  expect(useAuthStore.getState().isAuthenticated()).toBe(false)
  expect(navigate).toHaveBeenCalledWith('/login')
  expect(queryClient.getQueryData(['auth', 'me'])).toBeUndefined()
})
