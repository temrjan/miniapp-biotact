import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ordersApi } from "../api/ordersApi";
import { SizeReturnForm } from "./SizeReturnForm";

let client: QueryClient;
afterEach(() => {
  client?.clear();
  vi.restoreAllMocks();
});
it("requires received saleable units and a reason, sends a revision and blocks a duplicate confirmation", async () => {
  const save = vi
    .spyOn(ordersApi, "returnSizes")
    .mockResolvedValue({
      revision: 8,
      lines: [{ order_item_id: 5, restored_quantity: 2 }],
    });
  const reload = vi.fn();
  client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SizeReturnForm
        orderId={39}
        stock={{
          protocol: "size_stock_v1",
          owner_state: "refunded",
          revision: 7,
          dispatched_at: "2026-10-09T00:00:00Z",
          lines: [
            {
              order_item_id: 5,
              size: "XL",
              allocated_quantity: 3,
              restored_quantity: 1,
            },
          ],
        }}
        onReload={reload}
      />
    </QueryClientProvider>,
  );
  const confirm = screen.getByRole("button", {
    name: "Подтвердить возврат на склад",
  });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/XL/), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Причина возврата"), {
    target: { value: "Received saleable unit" },
  });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/XL/), { target: { value: "1" } });
  fireEvent.click(confirm);
  await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(save).toHaveBeenCalledWith(39, {
    expected_revision: 7,
    reason: "Received saleable unit",
    items: [{ order_item_id: 5, quantity: 1 }],
  });
  expect(confirm).toBeDisabled();
  expect(save).toHaveBeenCalledOnce();
});
