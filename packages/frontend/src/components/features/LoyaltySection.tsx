import { useLoyalty } from '@hooks/useLoyalty';
import { LoyaltyCard } from './LoyaltyCard';
import { JoinCard } from './JoinCard';
import { SurveyCard } from './SurveyCard';
import { TicketsCard } from './TicketsCard';

// Profile loyalty slot. Hidden while the program is dark (endpoint 404 → no data). Once live,
// a member sees their loyalty card + the optional survey + their lottery tickets; a non-member
// sees the direct-join CTA (data.isMember swap). All three are member-only — there is nothing to
// ask, and no tickets to count, before someone has joined.
export function LoyaltySection() {
  const { data } = useLoyalty();

  if (!data) {
    return null; // dark (404) / loading / error — the profile must keep working
  }

  return data.isMember ? (
    <>
      <LoyaltyCard />
      <SurveyCard
        gender={data.gender}
        age={data.age}
        city={data.city}
        isAdult={data.isAdult}
      />
      <TicketsCard />
    </>
  ) : (
    <JoinCard />
  );
}
