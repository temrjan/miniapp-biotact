/** Validate complete catalogue reads without changing ordinary server-paged calls. */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
export function collection(value: unknown, offset: number, size: number, previousTotal?: number) {
  if (!isRecord(value) || typeof value.total !== 'number' || !Number.isSafeInteger(value.total) || value.total < 0 ||
      (previousTotal !== undefined && value.total !== previousTotal) || !Array.isArray(value.items) ||
      value.items.length !== Math.min(size, Math.max(0, value.total - offset)))
    throw new Error('Invalid catalogue collection')
  return { items: value.items as unknown[], total: value.total }
}
export function categoryPage(value: unknown, page: number, size: number, previousTotal?: number) {
  const data = collection(value, (page - 1) * size, size, previousTotal)
  if (!isRecord(value) || value.page !== page || value.size !== size ||
      value.pages !== Math.max(1, Math.ceil(data.total / size))) throw new Error('Invalid category page')
  return { ...data, pages: value.pages }
}
