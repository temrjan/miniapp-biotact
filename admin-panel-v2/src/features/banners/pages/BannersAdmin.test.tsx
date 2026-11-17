import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/shared/api/client'
import { bannersApi } from '../api/bannersApi'
import { bannerMediaProbe } from '../model/bannerMedia'
import type { Banner, BannerUpdateData } from '../types/banner.types'
import { BannersListPage } from './BannersListPage'

const clients: QueryClient[] = []

function mountPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  clients.push(client)
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BannersListPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return userEvent.setup()
}

function makeBanner(overrides: Partial<Banner> = {}): Banner {
  return {
    id: 1,
    title: 'Баннер 1',
    title_uz: null,
    subtitle: null,
    subtitle_uz: null,
    image_url: 'https://app.biotact.uz/uploads/banners/wide-1.jpg',
    image_mobile_url: null,
    video_url: null,
    video_mobile_url: null,
    gradient: null,
    link_url: '/catalog',
    sort_order: 0,
    is_active: true,
    created_at: '2026-11-16T00:00:00Z',
    updated_at: '2026-11-16T00:00:00Z',
    ...overrides,
  }
}

function mockGetRoutes(getItems: () => unknown[]) {
  return vi.spyOn(apiClient, 'get').mockImplementation(async (endpoint) => {
    if (endpoint === '/banners') {
      const items = getItems()
      return { data: { items, total: items.length } }
    }
    if (endpoint === '/settings/banner_interval') {
      return { data: { id: 1, key: 'banner_interval', value: '4000', updated_at: '2026-11-16T00:00:00Z' } }
    }
    throw new Error(`Unexpected GET ${endpoint}`)
  })
}

function sizedMp4(name: string, bytes: number): File {
  const file = new File(['mp4-bytes'], name, { type: 'video/mp4' })
  Object.defineProperty(file, 'size', { value: bytes })
  return file
}

describe('PR-2 Banner Admin', () => {
  beforeEach(() => {
    vi.spyOn(bannerMediaProbe, 'readImageDimensions').mockResolvedValue({ width: 1920, height: 640 })
    vi.spyOn(bannerMediaProbe, 'readVideoMetadata').mockResolvedValue({ width: 1920, height: 640, duration: 6 })
  })

  afterEach(() => {
    cleanup()
    clients.splice(0).forEach((client) => client.clear())
    vi.restoreAllMocks()
  })

  it('A-04: bannersApi.getAll accepts three string media fields and keeps absent keys unnormalized', async () => {
    const fullBanner = makeBanner({
      id: 1,
      image_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-1.jpg',
      video_url: 'https://app.biotact.uz/uploads/banners/wide-1.mp4',
      video_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-1.mp4',
    })
    const legacyBanner = {
      id: 2,
      title: 'Legacy',
      is_active: true,
      sort_order: 1,
      created_at: '2026-11-16T00:00:00Z',
      updated_at: '2026-11-16T00:00:00Z',
    }
    mockGetRoutes(() => [fullBanner, legacyBanner])

    const result = await bannersApi.getAll()

    expect(result.items).toHaveLength(2)
    expect(result.items[0]).toMatchObject({
      image_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-1.jpg',
      video_url: 'https://app.biotact.uz/uploads/banners/wide-1.mp4',
      video_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-1.mp4',
    })
    const second = result.items[1]
    expect(second).toBeDefined()
    expect('image_mobile_url' in second!).toBe(false)
    expect('video_url' in second!).toBe(false)
    expect('video_mobile_url' in second!).toBe(false)
  })

  it.each(['video_url', 'image_mobile_url', 'video_mobile_url'] as const)(
    'A-04: bannersApi.getAll rejects list read when %s is a number',
    async (field) => {
      mockGetRoutes(() => [{ ...makeBanner(), [field]: 123 }])

      await expect(bannersApi.getAll()).rejects.toThrow('Invalid banner')
    }
  )

  it('A-02 (user path 1): refuses a clip larger than 5 MiB without sending any request', async () => {
    const items = [makeBanner({ id: 10, title: 'Акция' })]
    mockGetRoutes(() => items)
    const postSpy = vi.spyOn(apiClient, 'post')
    const patchSpy = vi.spyOn(apiClient, 'patch')
    const user = mountPage()

    await user.click(await screen.findByRole('button', { name: 'Редактировать' }))
    const clipInput = screen.getByLabelText('Файл широкого ролика 3:1')
    await user.upload(clipInput, sizedMp4('huge.mp4', 5 * 1024 * 1024 + 1))

    expect(await screen.findByRole('alert')).toHaveTextContent('5 МБ')
    expect(postSpy).not.toHaveBeenCalled()
    expect(patchSpy).not.toHaveBeenCalled()
  })

  it('A-02 (user path 1): refuses a clip longer than 10 s (12 s) without sending any request', async () => {
    const items = [makeBanner({ id: 10, title: 'Акция' })]
    mockGetRoutes(() => items)
    const postSpy = vi.spyOn(apiClient, 'post')
    const patchSpy = vi.spyOn(apiClient, 'patch')
    vi.mocked(bannerMediaProbe.readVideoMetadata).mockResolvedValueOnce({
      width: 1920,
      height: 640,
      duration: 12,
    })
    const user = mountPage()

    await user.click(await screen.findByRole('button', { name: 'Редактировать' }))
    await user.upload(screen.getByLabelText('Файл широкого ролика 3:1'), sizedMp4('long.mp4', 1000))

    expect(await screen.findByRole('alert')).toHaveTextContent('10 с')
    expect(postSpy).not.toHaveBeenCalled()
    expect(patchSpy).not.toHaveBeenCalled()
  })

  it('A-03, A-01 (user path 1): handles 422 video_banner_text_required refusal, then uploads clip and saves texts in one PATCH', async () => {
    let stored = makeBanner({ id: 10, title: 'Весна' })
    mockGetRoutes(() => [stored])
    const uploadedClipUrl = 'https://app.biotact.uz/uploads/banners/wide-clip.mp4'
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: { url: uploadedClipUrl, filename: 'wide-clip.mp4' },
    })
    const patchSpy = vi
      .spyOn(apiClient, 'patch')
      .mockRejectedValueOnce({
        isAxiosError: true,
        response: {
          status: 422,
          data: { detail: { code: 'video_banner_text_required', fields: ['title_uz'] } },
        },
      })
      .mockImplementationOnce(async (_url, body) => {
        stored = { ...stored, ...(body as BannerUpdateData) }
        return { data: stored }
      })

    const user = mountPage()
    await user.click(await screen.findByRole('button', { name: 'Редактировать' }))

    await user.upload(screen.getByLabelText('Файл широкого ролика 3:1'), sizedMp4('wide.mp4', 900_000))
    expect(postSpy).toHaveBeenCalledWith(
      '/uploads/banners/video',
      expect.any(FormData),
      expect.objectContaining({ headers: { 'Content-Type': 'multipart/form-data' } })
    )
    const wideSlot = screen.getByRole('region', { name: 'Широкий ролик 3:1' })
    await waitFor(() => expect(wideSlot.querySelector('video')).toHaveAttribute('src', uploadedClipUrl))

    await user.click(screen.getByRole('button', { name: 'Сохранить изменения' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Заголовок (UZ)')
    expect(screen.queryByText('Изменения сохранены.')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Заголовок \(RU\)/)).toHaveValue('Весна')
    expect(wideSlot.querySelector('video')).toHaveAttribute('src', uploadedClipUrl)

    await user.type(screen.getByLabelText('Заголовок (UZ)'), 'Bahor')
    await user.type(screen.getByLabelText('Подзаголовок (UZ)'), 'Yangi aksiya')
    await user.click(screen.getByRole('button', { name: 'Сохранить изменения' }))

    expect(await screen.findByText('Изменения сохранены.')).toBeInTheDocument()
    expect(patchSpy).toHaveBeenCalledTimes(2)
    expect(patchSpy).toHaveBeenLastCalledWith('/banners/10', {
      title_uz: 'Bahor',
      subtitle_uz: 'Yangi aksiya',
      video_url: uploadedClipUrl,
    })

    const savedVideo = wideSlot.querySelector('video')
    expect(savedVideo).toHaveAttribute('src', uploadedClipUrl)
    expect(savedVideo).toHaveAttribute('controls')
    expect(savedVideo?.muted).toBe(true)
  })

  it('A-08 (user path 1): removing the wide clip of a banner with both clips clears both video fields in one PATCH', async () => {
    let stored = makeBanner({
      id: 10,
      title: 'Комплект',
      title_uz: 'Toʻplam',
      image_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-10.jpg',
      video_url: 'https://app.biotact.uz/uploads/banners/wide-10.mp4',
      video_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-10.mp4',
    })
    mockGetRoutes(() => [stored])
    const patchSpy = vi.spyOn(apiClient, 'patch').mockImplementation(async (_url, body) => {
      stored = { ...stored, ...(body as BannerUpdateData) }
      return { data: stored }
    })

    const user = mountPage()
    await user.click(await screen.findByRole('button', { name: 'Редактировать' }))
    const phoneClipSlot = screen.getByRole('region', { name: 'Ролик для телефонов 3:2' })
    expect(phoneClipSlot.querySelector('video')).toHaveAttribute(
      'src',
      'https://app.biotact.uz/uploads/banners/phone-10.mp4'
    )

    await user.click(screen.getByRole('button', { name: 'Удалить широкий ролик' }))
    expect(screen.getByText('Недоступен: сначала добавьте широкий ролик 3:1.')).toBeInTheDocument()
    expect(screen.getByLabelText('Файл ролика для телефонов 3:2')).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Сохранить изменения' }))

    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledTimes(1)
    })
    expect(patchSpy).toHaveBeenCalledWith('/banners/10', {
      video_url: null,
      video_mobile_url: null,
    })
  })

  it('A-05 (user path 2): adding a banner sends is_active false and explains how to show it', async () => {
    const items: Banner[] = []
    mockGetRoutes(() => items)
    const uploadedImageUrl = 'https://app.biotact.uz/uploads/banners/new-wide.jpg'
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { url: uploadedImageUrl, filename: 'new-wide.jpg' } })
      .mockImplementationOnce(async (_url, body) => {
        const created = makeBanner({
          id: 5,
          title: (body as { title: string }).title,
          image_url: uploadedImageUrl,
          is_active: (body as { is_active: boolean }).is_active,
        })
        items.push(created)
        return { data: created }
      })

    const user = mountPage()
    await screen.findByText('Нет баннеров')

    const fileInput = screen.getByLabelText('Загрузить изображение для нового баннера')
    await user.upload(fileInput, new File(['img'], 'new-wide.jpg', { type: 'image/jpeg' }))

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(2))
    expect(postSpy).toHaveBeenNthCalledWith(
      2,
      '/banners',
      expect.objectContaining({
        image_url: uploadedImageUrl,
        is_active: false,
      })
    )
    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('скрытым')
    expect(notice).toHaveTextContent('глаз')
    expect(screen.getByText('Скрыт')).toBeInTheDocument()
  })

  it('A-07 (user path 2): showing a hidden banner without phone picture while phone format is on asks confirmation before PATCH', async () => {
    let items: Banner[] = [
      makeBanner({
        id: 1,
        title: 'Активный',
        is_active: true,
        image_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-1.jpg',
      }),
      makeBanner({
        id: 2,
        title: 'Черновик',
        is_active: false,
        image_mobile_url: null,
        sort_order: 1,
      }),
    ]
    mockGetRoutes(() => items)
    const patchSpy = vi.spyOn(apiClient, 'patch').mockImplementation(async (_url, body) => {
      const updated = { ...items[1]!, ...(body as BannerUpdateData) }
      items = [items[0]!, updated]
      return { data: updated }
    })

    const user = mountPage()
    const showButton = await screen.findByRole('button', { name: 'Показать Черновик' })

    await user.click(showButton)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('3:1')
    expect(dialog).toHaveTextContent('3:2')

    await user.click(within(dialog).getByRole('button', { name: 'Отмена' }))
    expect(patchSpy).not.toHaveBeenCalled()

    await user.click(showButton)
    const confirmedDialog = screen.getByRole('dialog')
    await user.click(within(confirmedDialog).getByRole('button', { name: 'Всё равно показать' }))

    await waitFor(() => expect(patchSpy).toHaveBeenCalledTimes(1))
    expect(patchSpy).toHaveBeenCalledWith('/banners/2', { is_active: true })
  })

  it('A-07 (user path 2): removing the phone picture of an active banner while phone format is on confirms before PATCH', async () => {
    let stored = makeBanner({
      id: 1,
      title: 'Активный',
      is_active: true,
      image_mobile_url: 'https://app.biotact.uz/uploads/banners/phone-1.jpg',
    })
    mockGetRoutes(() => [stored])
    const patchSpy = vi.spyOn(apiClient, 'patch').mockImplementation(async (_url, body) => {
      stored = { ...stored, ...(body as BannerUpdateData) }
      return { data: stored }
    })

    const user = mountPage()
    await user.click(await screen.findByRole('button', { name: 'Редактировать' }))
    await user.click(screen.getByRole('button', { name: 'Удалить фото 3:2' }))
    await user.click(screen.getByRole('button', { name: 'Сохранить изменения' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('3:1')
    await user.click(within(dialog).getByRole('button', { name: 'Отмена' }))
    expect(patchSpy).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Сохранить изменения' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Сохранить без фото 3:2' }))

    await waitFor(() => expect(patchSpy).toHaveBeenCalledTimes(1))
    expect(patchSpy).toHaveBeenCalledWith('/banners/1', { image_mobile_url: null })
  })
})
