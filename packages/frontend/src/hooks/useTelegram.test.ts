import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

// Mock @twa-dev/sdk before importing hooks
vi.mock('@twa-dev/sdk', () => ({
  default: {
    initDataUnsafe: {
      user: {
        id: 123456789,
        first_name: 'Test',
        last_name: 'User',
        username: 'testuser',
        language_code: 'ru',
      },
    },
    colorScheme: 'light',
    platform: 'android',
    viewportStableHeight: 600,
    viewportHeight: 550,
    isExpanded: true,
    initData: 'mock_init_data',
    HapticFeedback: {
      impactOccurred: vi.fn(),
      notificationOccurred: vi.fn(),
      selectionChanged: vi.fn(),
    },
    onEvent: vi.fn(),
    offEvent: vi.fn(),
  },
}))

import { useHaptic, useInitData } from './useTelegram'

describe('useTelegram hooks', () => {
  describe('useHaptic', () => {
    it('returns haptic feedback functions', () => {
      const { result } = renderHook(() => useHaptic())
      expect(result.current.light).toBeTypeOf('function')
      expect(result.current.medium).toBeTypeOf('function')
      expect(result.current.heavy).toBeTypeOf('function')
      expect(result.current.success).toBeTypeOf('function')
      expect(result.current.error).toBeTypeOf('function')
      expect(result.current.selection).toBeTypeOf('function')
    })
  })

  describe('useInitData', () => {
    it('returns init data', () => {
      const { result } = renderHook(() => useInitData())
      expect(result.current).toBe('mock_init_data')
    })
  })
})
