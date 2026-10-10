import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { productsApi } from "../api/productsApi";
import { SizeStockEditor } from "./SizeStockEditor";

const inventory = {
  product_id: 39,
  merchant_is_in_stock: true,
  sizes: [
    { size: "L", available_quantity: 50, revision: 0 },
    { size: "XL", available_quantity: 49, revision: 1 },
    { size: "2XL", available_quantity: 0, revision: 3 },
  ],
};
let client: QueryClient;
afterEach(() => {
  client?.clear();
  vi.restoreAllMocks();
});
function mount() {
  client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const reload = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <SizeStockEditor inventory={inventory} onReload={reload} />
    </QueryClientProvider>,
  );
  return reload;
}

it("saves all size counts with the loaded revisions in a separate action", async () => {
  const save = vi
    .spyOn(productsApi, "updateSizes")
    .mockResolvedValue(inventory);
  mount();
  fireEvent.change(screen.getByLabelText("XL", { exact: true }), {
    target: { value: "45" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Сохранить остатки размеров" }),
  );
  await screen.findByRole("status");
  expect(save).toHaveBeenCalledWith(39, [
    { size: "L", available_quantity: 50, expected_revision: 0 },
    { size: "XL", available_quantity: 45, expected_revision: 1 },
    { size: "2XL", available_quantity: 0, expected_revision: 3 },
  ]);
  expect(
    screen.getByRole("button", { name: "Сохранить остатки размеров" }),
  ).toBeDisabled();
});

it("a conflict blocks stale resubmission until the operator explicitly reloads", async () => {
  const save = vi
    .spyOn(productsApi, "updateSizes")
    .mockRejectedValue({ isAxiosError: true, response: { status: 409 } });
  const reload = mount();
  const button = screen.getByRole("button", {
    name: "Сохранить остатки размеров",
  });
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Загрузите актуальные значения",
  );
  fireEvent.change(screen.getByLabelText("L", { exact: true }), {
    target: { value: "51" },
  });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(save).toHaveBeenCalledTimes(1);
  fireEvent.click(
    screen.getByRole("button", { name: "Загрузить актуальные остатки" }),
  );
  await waitFor(() => expect(reload).toHaveBeenCalledOnce());
});
