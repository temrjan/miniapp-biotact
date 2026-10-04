/** One document owner. Tokens stay out of React snapshots, devtools and query cache. */
export const REFERRAL_KEY = 'biotact_employee_referral';
export type EntryStatus = 'empty' | 'resolving' | 'active' | 'unavailable';
export interface ReferralRecord {
  version: 1;
  token: string;
  expires_at: string;
}
interface ValidResponse {
  state: 'valid';
  product_id: string;
  observed_at: string;
  expires_at: string;
}
type ReferralStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
interface ContextOptions {
  enabled: () => boolean;
  resolve: (token: string) => Promise<unknown>;
  storage: () => ReferralStorage;
  monotonic: () => number;
  wall: () => number;
  identity: () => string;
  onInvalidate?: () => void;
}

export function canonicalToken(value: unknown): value is string {
  // 16 bytes -> 22 chars; the final sextet's low four bits must be zero.
  return typeof value === 'string' && /^[A-Za-z0-9_-]{21}[AQgw]$/.test(value);
}
export function canonicalProduct(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^[1-9][0-9]{0,18}$/.test(value) &&
    BigInt(value) <= 9223372036854775807n
  );
}
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function utc(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(value))
    return false;
  const stamp = Date.parse(value);
  return Number.isFinite(stamp) && new Date(stamp).toISOString() === value.slice(0, 23) + 'Z';
}
export function parseRecord(raw: string | null): ReferralRecord | null {
  if (raw === null || raw.length > 256) return null;
  try {
    const value: unknown = JSON.parse(raw);
    return object(value) &&
      Object.keys(value).length === 3 &&
      value.version === 1 &&
      canonicalToken(value.token) &&
      utc(value.expires_at)
      ? { version: 1, token: value.token, expires_at: value.expires_at }
      : null;
  } catch {
    return null;
  }
}
function validResponse(value: unknown): value is ValidResponse {
  return (
    object(value) &&
    Object.keys(value).length === 4 &&
    value.state === 'valid' &&
    canonicalProduct(value.product_id) &&
    utc(value.observed_at) &&
    utc(value.expires_at) &&
    value.expires_at > value.observed_at
  );
}

export class ReferralContext {
  private generation = 0;
  private active: ReferralRecord | null = null;
  private candidate: string | null = null;
  private deadline = 0;
  private memoryOnly = false;
  private status: EntryStatus = 'empty';
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private identity: string;
  private options: ContextOptions;
  constructor(options: ContextOptions) {
    this.options = options;
    this.identity = options.identity();
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  snapshot = (): EntryStatus => this.status;
  private notify(status: EntryStatus) {
    this.status = status;
    this.listeners.forEach((listener) => listener());
  }
  private storage(operation: (storage: ReferralStorage) => void) {
    if (this.memoryOnly) return;
    try {
      operation(this.options.storage());
    } catch {
      this.memoryOnly = true;
    }
  }
  private read(): ReferralRecord | null {
    let record: ReferralRecord | null = null;
    this.storage((storage) => {
      record = parseRecord(storage.getItem(REFERRAL_KEY));
    });
    return record;
  }
  private clear(status: EntryStatus, removeStorage = true) {
    this.generation += 1;
    this.options.onInvalidate?.();
    clearTimeout(this.timer);
    this.active = null;
    this.candidate = null;
    this.deadline = 0;
    if (removeStorage) this.storage((storage) => storage.removeItem(REFERRAL_KEY));
    this.notify(status);
  }
  reset = () => {
    this.clear('empty');
  };
  checkIdentity(): boolean {
    const identity = this.options.identity();
    if (identity === this.identity) return false;
    this.identity = identity;
    this.reset();
    return true;
  }
  async enter(values: string[], redirect?: (product: string) => void): Promise<void> {
    this.checkIdentity();
    this.clear('empty'); // synchronous before the first await, including malformed/OFF.
    if (!this.options.enabled()) return;
    const token = values.length === 1 ? values[0] : undefined;
    if (!canonicalToken(token)) {
      this.notify('unavailable');
      return;
    }
    await this.validate(token, redirect);
  }
  async reopen(): Promise<void> {
    if (this.checkIdentity()) return;
    if (!this.options.enabled()) {
      this.reset();
      return;
    }
    if (this.status === 'resolving') return; // coalesce focus + visibility.
    const stored = this.memoryOnly ? null : this.read();
    const current = this.memoryOnly ? this.candidate : stored?.token;
    if (!current) {
      this.reset();
      return;
    }
    const record = this.active ?? stored;
    if (record && Date.parse(record.expires_at) <= this.options.wall()) {
      this.reset();
      return;
    }
    this.clear('empty', false);
    await this.validate(current);
  }
  async storageChanged(): Promise<void> {
    if (this.memoryOnly) return;
    // Invalidate even a pending request; reread actual record rather than trusting event data.
    const record = this.read();
    if (this.memoryOnly) return;
    this.clear('empty', false);
    if (record && this.options.enabled() && Date.parse(record.expires_at) > this.options.wall()) {
      await this.validate(record.token);
    }
  }
  private async validate(token: string, redirect?: (product: string) => void) {
    const generation = this.generation;
    const identity = this.identity;
    const started = this.options.monotonic();
    this.candidate = token;
    this.notify('resolving');
    try {
      const response = await this.options.resolve(token);
      if (generation !== this.generation || this.checkIdentity() || identity !== this.identity)
        return;
      if (!validResponse(response)) {
        this.clear('unavailable');
        return;
      }
      this.deadline = started + Date.parse(response.expires_at) - Date.parse(response.observed_at);
      if (this.deadline <= this.options.monotonic()) {
        this.clear('unavailable');
        return;
      }
      this.active = { version: 1, token, expires_at: response.expires_at };
      this.storage((storage) => storage.setItem(REFERRAL_KEY, JSON.stringify(this.active)));
      this.notify('active');
      this.scheduleExpiry();
      redirect?.(response.product_id);
    } catch {
      if (generation === this.generation && !this.checkIdentity()) this.clear('unavailable');
    }
  }
  private scheduleExpiry() {
    clearTimeout(this.timer);
    const remaining = this.deadline - this.options.monotonic();
    if (remaining <= 0) {
      this.reset();
      return;
    }
    this.timer = setTimeout(() => this.scheduleExpiry(), Math.min(remaining, 2147483647));
  }
  tokenForOrder(): string | undefined {
    if (this.checkIdentity() || !this.options.enabled() || !this.active) return undefined;
    if (
      this.deadline <= this.options.monotonic() ||
      Date.parse(this.active.expires_at) <= this.options.wall()
    ) {
      this.reset();
      return undefined;
    }
    if (!this.memoryOnly) {
      const record = this.read();
      if (
        !this.memoryOnly &&
        (!record ||
          record.token !== this.active.token ||
          record.expires_at !== this.active.expires_at)
      ) {
        void this.storageChanged();
        return undefined;
      }
    }
    return this.active.token;
  }
}
