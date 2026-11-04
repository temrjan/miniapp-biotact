"""Consultant journal (spec.opyt r2 §4): every consultant turn that passed the SENSITIVE
filter, stored in `consultant_conversation` and `consultant_turn`. It lives outside
`src/consultant/`, so the knowledge PROFILE does not change (JN-01, JN-03).

Switch (JG-01, JG-02). `CONSULTANT_JOURNAL=true` plus an API restart turns the journal on;
it stays off while `DATABASE_ECHO` is on. The Captain turns it on only when all hold: the
website no longer says the context is kept only in the tab (PR-2 live), PR-4 is live, and
this code is deployed with its migration applied. No website text is added for it.

Retention: none. Removal of one conversation on request (JD-01) is a manual procedure, run
by the Captain or on his word with production database access:
1. Identify the conversation from what the requester gives (time and text of the messages),
   for example by `created_at` and `question` in `consultant_turn`.
2. `DELETE FROM consultant_conversation WHERE id = :id` (its turns are deleted with it).
3. A turn without a conversation (`conversation_id` NULL) is removed by its own `id`:
   `DELETE FROM consultant_turn WHERE id = :id`.
No product path reads or exposes the journal."""
