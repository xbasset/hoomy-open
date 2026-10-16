# Collection and authentication lessons

Status: authored findings from the private multi-machine integration experiments.
These are aggregate engineering observations, not a public source dataset or a
guarantee for every Codex/version/OS combination. Runtime contracts live in the
[ingestion contract](../architecture/conversation-ingestion-contract.md) and
[authentication design](../architecture/authentication-and-device-enrolment.md).

## Shared login, separate authority

Host-owned native OAuth avoids copying provider credentials into plugins,
collectors or model input. User context editing, explicit synthesis creation,
worker execution and device collection require distinct permissions. Dynamic
registration must advertise only the user scopes appropriate to that resource;
collector scopes in generic server discovery caused a native registration
failure even though resource enforcement itself was correct.

An explicit `offline_access` request needs real consent. Issuing a renewable
token while stripping that marker produced a later `invalid_scope` refresh in
the native client. Correcting future consent did not widen existing grants.
Natural-expiry renewal was then observed without another login. Refresh rotation
must be serialized and persisted; an interrupted exchange is ambiguous and is
not safe to replay blindly.

A global `Referrer-Policy: no-referrer` made browser form Origin null and broke
strict CSRF validation. `same-origin` preserved the origin for same-origin
forms without leaking cross-origin referrers. Null/wrong origins still fail.
Fake fetch tests alone did not expose this actual form behavior.

Device Authorization is a browser transaction, not just a public code. After
code confirmation the maintained provider marks it in flight. Approval still
requires following the `303` resume redirect before terminal polling receives
tokens. Switching browsers halfway through cannot resume that same code.
Same-phone-browser navigation from verification through final success works;
stale forms need a controlled page and a fresh flow, not weaker session checks
or manual provider-record completion.

Revoking a collector grant immediately rejected an unexpired access token and
its refresh family while an independent user/device grant remained valid.
A stale active machine badge exposed a separate registry-presentation defect;
reconciliation must follow the device's current referenced grant and avoid
revoking a replacement grant accidentally. UI status is not proof of token
validity, and a heartbeat is not proof of evidence upload.

## Scope before bytes

A lexical project path in an event cannot prove what the original file held.
The collector therefore validates owner, no-follow path identity, canonical
session identity and positive approved cwd before body access. Reading the
metadata header through a buffered stream prefetched body bytes too early;
an exact unbuffered first-line gate prevents that leak. Cwd transitions and
forked metadata require the same project and already declared ancestry, not
filename resemblance or future metadata.

Large sources need bounded processing. Skipping an oversized record by a
prefix heuristic can miss a later scope-bearing field or escaped key. The
strict streaming adapter validates complete UTF-8/JSON, duplicates, typed
closed envelopes and budgets independently of key order. Unknown records or
scope paths refuse the source. Known excluded tools/reasoning/checkpoints are
not conversation evidence; their omission is explicitly partial coverage.

Internal approval reviews can resemble ordinary user conversation records.
Exact typed first-header markers support metadata-only whole-source exclusion;
`thread_source` alone and ordinary delegated work do not. Classification must
stop before body buffering and store a declared exact-header digest, not retain
sensitive headers or fake a visible conversation. A sticky exclusion hides
saved source IDs and older projected versions at the classified location while
preserving immutable receipts/blobs and human context. It does not silently
classify a different archive path.

## Durable delivery and supervision

A receipt is acknowledged only after the backend commits the event, immutable
version and ownership association. Dropping a successful HTTP acknowledgement
and restarting the client still recovered the same receipt and hash without a
second source version. Pending queued bytes remain immutable across projection
adapter upgrades; receipt mismatches do not delete the event. Backpressure stops
new capture instead of discarding unacknowledged evidence.

A dedicated Linux system service can run as the non-root operator independently
of SSH without enabling lingering for all user services. The reviewable privileged
step installs only that unit; hardened filesystem binds expose only approved
history directories and private writable state. A user-owned helper remains
administrator code at execution time and must be reviewed accordingly.

The Mac executor's first background startups failed ambiguously. Closed
pre-cleanup diagnostics established initialization timeout, not an OAuth or
model failure. A watch-only absolute startup budget and once-only restart policy
later passed an observed idle interval without inference or ledger changes.
The deeper background slowness cause remains unknown. Do not infer permanent
availability or proof of all historical ledger bytes from bounded observations.

## Remaining limits

Credential text filters are conservative, not a proof that arbitrary prose is
safe. Original-source hashes and remote paths are collector claims, whereas
canonical projected hashes are verified centrally. Exact cited text membership
is provenance, not semantic entailment or current fact verification. Token
counters are native-reported observations, not independent billing evidence.
Research publication uses authored findings and aggregates; source sessions,
private path/identity records, raw receipts and auth state remain outside it.
