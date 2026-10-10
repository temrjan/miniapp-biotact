import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { productsApi, type SizeInventory } from "../api/productsApi";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/shared/components/ui/card";

interface Props {
  inventory: SizeInventory;
  onReload: () => void;
}

export function SizeStockEditor({ inventory, onReload }: Props) {
  const [quantities, setQuantities] = useState(() =>
    inventory.sizes.map((row) => String(row.available_quantity)),
  );
  const save = useMutation({
    mutationFn: () =>
      productsApi.updateSizes(
        inventory.product_id,
        inventory.sizes.map((row, i) => ({
          size: row.size,
          available_quantity: Number(quantities[i]),
          expected_revision: row.revision,
        })),
      ),
  });
  const valid = quantities.every(
    (value) =>
      /^\d+$/.test(value) &&
      Number.isSafeInteger(Number(value)) &&
      Number(value) <= 2147483647,
  );
  const stale = isAxiosError(save.error) && save.error.response?.status === 409;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Доступно для продажи по размерам</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Эти количества сохраняются отдельно от описания и цены товара.
        </p>
        {inventory.sizes.map((row, i) => (
          <label key={row.size} className="block">
            {row.size}
            <Input
              type="number"
              min="0"
              step="1"
              value={quantities[i]}
              disabled={save.isPending}
              onChange={(event) =>
                setQuantities((values) =>
                  values.map((value, index) =>
                    index === i ? event.target.value : value,
                  ),
                )
              }
            />
          </label>
        ))}
        <Button
          type="button"
          disabled={!valid || save.isPending || stale || save.isSuccess}
          onClick={() => save.mutate()}
        >
          Сохранить остатки размеров
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={save.isPending}
          onClick={onReload}
        >
          Загрузить актуальные остатки
        </Button>
        {save.isSuccess && (
          <p role="status">
            Остатки размеров сохранены. Для следующего изменения загрузите
            актуальные остатки.
          </p>
        )}
        {save.isError && (
          <p role="alert">
            {stale
              ? "Остатки изменились. Загрузите актуальные значения и заново внесите правку."
              : "Остатки не сохранены. Проверьте данные и повторите."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
