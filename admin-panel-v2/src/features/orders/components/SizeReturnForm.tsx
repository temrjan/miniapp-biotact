import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ordersApi } from "../api/ordersApi";
import type { SizeStockDetails } from "../types/order.types";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/shared/components/ui/card";

interface Props {
  orderId: number;
  stock: SizeStockDetails;
  onReload: () => void;
}

export function SizeReturnForm({ orderId, stock, onReload }: Props) {
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [reason, setReason] = useState("");
  const rows = stock.lines.filter(
    (line) => line.restored_quantity < line.allocated_quantity,
  );
  const items = rows
    .map((line) => ({
      order_item_id: line.order_item_id,
      quantity: Number(quantities[line.order_item_id] ?? 0),
    }))
    .filter((line) => line.quantity > 0);
  const valid =
    reason.trim().length > 0 &&
    items.length > 0 &&
    rows.every((line) => {
      const value = quantities[line.order_item_id] ?? "0";
      return (
        /^\d+$/.test(value) &&
        Number.isSafeInteger(Number(value)) &&
        Number(value) <= line.allocated_quantity - line.restored_quantity
      );
    });
  const save = useMutation({
    mutationFn: () =>
      ordersApi.returnSizes(orderId, {
        expected_revision: stock.revision,
        reason: reason.trim(),
        items,
      }),
    onSuccess: onReload,
  });
  if (!stock.dispatched_at) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Полученный возврат</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p>
          Укажите только полученные вещи, пригодные для продажи. Денежный
          возврат оформляется отдельно.
        </p>
        {rows.map((line) => (
          <label className="block" key={line.order_item_id}>
            {line.size} — осталось подтвердить{" "}
            {line.allocated_quantity - line.restored_quantity}
            <Input
              type="number"
              min="0"
              step="1"
              value={quantities[line.order_item_id] ?? "0"}
              onChange={(event) =>
                setQuantities((values) => ({
                  ...values,
                  [line.order_item_id]: event.target.value,
                }))
              }
            />
          </label>
        ))}
        <label className="block">
          Причина возврата
          <Input
            value={reason}
            maxLength={2000}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <Button
          type="button"
          disabled={!valid || save.isPending || save.isSuccess}
          onClick={() => save.mutate()}
        >
          Подтвердить возврат на склад
        </Button>
        {save.isError && (
          <p role="alert">
            Возврат не подтверждён. Загрузите актуальный заказ и проверьте
            данные.
          </p>
        )}
        <Button
          type="button"
          variant="outline"
          onClick={onReload}
          disabled={save.isPending}
        >
          Обновить заказ
        </Button>
      </CardContent>
    </Card>
  );
}
