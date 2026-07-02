import { useLoyalty } from '@hooks/useLoyalty';
import { LoyaltyCard } from './LoyaltyCard';
import { JoinCard } from './JoinCard';

// Profile loyalty slot. Hidden while the program is dark (endpoint 404 → no data). Once live,
// a member sees their loyalty card; a non-member sees the direct-join CTA (data.isMember swap).
export function LoyaltySection() {
  const { data } = useLoyalty();

  if (!data) {
    return null; // dark (404) / loading / error — the profile must keep working
  }

  return data.isMember ? <LoyaltyCard /> : <JoinCard />;
}
