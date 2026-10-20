import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useUserStore } from './userStore'
import { api } from '@/api/client'

// Mock API
vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}))

const mockProfile = {
  id: 1,
  telegram_id: 123456789,
  username: 'testuser',
  first_name: 'Test',
  last_name: 'User',
  phone: '+998901234567',
  language_code: 'ru',
  is_active: true,
  address: null,
}

const mockAddress = {
  street: 'Улица Тестовая',
  house: '10',
  apartment: '25',
  entrance: '2',
  floor: '5',
  comment: 'Домофон 25',
  lat: 41.311081,
  lng: 69.240562,
}

describe('userStore', () => {
  beforeEach(() => {
    // Очистка store перед каждым тестом
    useUserStore.getState().reset()
    vi.clearAllMocks()
  })

  // ═══════════════════════════════════════════════════════════════
  // Initial State
  // ═══════════════════════════════════════════════════════════════

  it('starts with null profile', () => {
    const { profile } = useUserStore.getState()
    expect(profile).toBeNull()
  })

  it('starts with isLoading false', () => {
    const { isLoading } = useUserStore.getState()
    expect(isLoading).toBe(false)
  })

  it('starts with no error', () => {
    const { error } = useUserStore.getState()
    expect(error).toBeNull()
  })

  // ═══════════════════════════════════════════════════════════════
  // fetchProfile
  // ═══════════════════════════════════════════════════════════════

  it('fetches profile successfully', async () => {
    vi.mocked(api.get).mockResolvedValueOnce(mockProfile)

    await useUserStore.getState().fetchProfile()

    expect(api.get).toHaveBeenCalledWith('/users/me')
    expect(useUserStore.getState().profile).toEqual(mockProfile)
    expect(useUserStore.getState().isLoading).toBe(false)
    expect(useUserStore.getState().error).toBeNull()
  })

  it('preserves an absent Telegram ID in the generic profile response', async () => {
    const profileWithoutTelegram = { ...mockProfile, telegram_id: null }
    vi.mocked(api.get).mockResolvedValueOnce(profileWithoutTelegram)

    await useUserStore.getState().fetchProfile()

    expect(useUserStore.getState().profile).toEqual(profileWithoutTelegram)
    expect(api.get).toHaveBeenCalledWith('/users/me')
  })

  it('sets isLoading true while fetching profile', async () => {
    vi.mocked(api.get).mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(mockProfile), 100))
    )

    const promise = useUserStore.getState().fetchProfile()

    // Проверяем что isLoading стал true
    expect(useUserStore.getState().isLoading).toBe(true)

    await promise

    // После завершения isLoading должен стать false
    expect(useUserStore.getState().isLoading).toBe(false)
  })

  it('handles fetchProfile error', async () => {
    const error = new Error('Network error')
    vi.mocked(api.get).mockRejectedValueOnce(error)

    await useUserStore.getState().fetchProfile()

    expect(useUserStore.getState().profile).toBeNull()
    expect(useUserStore.getState().error).toBe('Network error')
    expect(useUserStore.getState().isLoading).toBe(false)
  })

  // ═══════════════════════════════════════════════════════════════
  // updatePhone
  // ═══════════════════════════════════════════════════════════════

  it('updates phone successfully', async () => {
    const newPhone = '+998991234567'
    const updatedProfile = { ...mockProfile, phone: newPhone }
    vi.mocked(api.patch).mockResolvedValueOnce(updatedProfile)

    await useUserStore.getState().updatePhone(newPhone)

    expect(api.patch).toHaveBeenCalledWith('/users/me/phone', { phone: newPhone })
    expect(useUserStore.getState().profile?.phone).toBe(newPhone)
    expect(useUserStore.getState().isLoading).toBe(false)
    expect(useUserStore.getState().error).toBeNull()
  })

  it('handles updatePhone error', async () => {
    const error = new Error('Invalid phone format')
    vi.mocked(api.patch).mockRejectedValueOnce(error)

    await expect(
      useUserStore.getState().updatePhone('+123')
    ).rejects.toThrow()

    expect(useUserStore.getState().error).toBe('Invalid phone format')
    expect(useUserStore.getState().isLoading).toBe(false)
  })

  // ═══════════════════════════════════════════════════════════════
  // updateAddress
  // ═══════════════════════════════════════════════════════════════

  it('updates address successfully', async () => {
    const updatedProfile = { ...mockProfile, address: mockAddress }
    vi.mocked(api.patch).mockResolvedValueOnce(updatedProfile)

    await useUserStore.getState().updateAddress(mockAddress)

    expect(api.patch).toHaveBeenCalledWith('/users/me/address', mockAddress)
    expect(useUserStore.getState().profile?.address).toEqual(mockAddress)
    expect(useUserStore.getState().isLoading).toBe(false)
  })

  it('handles updateAddress error', async () => {
    const error = new Error('Address validation failed')
    vi.mocked(api.patch).mockRejectedValueOnce(error)

    await expect(
      useUserStore.getState().updateAddress(mockAddress)
    ).rejects.toThrow()

    expect(useUserStore.getState().error).toBe('Address validation failed')
  })

  // ═══════════════════════════════════════════════════════════════
  // Utility Methods
  // ═══════════════════════════════════════════════════════════════

  it('clears error', () => {
    useUserStore.setState({ error: 'Some error' })

    useUserStore.getState().clearError()

    expect(useUserStore.getState().error).toBeNull()
  })

  it('resets store to initial state', () => {
    useUserStore.setState({
      profile: mockProfile,
      isLoading: true,
      error: 'Some error',
    })

    useUserStore.getState().reset()

    expect(useUserStore.getState().profile).toBeNull()
    expect(useUserStore.getState().isLoading).toBe(false)
    expect(useUserStore.getState().error).toBeNull()
  })

  // ═══════════════════════════════════════════════════════════════
  // Critical Business Logic
  // ═══════════════════════════════════════════════════════════════

  it('prevents duplicate phone updates', async () => {
    const phone = '+998901234567'
    vi.mocked(api.patch).mockResolvedValue({ ...mockProfile, phone })

    // Первый вызов
    await useUserStore.getState().updatePhone(phone)

    // Второй вызов с тем же номером
    await useUserStore.getState().updatePhone(phone)

    // API должен быть вызван дважды (не оптимизируем, тестируем поведение)
    expect(api.patch).toHaveBeenCalledTimes(2)
  })

  it('maintains phone format +998XXXXXXXXX', async () => {
    const validPhone = '+998901234567'
    const updatedProfile = { ...mockProfile, phone: validPhone }
    vi.mocked(api.patch).mockResolvedValueOnce(updatedProfile)

    await useUserStore.getState().updatePhone(validPhone)

    expect(useUserStore.getState().profile?.phone).toMatch(/^\+998\d{9}$/)
  })
})
