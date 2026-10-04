import WebApp from '@twa-dev/sdk';
import { api } from '@/api/client';
import { ReferralContext } from './context';

export const isReferralEntryEnabled = () => import.meta.env.VITE_EMPLOYEE_REFERRAL_ENTRY === 'true';
export const referralContext = new ReferralContext({
  enabled: isReferralEntryEnabled,
  resolve: (token) =>
    api.post<unknown>(
      '/employee-referrals/resolve',
      { token },
      { redirect: 'error', cache: 'no-store' }
    ),
  storage: () => window.localStorage,
  monotonic: () => performance.now(),
  wall: () => Date.now(),
  // Used only to notice credential switches; never persisted or sent to diagnostics.
  identity: () => WebApp.initData,
});

export interface ReferralEntry {
  values: string[];
  location: string;
}
declare global {
  interface Window {
    consumeBiotactReferral?: () => ReferralEntry | null;
  }
}
let bootstrapConsumed = false;
export function consumeBootstrap(): ReferralEntry | null {
  if (bootstrapConsumed) return null;
  bootstrapConsumed = true;
  const entry = window.consumeBiotactReferral?.() ?? null;
  delete window.consumeBiotactReferral;
  return entry;
}

export function cleanEntryURL(): ReferralEntry | null {
  const url = new URL(window.location.href);
  if (!url.searchParams.has('ref')) return null;
  const values = url.searchParams.getAll('ref');
  url.searchParams.delete('ref');
  const location = url.pathname + url.search + url.hash;
  window.history.replaceState(window.history.state, '', location);
  return { values, location };
}
