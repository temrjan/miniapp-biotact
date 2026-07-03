import { useMutation, useQueryClient } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import type { SurveyPayload, SurveyResult } from '@/types';

// Progressive survey (member-only): writes gender/age/city, idempotent for the survey_done
// event. On success the loyalty summary is invalidated (cheap, keeps the cache consistent —
// /me does not echo the survey fields).
export function useSurvey() {
  const queryClient = useQueryClient();

  return useMutation<SurveyResult, Error, SurveyPayload>({
    mutationFn: (payload: SurveyPayload) => loyaltyApi.survey(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['loyalty', 'me'] });
    },
  });
}
