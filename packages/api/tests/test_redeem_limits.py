"""Unit tests for the pure redeem maths (Stage 6 partial redemption).

``max_redeemable`` and ``build_ofd_items`` are pure functions — the cheap, exhaustive layer under
the integration suite in ``test_orders_redeem.py``. The limit examples are the ones the plan pins by
hand; the OFD cases pin the fiscal invariant ``Σ line total == amount charged``.
"""

from decimal import Decimal

import pytest

from src.orders.models import OrderItem
from src.orders.service import max_redeemable
from src.payments.service import build_ofd_items


class TestMaxRedeemable:
    """The redemption ceiling: goods-only, step-aligned, card left with 0 or ≥ 5000."""

    def test_partial_is_capped_by_the_card_floor_not_the_balance(self) -> None:
        """balance 93 920, goods 94 000 → 89 000 — NOT 93 900: the card must keep ≥ 5 000."""
        assert (
            max_redeemable(balance=93_920, subtotal=Decimal("94000"), total=Decimal("94000"))
            == 89_000
        )

    def test_full_cover_wins_when_the_balance_affords_it(self) -> None:
        """A rich balance can spend the whole goods sum (remainder 0), beating the partial cap."""
        assert (
            max_redeemable(balance=1_000_000, subtotal=Decimal("94000"), total=Decimal("94000"))
            == 94_000
        )

    def test_remainder_exactly_the_floor_is_reachable(self) -> None:
        """balance 64 000, goods 69 000 → 64 000, leaving the card exactly 5 000 (the tight edge)."""
        assert (
            max_redeemable(balance=64_000, subtotal=Decimal("69000"), total=Decimal("69000"))
            == 64_000
        )

    def test_balance_above_the_floor_still_floors_to_keep_the_card_at_5000(self) -> None:
        """balance 66 000, goods 69 000 → 64 000: spending 66 000 would leave 3 000 (dead zone)."""
        assert (
            max_redeemable(balance=66_000, subtotal=Decimal("69000"), total=Decimal("69000"))
            == 64_000
        )

    def test_small_order_allows_only_full_cover(self) -> None:
        """goods 3 000 (< MIN_REDEEM + floor) → the only redemption is 100 % (partial is impossible)."""
        assert (
            max_redeemable(balance=1_000_000, subtotal=Decimal("3000"), total=Decimal("3000"))
            == 3_000
        )

    def test_small_order_without_the_balance_for_full_is_zero(self) -> None:
        """goods 3 000, balance 2 000 → 0: cannot afford full, and no partial exists → only card."""
        assert max_redeemable(balance=2_000, subtotal=Decimal("3000"), total=Decimal("3000")) == 0

    def test_zero_balance_is_never_redeemable(self) -> None:
        assert max_redeemable(balance=0, subtotal=Decimal("94000"), total=Decimal("94000")) == 0

    def test_eligible_is_subtotal_not_total_so_delivery_is_never_redeemed(self) -> None:
        """goods 10 000 + delivery 5 000 → max 10 000, never 15 000: points cover goods only."""
        assert (
            max_redeemable(balance=1_000_000, subtotal=Decimal("10000"), total=Decimal("15000"))
            == 10_000
        )

    def test_delivery_can_push_full_into_the_dead_zone_leaving_only_partial(self) -> None:
        """goods 10 000 + delivery 2 000: full would leave the card 2 000 (dead) → partial 7 000."""
        assert (
            max_redeemable(balance=1_000_000, subtotal=Decimal("10000"), total=Decimal("12000"))
            == 7_000
        )

    def test_non_step_subtotal_disqualifies_full_cover(self) -> None:
        """goods 94 050 is not a multiple of 100 → full is not a legal amount → partial 89 000."""
        assert (
            max_redeemable(balance=1_000_000, subtotal=Decimal("94050"), total=Decimal("94050"))
            == 89_000
        )


def _item(total: Decimal, *, quantity: int = 1, price: Decimal | None = None) -> OrderItem:
    """A transient order line for the pure OFD tests (no session, no product row)."""
    return OrderItem(
        product_name="Item",
        product_price=price if price is not None else total / quantity,
        quantity=quantity,
        total=total,
    )


class TestBuildOfdItems:
    """The discount model: emitted line totals must sum EXACTLY to the amount charged."""

    def test_card_amount_passes_lines_through_unchanged(self) -> None:
        """When the charge equals the full catalogue sum, every line is verbatim (card regression)."""
        items = [_item(Decimal("69000")), _item(Decimal("31000"))]
        full = (69_000 + 31_000) * 100

        ofd = build_ofd_items(items, amount_tiyins=full)

        assert [o.total for o in ofd] == [6_900_000, 3_100_000]
        assert [o.price for o in ofd] == [6_900_000, 3_100_000]

    def test_partial_amount_scales_lines_to_sum_exactly(self) -> None:
        """Charging half spreads the discount pro-rata; the totals still sum to the charge.

        This is the mutation catcher for the scaling: pass the lines through unchanged (skip
        ``_scale_line_totals``) and Σ stays 10 000 000 ≠ 5 000 000 → the fiscal invariant raises.
        """
        items = [_item(Decimal("69000")), _item(Decimal("31000"))]
        charge = 5_000_000  # exactly half of the 10 000 000 catalogue sum

        ofd = build_ofd_items(items, amount_tiyins=charge)

        # The exact list pins the pro-rata split AND its sum (3 450 000 + 1 550 000 == charge).
        assert [o.total for o in ofd] == [3_450_000, 1_550_000]

    def test_last_line_absorbs_the_rounding_residue(self) -> None:
        """Three equal lines charged 2/3: floors undershoot by 2 tiyins → the last line takes them."""
        items = [_item(Decimal("100")), _item(Decimal("100")), _item(Decimal("100"))]
        charge = 20_000  # 2/3 of 30 000; 30 000 // 3 * 2 leaves a 2-tiyin residue

        ofd = build_ofd_items(items, amount_tiyins=charge)

        # 6 666 + 6 666 + 6 668 == 20 000: the last line carries the 2-tiyin residue.
        assert [o.total for o in ofd] == [6_666, 6_666, 6_668]

    def test_charge_above_the_catalogue_sum_is_refused(self) -> None:
        """A charge exceeding the line totals cannot be reconciled — fail loud, not silent."""
        items = [_item(Decimal("69000"))]
        with pytest.raises(ValueError, match="outside"):
            build_ofd_items(items, amount_tiyins=6_900_000 + 1)

    def test_zero_charge_is_refused(self) -> None:
        """A non-positive charge against real lines is bad data — refused, never a free receipt."""
        items = [_item(Decimal("69000"))]
        with pytest.raises(ValueError, match="outside"):
            build_ofd_items(items, amount_tiyins=0)

    def test_multi_quantity_partial_line_stays_consistent_and_exact(self) -> None:
        """qty > 1 with an indivisible discount: every line keeps ``total == price * qty`` AND the
        receipt (``Σ price * qty``) equals the charge — the [Gate-2 BLOCKER] regression.

        3 units of 69 000 = 207 000 goods; 100 000 spent as points leaves 107 000 to the card.
        107 000 / 3 is not whole, so the line is split (2 units + 1 unit carrying the 2-tiyin
        remainder) rather than emit a price whose ``price * qty`` disagrees with the total the
        receipt shows.
        """
        items = [_item(Decimal("207000"), quantity=3, price=Decimal("69000"))]
        charge = 10_700_000  # (207 000 - 100 000) * 100

        ofd = build_ofd_items(items, amount_tiyins=charge)

        assert all(o.total == o.price * o.qty for o in ofd)  # each line internally consistent
        assert sum(o.price * o.qty for o in ofd) == charge  # the receipt equals the charge
        assert sum(o.qty for o in ofd) == 3  # all three units still on the receipt
        assert [(o.qty, o.price) for o in ofd] == [(2, 3_566_666), (1, 3_566_668)]

    def test_multi_quantity_card_path_stays_consistent(self) -> None:
        """A qty > 1 card order (no discount) keeps the catalogue unit price, total == price * qty."""
        items = [_item(Decimal("207000"), quantity=3, price=Decimal("69000"))]
        full = 3 * 6_900_000

        ofd = build_ofd_items(items, amount_tiyins=full)

        assert [(o.qty, o.price, o.total) for o in ofd] == [(3, 6_900_000, 20_700_000)]

    def test_divisible_multi_quantity_needs_no_split(self) -> None:
        """When the discounted total IS divisible by qty, one line suffices — no cosmetic split."""
        items = [_item(Decimal("138000"), quantity=2, price=Decimal("69000"))]
        charge = 6_900_000  # half of 13 800 000; 3 450 000 per unit, exact

        ofd = build_ofd_items(items, amount_tiyins=charge)

        assert [(o.qty, o.price, o.total) for o in ofd] == [(2, 3_450_000, 6_900_000)]
        assert sum(o.price * o.qty for o in ofd) == charge
