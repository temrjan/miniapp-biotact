import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { productsApi } from '@/features/products/api/productsApi'
import { Input } from '@/shared/components/ui/input'
import { Button } from '@/shared/components/ui/button'
import { useCampaignActions } from '../hooks/useCampaignActions'

interface PickerProps { value: string; onChange: (id: string) => void; disabled: boolean; error?: string | undefined }
export function CampaignProductPicker({ value, onChange, disabled, error }: PickerProps) {
  const [search, setSearch] = useState(''), [page, setPage] = useState(1)
  const { controller, actorId, state } = useCampaignActions()
  const enabled = actorId !== null && state.status !== 'accessDenied'
  const list = useQuery({ queryKey: ['campaigns', actorId, 'products', search, page],
    queryFn: () => controller.read(() => productsApi.getProducts({ search, page, size: 20, active_only: false })),
    enabled, retry: false, refetchOnWindowFocus: false })
  const id = Number(value)
  const selected = useQuery({ queryKey: ['campaigns', actorId, 'product', value],
    queryFn: () => controller.read(() => productsApi.getProduct(id)),
    enabled: enabled && Number.isSafeInteger(id) && id > 0, retry: false, refetchOnWindowFocus: false })
  return <section className="space-y-3" aria-label="Выбор товара">
    <label className="block" htmlFor="campaign-product-id">Товар · ID</label>
    <Input id="campaign-product-id" inputMode="numeric" value={value} onChange={e => onChange(e.target.value)} disabled={disabled}
      aria-invalid={!!error} aria-describedby={error ? 'campaign-product-error' : undefined} />
    {error && <p id="campaign-product-error" className="text-destructive">{error}</p>}
    {value && <p>{selected.data ? `${selected.data.name} · №${value}${selected.data.is_active ? '' : ' · отключён'}` :
      selected.isError ? `Товар №${value}: название недоступно; ID сохранён.` : `Товар №${value}: проверка названия…`}</p>}
    {!disabled && <>
      <label className="block" htmlFor="campaign-product-search">Поиск товара</label>
      <Input id="campaign-product-search" value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} />
      {list.isFetching ? <p role="status">Загрузка товаров…</p> : list.isError ? <p role="alert">Не удалось получить товары.</p> :
        <ul className="space-y-1">{list.data?.items.map(product => <li key={product.id}>
          <Button type="button" variant="outline" className="max-w-full h-auto whitespace-normal text-left" onClick={() => onChange(String(product.id))}>
            {product.name} · №{product.id}{product.is_active ? '' : ' · отключён'}
          </Button>
        </li>)}</ul>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => setPage(p => p - 1)} disabled={page === 1 || list.isFetching}>Предыдущие товары</Button>
        <span>Страница товаров {page}</span>
        <Button type="button" variant="outline" onClick={() => setPage(p => p + 1)} disabled={list.isFetching || !list.data || page >= list.data.pages}>Следующие товары</Button>
        <Button type="button" variant="outline" onClick={() => onChange('')} disabled={!value}>Очистить товар</Button>
      </div>
      <p className="text-sm text-muted-foreground">Каталог помогает выбрать товар. Возможность публикации проверяет сервер; это не подтверждение физического наличия.</p>
    </>}
  </section>
}
