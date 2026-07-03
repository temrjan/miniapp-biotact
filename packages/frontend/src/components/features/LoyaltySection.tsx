import { useLoyalty } from '@hooks/useLoyalty';
import { LoyaltyCard } from './LoyaltyCard';
import { JoinCard } from './JoinCard';
import { SurveyCard } from './SurveyCard';

// Profile loyalty slot. Hidden while the program is dark (endpoint 404 → no data). Once live,
// a member sees their loyalty card + the optional survey; a non-member sees the direct-join CTA
// (data.isMember swap). The survey is member-only — nothing to ask before someone has joined.
export function LoyaltySection() {
  const { data } = useLoyalty();

  if (!data) {
    return null; // dark (404) / loading / error — the profile must keep working
  }

  return data.isMember ? (
    <>
      <LoyaltyCard />
      <SurveyCard />
    </>
  ) : (
    <JoinCard />
  );
}
