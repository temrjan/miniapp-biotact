/* Current client/hook source, synthetic hook state, real gate HTML response. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const ts = require(path.join(root, 'packages/frontend/node_modules/typescript'));
const port = process.argv[2];
const base = `http://127.0.0.1:${port}/api/v1`;
let resets = 0, payments = 0, requests = 0, mode = 423;
const pending = { creationUnknown: false, pending: null,
  forgetOrder() { this.pending = null; this.creationUnknown = false; },
  rememberOrder(id) { this.pending = { orderId: id }; this.creationUnknown = false; },
  markCreationUnknown() { this.creationUnknown = true; },
};
const store = (selector) => selector(pending);
store.getState = () => pending;
const webApp = { initData: 'synthetic', openLink() { payments += 1; } };
let fetches = [];
const modules = {
  '@twa-dev/sdk': { default: webApp },
  '@/employeeReferrals/authBoundary': { referralAuthRevision: () => 1 },
  '@/stores/languageStore': { useLanguageStore: { getState: () => ({ language: 'ru' }) } },
  '@/utils/constants': { API_BASE_URL: base, API_TIMEOUT_MS: 5000, ROUTES: { PAYMENT_SUCCESS: '/success' } },
  '@stores/pendingOrderStore': { usePendingOrderStore: store },
  'react': { useCallback: (fn) => fn, useLayoutEffect: (fn) => fn(), useRef: (v) => ({ current: v }), useState: (v) => [v, () => {}] },
  '@tanstack/react-query': { useQueryClient: () => ({ invalidateQueries: () => {} }) },
  'react-router-dom': { useNavigate: () => () => {} },
  '@hooks/useTelegram': { useHaptic: () => ({ success() {}, error() {} }) },
  '@hooks/useLoyalty': { LOYALTY_ME_KEY: ['fixture'] },
  '@/i18n': { useTranslation: () => ({ t: (s) => s }) },
  '@/employeeReferrals/runtime': { referralContext: { tokenForOrder: () => undefined }, isReferralEntryEnabled: () => false },
  '@/utils/size': { needsSizeChoice: () => false },
};
function load(relative) {
  const filename = path.join(root, 'packages/frontend/src', relative);
  const source = fs.readFileSync(filename, 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  const module = { exports: {} };
  const context = { module, exports: module.exports, require: (name) => { assert.ok(name in modules, `Unexpected import ${name}`); return modules[name]; },
    AbortController, Event, Error, setTimeout, clearTimeout, console,
    window: { location: { origin: base }, dispatchEvent: () => { resets += 1; }, confirm: () => true }, alert() {},
    fetch: async (url, config) => {
      fetches.push([url, config.method]);
      if (url.endsWith('/orders/quote')) return new Response(JSON.stringify({ total: '30000', quote_fingerprint: 'fixture' }), { status: 200 });
      if (url.endsWith('/payments/create')) { payments += 1; throw new Error('Payment must not start after refusal'); }
      if (url.endsWith('/orders')) {
        requests += 1;
        if (mode !== 423) return new Response('{}', { status: mode });
      }
      return fetch(url, { ...config, headers: { ...config.headers, Host: 'app.biotact.uz' } });
    },
  };
  vm.runInNewContext(code, context, { filename });
  return module.exports;
}
(async () => {
  modules['@/utils/order'] = load('utils/order.ts');
  modules['@/api/client'] = load('api/client.ts');
  const { usePayment } = load('hooks/usePayment.ts');
  for (const [sized, points] of [[false, 0], [true, 0], [false, 30000], [true, 30000]]) {
    pending.forgetOrder(); requests = 0; payments = 0; resets = 0;
    const pay = usePayment({ items: [{ productId: '7', quantity: 1, ...(sized ? { size: 'L' } : {}), product: { id: '7', price: 30000, requiresSize: sized } }], deliveryAddress: 'fixture', name: 'fixture', phone: '000', redeemPoints: points, displayedTotal: 30000, onQuote() {}, onInvalidAmount() {} });
    await pay.pay();
    assert.equal(requests, 1);
    assert.equal(payments, 0);
    assert.equal(resets, 0);
    assert.equal(pending.creationUnknown, false);
    assert.equal(pending.pending, null);
  }
  // Positive controls prove that auth-reset and uncertainty channels are observable.
  mode = 401;
  await modules['@/api/client'].api.post('/orders', {}).catch(() => {});
  assert.equal(resets, 1);
  mode = 503;
  pending.forgetOrder();
  await usePayment({ items: [{ productId: '7', quantity: 1, size: 'L', product: { id: '7', price: 30000, requiresSize: true } }], deliveryAddress: 'fixture', phone: '000', name: 'fixture', redeemPoints: 0, displayedTotal: 30000, onQuote() {}, onInvalidAmount() {} }).pay();
  assert.equal(pending.creationUnknown, true);
  console.log('CLIENT_CURRENT_SOURCE_REAL_423_NO_PAYMENT_NO_AUTH_RESET_FENCE_CLEARED; 401/503 controls passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
