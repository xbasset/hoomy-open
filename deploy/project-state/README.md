# Central cited-state operations

The central store is authoritative for approved context, immutable visible-message
projections, explicitly queued state jobs, usage ledgers and exact cached citations.
Approved Mac/VPS collectors and the remote Codex plugin operate independently of
the retired local archive writer. A source/repository verification module and
Act/Learn research scaffolds are preserved separately; cited state does not make
conversation reports current verified facts.

`compose.state.yaml` adds one explicit `HOOMY_STATE_PROJECT_ID` to both central
services. Empty disables execution; the same approved canonical UUID must be
selected on gateway and backend. Collection's project setting and per-machine
policies remain independent. Existing native grants cannot silently acquire the
state refresh or execute scopes.

The [executor runbook](EXECUTOR.md) covers project-data-only additive migration,
explicit native connection consent, manual jobs, prepare-only Mac watch units,
interruption and rollback. A project-specific watcher consumes owner-queued work;
it never refreshes, creates retries or calls a model while idle. Its installed
server namespace remains `hoomy_remote_acceptance`. Verify the actual installed
connection's server origin and central project profile before starting it. The
local Hoomy connection is not a fallback.

Reconciliation of an old local archive must preserve project IDs, approved context
and existing source versions without replacing newer central rows or importing
raw legacy evidence as approved synthesis input. The archive authority guard
rejects writes and legacy jobs. Re-enabling a local writer is a separate operator
decision after reconciling all newer central edits; it does not change collection
consent or convert authentication credentials.

Current contracts are [state design](../../docs/architecture/project-state.md)
and [state API](../../docs/architecture/project-state-api.md). Comparative method
measurements, exact-quote rejection, partial device coverage, reader-quality
exclusions and watcher startup failures remain in
[method research](../../docs/research/project-state-bounded-method.md).
This source preparation makes no fresh model call or runtime acceptance claim.
