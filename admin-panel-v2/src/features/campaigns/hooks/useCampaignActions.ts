import { useSyncExternalStore } from 'react'
import { MutationObserver, onlineManager, QueryClient, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/features/auth/stores/authStore'
import { CampaignApiError, campaignsApi } from '../api/campaignsApi'
import { clearAttempt, idleState, readAttempt, storeAttempt, writesBlocked } from '../model/campaignRecovery'
import type { Attempt, RecoveryState } from '../model/campaignRecovery'
import type { CampaignRecord, DraftInput, EditInput } from '../types/campaign.types'

type WriteIntent = { kind: 'create'; input: DraftInput } |
  { kind: 'patch'; saved: CampaignRecord; input: EditInput } |
  { kind: 'publish' | 'end'; saved: CampaignRecord }

interface ControllerState extends RecoveryState { session: number }
const controllers = new WeakMap<QueryClient, CampaignController>()

export class CampaignController {
  private actorId: number | null = null
  private token: string | null = null
  private state: ControllerState = { ...idleState(), session: 0 }
  private listeners = new Set<() => void>()
  private disposeAuth: () => void
  private client: QueryClient
  constructor(client: QueryClient) {
    this.client = client
    this.syncActor()
    this.disposeAuth = useAuthStore.subscribe(() => this.syncActor())
  }
  dispose = () => { this.disposeAuth(); this.listeners.clear() }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  getSnapshot = () => this.state
  getActorId = () => this.actorId
  private update(state: RecoveryState) {
    this.state = { ...state, session: this.state.session }
    this.listeners.forEach(listener => listener())
  }
  private syncActor() {
    const auth = useAuthStore.getState()
    const actorId = auth.token ? auth.user?.id ?? null : null
    if (actorId === this.actorId && auth.token === this.token) return
    const previous = this.actorId
    this.actorId = actorId
    this.token = auth.token
    if (previous !== null) {
      void this.client.cancelQueries({ queryKey: ['campaigns', previous] })
      this.client.removeQueries({ queryKey: ['campaigns', previous] })
    }
    this.state = { ...idleState(), session: this.state.session + 1 }
    if (actorId !== null) {
      try {
        const attempt = readAttempt(sessionStorage, actorId)
        if (attempt) this.state = { ...this.state, status: 'unknown', attempt, message: 'Результат действия не подтверждён.' }
      } catch {
        this.state = { ...this.state, status: 'definiteError', message: 'Хранилище вкладки недоступно. Отправка заблокирована.' }
      }
    }
    this.listeners.forEach(listener => listener())
  }
  isCurrent = (session: number) => session === this.state.session && this.actorId !== null && this.state.status !== 'accessDenied'
  private deny() {
    this.state = { ...this.state, session: this.state.session + 1 }
    this.update({ ...idleState(), status: 'accessDenied', attempt: this.state.attempt, message: 'Доступ к акциям запрещён. Проверьте полномочия заново.' })
    if (this.actorId !== null) {
      void this.client.cancelQueries({ queryKey: ['campaigns', this.actorId] })
      this.client.removeQueries({ queryKey: ['campaigns', this.actorId] })
    }
  }
  async read<T>(send: () => Promise<T>): Promise<T> {
    const session = this.state.session
    if (!this.isCurrent(session)) throw new CampaignApiError(null, false)
    try {
      const value = await send()
      if (!this.isCurrent(session)) throw new CampaignApiError(null, false)
      return value
    } catch (error: unknown) {
      if (this.isCurrent(session) && error instanceof CampaignApiError && (error.code === 'admin_forbidden' || error.code === 'invalid_admin_auth')) this.deny()
      throw error
    }
  }
  async authorize(): Promise<void> {
    const session = this.state.session
    if (this.actorId === null) return
    try {
      await campaignsApi.list(1)
      if (session !== this.state.session || this.actorId === null) return
      this.state = { ...this.state, session: session + 1 }
      void this.client.cancelQueries({ queryKey: ['campaigns', this.actorId] })
      this.client.removeQueries({ queryKey: ['campaigns', this.actorId] })
      this.update({ ...idleState(), attempt: this.state.attempt, status: this.state.attempt ? 'unknown' : 'idle' })
    } catch (error: unknown) {
      if (session === this.state.session && error instanceof CampaignApiError && (error.code === 'admin_forbidden' || error.code === 'invalid_admin_auth')) this.deny()
    }
  }
  async execute(intent: WriteIntent): Promise<CampaignRecord | null> {
    this.syncActor()
    const actorId = this.actorId, session = this.state.session
    if (actorId === null || writesBlocked(this.state)) return null
    const missing = this.state.attempt
    if (intent.kind !== 'create' && this.state.errorCode === 'campaign_not_found' && missing && missing !== 'corrupt' && missing.campaignId === intent.saved.id) return null
    const offline = () => !navigator.onLine || !onlineManager.isOnline()
    if (offline()) {
      this.update({ ...idleState(), status: 'definiteError', message: 'Нет соединения. Действие не отправлено.' })
      return null
    }
    if (intent.kind !== 'create' &&
        (intent.saved.state !== (intent.kind === 'end' ? 'published' : 'draft') ||
         (intent.kind === 'patch' && intent.input.expected_updated_at !== intent.saved.updated_at))) return null
    const attempt: Attempt = {
      schema: 1, actorId, attemptId: crypto.randomUUID(), kind: intent.kind,
      campaignId: intent.kind === 'create' ? null : intent.saved.id,
      baseVersion: intent.kind === 'create' ? null : intent.saved.updated_at,
      sentAt: new Date().toISOString(), createName: intent.kind === 'create' ? intent.input.name : null,
    }
    try { storeAttempt(sessionStorage, attempt) } catch {
      this.update({ ...idleState(), status: 'unknown', attempt: 'corrupt', message: 'Не удалось сохранить отметку. Действие не отправлено; проверьте список перед продолжением.' })
      return null
    }
    // Acquired synchronously before Query's first await, shared by every campaign page.
    this.update({ ...idleState(), status: 'pending', attempt })
    let dispatched = false
    const mutation = new MutationObserver<CampaignRecord, unknown, void>(this.client, {
      retry: false, networkMode: 'always', gcTime: 0,
      mutationFn: () => {
        if (!this.isCurrent(session) || offline()) throw new Error('Campaign preflight refused')
        dispatched = true
        switch (intent.kind) {
          case 'create': return campaignsApi.create(intent.input)
          case 'patch': return campaignsApi.patch(intent.saved.id, intent.input)
          case 'publish': return campaignsApi.publish(intent.saved.id, intent.saved.updated_at)
          case 'end': return campaignsApi.end(intent.saved.id, intent.saved.updated_at)
        }
      },
    })
    try {
      const record = await mutation.mutate()
      if (!this.isCurrent(session)) return null
      if (!clearAttempt(sessionStorage, actorId, attempt)) throw new Error('Attempt changed')
      this.update(idleState())
      void this.client.invalidateQueries({ queryKey: ['campaigns', actorId] })
      return record
    } catch (error: unknown) {
      if (!this.isCurrent(session)) return null
      const definite = !dispatched || error instanceof CampaignApiError && !error.unknownResult
      if (definite) {
        try { if (!clearAttempt(sessionStorage, actorId, attempt)) throw new Error('Attempt changed') } catch {
          this.update({ ...idleState(), status: 'unknown', attempt, message: 'Результат действия не подтверждён. Отметка сохранена.' })
          return null
        }
        if (error instanceof CampaignApiError && (error.code === 'admin_forbidden' || error.code === 'invalid_admin_auth')) { this.deny(); return null }
        const conflict = error instanceof CampaignApiError && ['campaign_changed', 'invalid_campaign_state', 'immutable_campaign'].includes(error.code ?? '')
        this.update({ ...idleState(), status: conflict ? 'conflict' : 'definiteError', attempt: conflict || error instanceof CampaignApiError && error.code === 'campaign_not_found' ? attempt : null, conflict,
          errorCode: error instanceof CampaignApiError ? error.code : null,
          message: error instanceof CampaignApiError ? error.message : 'Действие не отправлено. Проверьте соединение.' })
      } else {
        this.update({ ...idleState(), status: 'unknown', attempt, message: 'Результат действия не подтверждён.' })
      }
      return null
    } finally { mutation.reset() }
  }
  async inspect(page = 1, chosenId?: number): Promise<void> {
    if (!writesBlocked(this.state) || this.state.status === 'pending' || this.state.status === 'checking') return
    const previous = this.state, session = previous.session
    const attempt = previous.attempt
    this.update({ ...previous, status: 'checking', record: null, page: null })
    try {
      // Direct API calls deliberately bypass Query's potentially fresh presentation cache.
      const id = chosenId ?? (attempt && attempt !== 'corrupt' ? attempt.campaignId : null)
      const record = id !== null ? await this.read(() => campaignsApi.get(id)) : null
      const list = id === null ? await this.read(() => campaignsApi.list(page)) : null
      if (this.isCurrent(session)) this.update({ ...previous, status: 'awaitingDecision', record, page: list })
    } catch (error: unknown) {
      if (this.isCurrent(session) && this.state.status !== 'accessDenied') this.update({ ...previous,
        record: null, page: null, message: error instanceof CampaignApiError ? error.message : 'Проверка не удалась. Отправка остаётся заблокирована.' })
    }
  }
  resolve(): CampaignRecord | null | false {
    if (this.state.status !== 'awaitingDecision' || (!this.state.record && !this.state.page) || this.actorId === null) return false
    try {
      if (this.state.status === 'awaitingDecision' && this.state.attempt &&
          !clearAttempt(sessionStorage, this.actorId, this.state.attempt) && this.state.attempt !== 'corrupt') {
        // Conflicts have a definite refusal and their persisted marker was already cleared.
        if (readAttempt(sessionStorage, this.actorId) !== null) return false
      }
    } catch {
      this.update({ ...this.state, message: 'Не удалось закрыть отметку. Отправка остаётся заблокирована.' })
      return false
    }
    const record = this.state.record
    if (record) this.client.setQueryData(['campaigns', this.actorId, 'record', record.id], record)
    this.update(idleState())
    return record
  }
}

export function campaignController(client: QueryClient): CampaignController {
  let controller = controllers.get(client)
  if (!controller) { controller = new CampaignController(client); controllers.set(client, controller) }
  return controller
}

export function useCampaignActions() {
  const client = useQueryClient()
  const controller = campaignController(client)
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  return { controller, state, actorId: controller.getActorId(), blocked: writesBlocked(state) }
}
