# MailAssist implementation review

Reviewed 2026-10-20. This is a source and isolated-test review, not a certification of every runtime behavior.

## Current status

All 20 numbered findings now have implemented resolutions. Original findings are retained below for traceability. Live Gmail/Gemini acceptance, billing prices, and browser rendering have not been verified by this review.

## Validation and scope

- Initial audit suite: **114 passed**. See the final verification section for the expanded suite.
- Frontend: TypeScript and Vite production build passed; oxlint passed.
- Additional reproductions used a throwaway database and fake Gmail/Gemini providers. No real mail was sent or production data changed.
- Reviewed authentication/OAuth, sync, triage, drafting, sending, learning, knowledge, settings, usage, demo data, and evaluation scripts.
- No browser interaction or live Gmail/Gemini round trip was performed. Model quality, actual permissions, deployed configuration, and current model pricing were not verified.
- No application fixes were made during the initial audit. Subsequent fixes are noted under individual findings.

## High priority

### 1. Auto-send bypasses the no-reply decision

**Fixed after review:** auto-send now requires `needs_reply`, reruns the bulk/sender/category exclusions, and checks the latest thread sender before drafting and immediately before sending. Unknown thread state skips unattended sending. Regression coverage includes excluded mail, answered/unknown threads, replies arriving during drafting, and provider lookup failures. The finding below describes the original implementation.

`backend/app/services/autosend.py:50` checks settings, attention, and category, but ignores `needs_reply`, bulk headers, no-reply addresses, and already-answered thread state. Sync calls it on all freshly stored messages, including those excluded from drafting.

**Reproduced:** a routine bulk message from `noreply@example.com`, explicitly marked `needs_reply=False`, was sent a reply by the fake provider.

Require the reply decision before auto-send and recheck thread state immediately before unattended sending. Add integration coverage for every no-reply case.

### 2. Automatic drafting and sending leave a second sendable reply

**Fixed after review:** auto-send no longer generates replies. Auto-draft marks new background drafts as eligible; auto-send sends that same pending row and marks it sent. Manual/legacy drafts default to ineligible. User edits/status changes revoke eligibility permanently; opening an eligible draft editor also requests manual ownership. Sync considers existing drafts even with auto-drafting disabled and no new messages. Multiple draft versions require manual review. The finding below describes the original implementation. Concurrent send/edit coordination remains a separate finding (6).

`backend/app/routers/emails.py` drafts first, then calls auto-send, which independently generates another draft.

**Reproduced:** the same email ended with one `pending` draft and one `sent` draft. The pending copy remains sendable from the queue.

Use one coordinated lifecycle: reuse or retire the existing draft when auto-sending, and avoid generating twice.

### 3. Regeneration retains earlier sendable versions

**Fixed after review:** successful regeneration marks previous pending/approved/rejected versions `superseded` in the same transaction as the new draft. Failed generation preserves the current version. Superseded drafts cannot be edited, reapproved, or sent; bulk send excludes them. The queue exposes a read-only Previous versions tab, and sent history remains intact. The finding below describes the original implementation.

`backend/app/routers/drafts.py:103` inserts a new pending draft on every generation without retiring prior pending/approved versions. Email detail shows the newest, but the queue still exposes older versions. Bulk sending can therefore send multiple versions of one reply.

**Reproduced:** generating twice left the previous version pending.

Define superseded versions explicitly, or update the active draft while retaining separate version history.

### 4. Network errors can leave sync stuck indefinitely

**Fixed after review:** polling now handles its own failures, retries up to four consecutive attempts, imposes request and overall checking timeouts, and releases busy state when checking fails. Polling cancels on logout/unmount and ignores late responses; only successful jobs update the last-success timestamp. Six focused polling regression checks cover recovery, terminal failures, cancellation, and deadlines. The finding below describes the original implementation.

`frontend/src/App.tsx:195` defines an asynchronous polling function, invokes it without awaiting it, and schedules later calls without local error handling. A rejected status request bypasses the surrounding catch; `syncing` remains true and prevents further syncing.

Handle errors inside the polling loop, support bounded retries/cancellation, and always release the busy state. Only update the last-successful-sync timestamp for successful jobs; currently error jobs update it too.

### 5. Reading mail can overwrite unsaved edits or show another email's draft

**Fixed after review:** email detail is keyed by email ID, same-email metadata refreshes do not reload/reset draft state, and draft loading ignores responses after cleanup. Loading blocks generation until the initial draft lookup completes. Old editor operations cannot populate another email's editor, and stale operation errors are suppressed after unmount. Two state/effect callback tests cover unsaved text preservation and discarded late draft loads. No browser/React scheduler integration test was performed. The finding below describes the original implementation.

`frontend/src/components/EmailDetail.tsx:27` resets draft text whenever the entire email object changes. Background refresh replaces objects even for the same email, clearing in-progress edits. Draft requests also have no cancellation or identity guard; opening A then B can allow A's late response to populate B's draft editor. Generation and summary responses have similar stale-response risks.

Tie loading to email identity, protect unsaved content, and ignore responses for a no-longer-active email.

### 6. Sending lacks an atomic claim and durable reconciliation

**Resolution:** Fixed: all send paths share a durable per-email claim and conditional draft transition before provider delivery. Competing sends/edits are rejected. Provider failures enter send_unknown and process interruptions remain sending, both non-retryable without explicit user confirmation after checking Sent mail. Active sending cannot be resolved for 15 minutes. Successful provider IDs are stored; completion is committed per message. Reconciliation is manual because Gmail acceptance cannot be transactionally rolled back or inferred safely from a timeout.

The text below records the original finding.

The single-send and bulk-send paths read draft status, perform an external send, then persist `sent`. Concurrent requests can both pass the status check. A process failure or database failure after Gmail accepts a message can leave a draft sendable. Bulk-send defers the final commit until after the entire batch.

This is a code-path finding, not a concurrency reproduction. Add an atomic sending state and durable attempt/provider-message tracking, with reconciliation for uncertain outcomes. Do not assume a database transaction can roll back a Gmail send.

## Functional correctness

### 7. Mailbox lists and statistics silently truncate

**Resolution:** Fixed: validated limit/offset pagination is available for emails, drafts, and edit history. Frontend loading follows every page, so inbox counts/Insights no longer stop at 50. Queue metadata fetches missing/archived messages by ID; opening a queue item clears search and selects its correct folder. This eagerly loads pages, suitable for the current local app; large-mailbox virtualization/aggregated count endpoints remain future scaling work.

The text below records the original finding.

`backend/app/routers/emails.py:292` defaults to 50 rows. The frontend exposes no pagination and uses the same request for sidebar totals, insights, and queue email lookup.

**Reproduced:** 61 stored messages yielded 50 through the default API. Queue items for archived messages also lack metadata because the lookup only requests unarchived mail. Draft lists independently cap at 100 without a paging interface.

Implement pagination and independent aggregate counts; fetch queue message metadata independently of the inbox page. Resolve a queue-open target directly by ID, including archived mail, instead of searching the current inbox list.

### 8. Successful sends do not clear Needs reply

**Resolution:** Fixed: shared delivery completion clears Needs reply for messages in the answered thread up to the selected message, preserves newer arrivals, and supersedes other unsent versions of the same reply. Explicit confirmed-sent reconciliation follows the same completion path.

The text below records the original finding.

Single-send, bulk-send, and auto-send do not update `EmailMessage.needs_reply`. Sync skips known messages, so the flag can remain stale even on subsequent syncs.

**Reproduced:** a successfully sent reply left `needs_reply=True` on its email.

Update reply state after sending and define it at thread level so multiple messages from an answered thread do not continue inviting replies.

### 9. Classification failures become permanent imported state

**Resolution:** Fixed: each sync retries stored messages with an empty category before considering automatic drafts. Failed retries remain retryable; automatic drafting excludes unclassified and already-claimed messages.

The text below records the original finding.

`backend/app/routers/emails.py:131` records a failure reason but still stores the message and advances the cursor. Future syncs skip known messages. There is no application retry/reclassification flow for these rows. An empty category also passes the reply-decision exclusions and can enter automatic drafting.

Track enrichment status separately, retry failed classification, and avoid automatic drafting until classification succeeds or the user requests it.

### 10. Full-fetch cursor capture can skip arriving messages

**Resolution:** Fixed: full-fetch sync captures the history cursor before taking the inbox snapshot, so arrivals during the snapshot are picked up by the next incremental sync. The initial import remains intentionally capped by sync_max_emails.

The text below records the original finding.

`backend/app/routers/emails.py:73` captures the history cursor after fetching the recent inbox snapshot. A message arriving between those calls can be absent from the snapshot yet older than the saved cursor, so incremental sync never requests it.

Capture the cursor before the initial snapshot, then consume changes from that point with deduplication. Initial import is also intentionally capped by `sync_max_emails`; the UI should explain that it is not the full historical mailbox.

### 11. Default tone changes do not reach manual drafting

**Resolution:** Fixed: saving settings publishes the current tone to App. The editor follows default-tone changes until the user explicitly chooses a different tone.

The text below records the original finding.

`frontend/src/App.tsx:152` loads default tone on login/user change, with no update callback from Settings. EmailDetail initializes tone once and does not follow updated defaults. Saving Friendly can therefore leave manual drafts explicitly requesting Professional until a reload.

Share saved preferences with the app and initialize each newly opened email from the current default unless the user has explicitly overridden it.

### 12. Settings can lose changes during in-app navigation

**Resolution:** Fixed: Settings remains mounted while navigating within the signed-in app, retaining unsaved changes and its save bar. Browser-unload protection remains. Learning actions are disabled while the manual guide differs from the saved baseline, requiring save/discard first.

The text below records the original finding.

`frontend/src/components/SettingsPage.tsx:135` only guards browser unload. Clicking Inbox, Insights, or Draft queue unmounts Settings without saving or warning. The new settings implementation has this gap too. Learning also replaces the style profile immediately, including an unsaved manual style-guide edit.

Preserve a settings draft outside the unmounted component or guard internal navigation; explicitly handle replacement of an edited style guide.

### 13. Learning examples can represent unsent/rejected wording

**Resolution:** Fixed by making the semantics accurate: these are saved edits, not necessarily sent replies. The UI says Your saved edit and the refinement prompt explicitly states that edits may be unsent and edited facts are not verified. This preserves the feature of learning from deliberately saved wording without falsely claiming it was sent.

The text below records the original finding.

`backend/app/routers/drafts.py:175` creates learning examples on saving content, before sending. Such examples can be applied even if the draft is later rejected. The UI labels the edited side `You sent`, and the style prompt describes it as actually sent.

**Reproduced:** saving an edit without sending created a learnable example. Either restrict learning to sent drafts or accurately distinguish saved edits from sent replies throughout the UI and prompt.

### 14. Multiple-mailbox selection differs between frontend and backend

**Resolution:** Fixed: account listing and backend selection both sort newest-connected first; reconnecting refreshes that selection timestamp. The first non-demo account shown in the UI is therefore the account used for sync/style learning.

The text below records the original finding.

The frontend chooses the first non-demo account (`App.tsx:148`); `services/registry.py` chooses the newest. Reconnecting an older mailbox only updates credentials and does not make it newest. The displayed/reconnected mailbox can differ from the one being synced or used for style learning.

Persist an explicit active account and use it consistently, or enforce a single connected mailbox.

### 15. Logout does not fully reset or cancel session work

**Resolution:** Fixed: logout resets mailbox counts, filters, search, view, demo state, preferences, sync state and notices. API requests are aborted and tagged by session version, including body parsing and multi-page loads. Late session responses are rejected; inbox requests also reject superseded search results.

The text below records the original finding.

`frontend/src/App.tsx:301` clears only some state. Counts, view, search/filter, demo state, sync state, and sync polling can survive until another login. In-flight old-user requests can also complete after account switching and write stale data into the new session's UI.

Cancel session requests and timers, scope responses to a session generation, and reset all user-specific state on logout.

### 16. Long passwords cause a server error

**Resolution:** Fixed: registration validates the 72-byte bcrypt limit with a useful HTTP 400, including multibyte input. Existing bcrypt hashes continue to work.

The text below records the original finding.

Registration checks a minimum length but passes arbitrary UTF-8 input directly to bcrypt (`backend/app/security.py:21`).

**Reproduced:** registering with a 73-byte password returned HTTP 500 in the installed environment.

Validate the supported byte length with a useful client error or use a password-hashing scheme that supports the intended input range. Include multibyte input tests.

### 17. Demo cleanup can remove real knowledge entries

**Resolution:** Fixed: seeded knowledge and seeded profile/signature fields have explicit provenance flags. Cleanup removes only unchanged seeded content and preserves real entries with matching titles/values and customized seed content. Legacy untagged rows are conservatively preserved.

The text below records the original finding.

`backend/app/routers/dev.py:285` identifies demo knowledge solely by title. It can delete a pre-existing real item with a matching title, or a demo-seeded item that the user subsequently customized.

Track seeded row identities/origin and preserve customized or independently created entries.

## Evaluation and measurement

### 18. Classification caches can silently score stale labels

**Resolution:** Fixed: cached predictions carry a hash of inputs, model/thinking settings, and classifier implementation. Scoring always joins predictions to current ground truth. Reports state completed coverage and identify missing predictions as excluded.

The text below records the original finding.

`eval/classification_eval.py:127` keys the cache by model name and example ID. Cached records include expected labels. Changing labels, email contents, prompt, or thinking settings does not invalidate those records; scoring at line 175 consumes their stored expected labels too.

Fingerprint dataset inputs and inference configuration. Score cached predictions against current ground truth, and report successful coverage alongside metrics when failures/quota limits produce a partial evaluation.

### 19. Style metrics ignore Cyrillic and the ablation changes more than style

**Resolution:** Fixed: word metrics use Unicode letters. Both arms retain the same identity, signature, tone and knowledge. A non-empty profile and a non-empty disjoint training-ID manifest are required. Users must also keep actual source emails disjoint; ID validation cannot detect renamed copies.

The text below records the original finding.

`eval/style_eval.py:46` tokenizes only `[a-zA-Z']`, making word counts and vocabulary overlap unsuitable for Macedonian/Cyrillic replies. Its personalized arm also receives the user's name while the baseline receives no settings, confounding style-profile effects with identity/sign-off differences. Missing profiles produce a warning but still permit a nominal profile comparison.

Use Unicode-aware tokenization, keep identity/signature/tone/knowledge constant across arms, require a profile for the profile comparison, and keep profile-training emails separate from evaluation replies.

### 20. Usage failures are underreported

**Resolution:** Fixed: empty/schema-invalid output records failure while retaining returned usage. Each failed retry is recorded separately and attempts share a logical operation ID. Cost reports distinguish attempts from logical operations. Unpriced models expose null/Unknown costs and an unpriced-call count; configured prices themselves have not been reverified in this implementation task.

The text below records the original finding.

`backend/app/services/llm.py` records success before checking for an empty response; malformed classification JSON also fails after a successful usage record. Retries produce one logical-operation record, not one row for every network attempt despite the current wording.

Separate transport success from usable-output success, preserve token accounting for unusable responses, and distinguish logical operations from API attempts. Unknown model prices currently become zero; expose unknown cost rather than implying a free call. This review did not verify published prices.

## Explicit scope limitations

- Read/archive operations are local by design and are not pushed to Gmail; the UI should make that boundary clear.
- Reply generation uses the selected message body, not fetched full-thread context. Thread summarization does fetch the thread.
- Templates/facts support add/delete but no editing workflow.
- OAuth failure redirects include `auth_error`, but the frontend does not display that parameter. Consent cancellation also lacks a handled callback path.
- API base URL is hardcoded to localhost; deployment requires configuration work.
- OAuth/session hardening and a full security audit were not completed. Notable source-level concerns include session tokens in OAuth-start query URLs, a development secret fallback, plaintext stored OAuth credentials, and no distinct JWT audience/type check separating OAuth state from session tokens.

## Recommended order

1. Auto-send eligibility, duplicate drafts, and send coordination.
2. Frontend request identity/cancellation, sync recovery, and unsaved-edit protection.
3. Reply-state updates, classification retries, pagination/counts, and account consistency.
4. Learning semantics, evaluation validity, and measurement accuracy before thesis results are finalized.
5. Demo provenance, validation, deployment configuration, and remaining UX/accessibility checks.

Add regression tests around the reproduced cases; passing component checks and isolated unit tests do not establish correctness of the combined sync/draft/send lifecycle.

## Final verification after fixes

- Backend: **170 tests passed**, including concurrent send requests, durable uncertain-delivery states, edit locking during delivery, answered-thread state, independent auto-draft/send behavior, classification recovery, cursor ordering, multi-page retrieval, password byte limits, active-account consistency, demo provenance, Unicode evaluation, cache invalidation, and per-attempt telemetry.
- Frontend: **12 regression checks passed**, covering multi-page retrieval, session aborts/stale response rejection, draft state/effect callbacks, saved-tone changes, and sync retry/cancellation/deadline behavior. Component callback checks do not replace browser or React scheduler integration tests.
- Production TypeScript/Vite build and oxlint passed.
- No real messages sent and no live Gmail/Gemini verification performed. No live evaluation was run or quota spent.
- Restart the backend to apply additive database columns. Previously recorded telemetry cannot be corrected retroactively; legacy untagged demo content is preserved conservatively.
- Delivery recovery is intentionally explicit: inspect Gmail Sent before confirming sent/not sent in the queue. Interrupted sending states cannot be resolved for 15 minutes. This prevents automatic retries of uncertain delivery, but does not claim transactional exactly-once delivery across Gmail and the local database.
- Style evaluation now requires a non-empty profile and its disjoint training-ID manifest, documented in eval/README.md. Verify source-email separation when preparing thesis data.
- API pagination currently eagerly retrieves all imported records for this local app. Large-mailbox virtualization and full historical Gmail backfill are outside these bug fixes.
