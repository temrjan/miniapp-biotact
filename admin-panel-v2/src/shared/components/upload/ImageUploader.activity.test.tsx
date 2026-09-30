import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImageUploader } from './ImageUploader'
import { deferred, installHttp, refusal } from '@/features/campaigns/test/http'
import type { Reply } from '@/features/campaigns/test/http'

const restores: (() => void)[] = []
afterEach(() => { cleanup(); restores.forEach(fn => fn()); restores.length = 0; vi.restoreAllMocks() })
const image = () => new File(['synthetic'], 'fixture.png', { type: 'image/png' })

describe('ImageUploader actual dropzone and transport activity', () => {
  it('should signal before dispatch and refuse concurrent drop/removal until the batch settles', async () => {
    const pending = deferred<Reply>(), order: string[] = [], changes: string[][] = []
    const http = installHttp(() => { order.push('HTTP'); return pending.promise }); restores.push(http.restore)
    render(<ImageUploader value={['https://shop.test/uploads/old.png']} maxFiles={2}
      onChange={urls => changes.push(urls)} onUploadingChange={active => order.push(String(active))} />)
    const input = screen.getByLabelText('Загрузить изображение')
    await userEvent.setup().upload(input, image())
    await waitFor(() => expect(http.requests).toHaveLength(1))
    expect(order).toEqual(['true', 'HTTP'])
    expect(screen.getByRole('button', { name: 'Удалить изображение 1' })).toBeDisabled()
    // A second platform drop bypasses userEvent's disabled-input shortcut.
    fireEvent.drop(input.parentElement!, { dataTransfer: { files: [image()], items: [{ kind: 'file', type: 'image/png', getAsFile: image }], types: ['Files'] } })
    await act(async () => { pending.resolve({ status: 200, data: { url: 'https://shop.test/uploads/new.png', filename: 'new.png' } }) })
    await waitFor(() => expect(order.at(-1)).toBe('false'))
    expect(http.requests).toHaveLength(1)
    expect(changes).toEqual([['https://shop.test/uploads/old.png', 'https://shop.test/uploads/new.png']])
  })
  it('should retain the prior value after failed upload and always release activity', async () => {
    const http = installHttp(() => refusal(503, 'service_unavailable')); restores.push(http.restore)
    const onChange = vi.fn(), activity = vi.fn()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<ImageUploader value={['https://shop.test/uploads/old.png']} maxFiles={2} onChange={onChange} onUploadingChange={activity} />)
    await userEvent.setup().upload(screen.getByLabelText('Загрузить изображение'), image())
    expect(await screen.findByRole('alert')).toHaveTextContent('не удалось загрузить')
    expect(onChange).not.toHaveBeenCalled()
    expect(activity.mock.calls).toEqual([[true], [false]])
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://shop.test/uploads/old.png')
  })
  it.each([undefined, '/uploads/categories'])('should preserve existing endpoint %s and default multi-file/removal behavior', async endpoint => {
    const http = installHttp(() => ({ status: 200, data: { url: 'https://shop.test/uploads/new.png', filename: 'new.png' } })); restores.push(http.restore)
    const onChange = vi.fn()
    render(<ImageUploader {...(endpoint ? { uploadEndpoint: endpoint } : {})} value={['old']} onChange={onChange} />)
    await userEvent.setup().upload(screen.getByLabelText('Загрузить изображение'), [image(), image()])
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(['old', 'https://shop.test/uploads/new.png', 'https://shop.test/uploads/new.png']))
    expect(http.requests.map(r => r.url)).toEqual([endpoint ?? '/uploads/products', endpoint ?? '/uploads/products'])
    await userEvent.setup().click(screen.getByRole('button', { name: 'Удалить изображение 1' }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })
  it('should enforce campaign single-file limit without changing the existing default', async () => {
    const http = installHttp(() => ({ status: 200, data: { url: 'https://shop.test/uploads/new.png', filename: 'new.png' } })); restores.push(http.restore)
    const onChange = vi.fn()
    render(<ImageUploader maxFiles={1} onChange={onChange} onUploadingChange={() => {}} />)
    await userEvent.setup().upload(screen.getByLabelText('Загрузить изображение'), [image(), image()])
    expect(await screen.findByRole('alert')).toHaveTextContent('максимум 1')
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(['https://shop.test/uploads/new.png']))
    expect(http.requests).toHaveLength(1)
  })
})
