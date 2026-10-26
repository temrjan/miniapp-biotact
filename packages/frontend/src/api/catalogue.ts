import { api } from './client';
import type { ApiProductPrice } from '@/utils/price';
import type { ApiSizeStock } from '@/utils/size';

/** Snake-case catalogue fields used by the current storefront mappers. */
export interface CatalogueProduct extends ApiProductPrice, ApiSizeStock {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  image_url: string | null;
  images: string[] | null;
  category_id: number | null;
  stock_quantity: number | null;
  is_in_stock: boolean;
  is_purchasable: boolean;
  additional_info_title: string | null;
  additional_info: string | null;
  disclaimer: string | null;
  video_url: string | null;
}
export interface CatalogueCategory {
  id: number;
  name: string;
  slug: string;
  image_url: string | null;
}
export interface CatalogueBanner {
  id: number;
  title: string;
  title_uz: string | null;
  subtitle: string | null;
  subtitle_uz: string | null;
  image_url: string | null;
  gradient: string | null;
  link_url: string | null;
  sort_order: number;
  is_active: boolean;
}
export interface CataloguePage<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid catalogue object');
  return value as Record<string, unknown>;
}
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) throw new Error('Invalid catalogue integer');
  return value;
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Invalid catalogue text');
  return value;
}
function nullableText(value: unknown): string | null { return value == null ? null : text(value); }
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Invalid catalogue flag');
  return value;
}
function money(value: unknown): number | string {
  if ((typeof value !== 'number' && typeof value !== 'string') || value === '' || !Number.isFinite(Number(value))) throw new Error('Invalid catalogue price');
  return value;
}
function texts(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('Invalid catalogue list');
  return value.map(text);
}
export function parseProduct(value: unknown): CatalogueProduct {
  const p = record(value);
  return {
    id: integer(p.id, 1), name: text(p.name), slug: text(p.slug), description: nullableText(p.description),
    price: money(p.price), old_price: p.old_price == null ? null : money(p.old_price),
    is_sale: boolean(p.is_sale), sale_price: p.sale_price == null ? null : money(p.sale_price),
    image_url: nullableText(p.image_url), images: p.images == null ? null : texts(p.images),
    category_id: p.category_id === null ? null : integer(p.category_id, 1),
    stock_quantity: p.stock_quantity === null ? null : integer(p.stock_quantity),
    is_in_stock: boolean(p.is_in_stock), is_purchasable: boolean(p.is_purchasable),
    ...(p.requires_size === undefined ? {} : { requires_size: boolean(p.requires_size) }),
    ...(p.available_sizes === undefined ? {} : { available_sizes: texts(p.available_sizes) }),
    ...(p.is_active === undefined ? {} : { is_active: boolean(p.is_active) }),
    additional_info_title: nullableText(p.additional_info_title), additional_info: nullableText(p.additional_info),
    disclaimer: nullableText(p.disclaimer), video_url: nullableText(p.video_url),
  };
}
export function parseCategory(value: unknown): CatalogueCategory {
  const c = record(value);
  return { id: integer(c.id, 1), name: text(c.name), slug: text(c.slug), image_url: nullableText(c.image_url) };
}
function parseBanner(value: unknown): CatalogueBanner {
  const b = record(value);
  return { id: integer(b.id, 1), title: text(b.title), title_uz: nullableText(b.title_uz),
    subtitle: nullableText(b.subtitle), subtitle_uz: nullableText(b.subtitle_uz), image_url: nullableText(b.image_url),
    gradient: nullableText(b.gradient), link_url: nullableText(b.link_url),
    sort_order: typeof b.sort_order === 'number' && Number.isInteger(b.sort_order) ? b.sort_order : integer(undefined),
    is_active: boolean(b.is_active) };
}
export function parsePage<T>(value: unknown, parse: (item: unknown) => T, page: number, size: number): CataloguePage<T> {
  const data = record(value);
  const total = integer(data.total);
  const pages = integer(data.pages, 1);
  if (data.page !== page || data.size !== size || pages !== Math.max(1, Math.ceil(total / size)) ||
      !Array.isArray(data.items) || data.items.length !== Math.min(size, Math.max(0, total - (page - 1) * size)))
    throw new Error('Invalid catalogue page');
  return { items: data.items.map(parse), total, page, size, pages };
}
const languageHeaders = (language: string) => ({ headers: { 'X-Language': language } });

async function allPages<T>(endpoint: string, size: number, language: string, parse: (item: unknown) => T, params: Record<string, string> = {}): Promise<T[]> {
  const items: T[] = [];
  let total: number | undefined;
  for (let page = 1; ; page++) {
    const query = new URLSearchParams({ ...params, size: String(size), page: String(page) });
    const data = parsePage(await api.get<unknown>(`${endpoint}?${query}`, languageHeaders(language)), parse, page, size);
    if (total !== undefined && data.total !== total) throw new Error('Catalogue changed during traversal');
    total = data.total;
    items.push(...data.items);
    if (page >= data.pages) return items;
  }
}
export const loadCategories = (language: string) => allPages('/categories', 100, language, parseCategory);
export const loadProducts = (language: string, params: Record<string, string> = {}, size = 100) =>
  allPages('/products', size, language, parseProduct, params);
export async function loadActiveBanners(language: string): Promise<CatalogueBanner[]> {
  const items: CatalogueBanner[] = [];
  let total: number | undefined;
  for (let skip = 0; ; skip += 100) {
    const data = record(await api.get<unknown>(`/banners?is_active=true&limit=100&skip=${skip}`, languageHeaders(language)));
    const count = integer(data.total);
    if ((total !== undefined && count !== total) || !Array.isArray(data.items) || data.items.length !== Math.min(100, Math.max(0, count - skip)))
      throw new Error('Invalid banner page');
    total = count;
    const page = data.items.map(parseBanner);
    if (page.some(b => !b.is_active)) throw new Error('Invalid active banner');
    items.push(...page);
    if (items.length >= count) return items;
  }
}
