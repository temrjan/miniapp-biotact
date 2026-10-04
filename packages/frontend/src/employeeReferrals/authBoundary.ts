// Non-sensitive revision: an older ordinary auth failure must not retire a newer entry.
let revision = 0;
export const referralAuthRevision = () => revision;
export const invalidateReferralAuthRequests = () => {
  revision += 1;
};
