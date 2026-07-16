import { describe, it, expect } from 'vitest';

import { MIN_CARD_REMAINDER, MIN_REDEEM, clampRedeem, maxRedeemable } from './redeem';

/**
 * Пробы против РЕАЛЬНЫХ `maxRedeemable`/`clampRedeem` (не локальных копий) — это зеркало
 * бэкенда `orders/service.py::max_redeemable`, и границы взяты 1-в-1 с `test_redeem_limits.py`.
 * Если фронт разойдётся с бэкендом — красный тут, а не тихий 422 у покупателя.
 */

describe('maxRedeemable — зеркало бэкенда, полный набор границ', () => {
  it('partial капается card-floor, не балансом (93 920 / 94 000 → 89 000)', () => {
    expect(maxRedeemable({ balance: 93_920, subtotal: 94_000, total: 94_000 })).toBe(89_000);
  });

  it('full-cover побеждает при богатом балансе (→ 94 000)', () => {
    expect(maxRedeemable({ balance: 1_000_000, subtotal: 94_000, total: 94_000 })).toBe(94_000);
  });

  it('остаток ровно 5 000 достижим (64 000 / 69 000 → 64 000)', () => {
    expect(maxRedeemable({ balance: 64_000, subtotal: 69_000, total: 69_000 })).toBe(64_000);
  });

  it('баланс выше floor всё равно капается, чтобы картой осталось 5 000 (66 000 → 64 000)', () => {
    expect(maxRedeemable({ balance: 66_000, subtotal: 69_000, total: 69_000 })).toBe(64_000);
  });

  it('малый заказ — только full-cover (3 000 / богатый баланс → 3 000)', () => {
    expect(maxRedeemable({ balance: 1_000_000, subtotal: 3_000, total: 3_000 })).toBe(3_000);
  });

  it('малый заказ без баланса на full → 0', () => {
    expect(maxRedeemable({ balance: 2_000, subtotal: 3_000, total: 3_000 })).toBe(0);
  });

  it('нулевой баланс → 0', () => {
    expect(maxRedeemable({ balance: 0, subtotal: 94_000, total: 94_000 })).toBe(0);
  });

  it('eligible = subtotal, НЕ total: доставка баллами не гасится (sub 10 000 / total 15 000 → 10 000)', () => {
    expect(maxRedeemable({ balance: 1_000_000, subtotal: 10_000, total: 15_000 })).toBe(10_000);
  });

  it('доставка выталкивает full в мёртвую зону → остаётся partial (sub 10 000 / total 12 000 → 7 000)', () => {
    expect(maxRedeemable({ balance: 1_000_000, subtotal: 10_000, total: 12_000 })).toBe(7_000);
  });

  it('subtotal не кратен шагу → full недоступен, только partial (94 050 → 89 000)', () => {
    expect(maxRedeemable({ balance: 1_000_000, subtotal: 94_050, total: 94_050 })).toBe(89_000);
  });
});

describe('clampRedeem — всегда валидное значение, мёртвая зона перепрыгивается', () => {
  const money = { balance: 100_000, subtotal: 69_000, total: 69_000 }; // max=69000 (full), partialMax=64000

  it('0 и отрицательное → 0 (только карта)', () => {
    expect(clampRedeem(0, money)).toBe(0);
    expect(clampRedeem(-500, money)).toBe(0);
  });

  it('ниже MIN_REDEEM → 0', () => {
    expect(clampRedeem(300, money)).toBe(0);
    expect(clampRedeem(MIN_REDEEM - 1, money)).toBe(0);
  });

  it('снап на шаг 100 вниз', () => {
    expect(clampRedeem(60_050, money)).toBe(60_000);
    expect(clampRedeem(MIN_REDEEM, money)).toBe(MIN_REDEEM);
  });

  it('partial-потолок (остаток ровно 5 000)', () => {
    expect(clampRedeem(64_000, money)).toBe(64_000);
    expect(69_000 - 64_000).toBe(MIN_CARD_REMAINDER);
  });

  it('мёртвая зона (остаток 1..4999) снапится вниз к partial-потолку, не возвращается', () => {
    // 66 000 оставило бы картой 3 000 (мёртвая зона) → снап к 64 000.
    expect(clampRedeem(66_000, money)).toBe(64_000);
  });

  it('тянущий до самого верха при доступном full → полное покрытие', () => {
    expect(clampRedeem(69_000, money)).toBe(69_000);
    expect(clampRedeem(1_000_000, money)).toBe(69_000);
  });

  it('недоступное списание (баланс мал) → всегда 0', () => {
    const poor = { balance: 100, subtotal: 69_000, total: 69_000 };
    expect(clampRedeem(500, poor)).toBe(0);
    expect(clampRedeem(69_000, poor)).toBe(0);
  });

  it('малый заказ — только 0 или full (partial невозможен)', () => {
    const small = { balance: 1_000_000, subtotal: 3_000, total: 3_000 };
    expect(clampRedeem(1_000, small)).toBe(0); // partial недостижим
    expect(clampRedeem(3_000, small)).toBe(3_000); // full
  });
});
