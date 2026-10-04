import { z } from 'zod';
import { apiClient } from '@/shared/api/client';

const max = 9223372036854775807n;
const positive = z.string().regex(/^[1-9][0-9]{0,18}$/).refine(value => BigInt(value) <= max);
const quantity = z.string().regex(/^(0|[1-9][0-9]{0,18})$/).refine(value => BigInt(value) <= max);
const uuid = z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
const delta = z.strictObject({ order_item_id: positive, quantity: positive });
const lines = z.array(delta).nonempty().refine(values => new Set(values.map(line => line.order_item_id)).size === values.length);
export const intentSchema = z.strictObject({ return_id: uuid, lines });
const summarySchema = z.strictObject({
  order_id: positive, order_version: positive,
  lines: z.array(z.strictObject({ order_item_id: positive, product_id: positive, paid_quantity: positive, returned_quantity: quantity })
    .refine(line => BigInt(line.returned_quantity) <= BigInt(line.paid_quantity))).nonempty(),
}).refine(value => value.lines.every((line, index) => index === 0 || BigInt(line.order_item_id) > BigInt(value.lines[index - 1]?.order_item_id ?? '0')));
const receiptSchema = z.strictObject({
  receipt: z.strictObject({ return_id: uuid, order_id: positive, order_version: positive.refine(value => BigInt(value) >= 2n),
    confirmed_at: z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/).refine(value => Number.isFinite(Date.parse(value))), lines }),
  replayed: z.boolean(),
});
export type ReturnIntent = z.infer<typeof intentSchema>;
export type ReturnSummary = z.infer<typeof summarySchema>;
export type ReturnReceipt = z.infer<typeof receiptSchema>;
export const normalizeLines = (values: ReturnIntent['lines']) => [...values].sort((a, b) => BigInt(a.order_item_id) < BigInt(b.order_item_id) ? -1 : 1);

export const referralReturnsApi = {
  async summary(orderId: string): Promise<ReturnSummary> {
    positive.parse(orderId);
    const response = await apiClient.get<unknown>(`/admin/orders/${orderId}/referral-returns`);
    const summary = summarySchema.parse(response.data);
    if (summary.order_id !== orderId) throw new Error('Invalid return summary');
    return summary;
  },
  async confirm(orderId: string, intent: ReturnIntent): Promise<ReturnReceipt> {
    positive.parse(orderId);
    intentSchema.parse(intent);
    const response = await apiClient.post<unknown>(`/admin/orders/${orderId}/referral-returns`, intent);
    const result = receiptSchema.parse(response.data);
    if (result.receipt.order_id !== orderId || result.receipt.return_id !== intent.return_id ||
      JSON.stringify(normalizeLines(result.receipt.lines)) !== JSON.stringify(normalizeLines(intent.lines))) throw new Error('Invalid return receipt');
    return result;
  },
};
