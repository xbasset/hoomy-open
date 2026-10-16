# Hoomy authenticated gateway

Authenticated gateway for one private workspace. GitHub verifies identity; the
maintained `oidc-provider` authorization server issues Hoomy's independent,
opaque resource-scoped grants. The private Python backend stays on loopback.
Public routes never proxy arbitrary backend paths, worker packets or reset APIs.

## Runtime

Use Node 24 (minimum 22.16 for built-in SQLite). Install with `npm ci
--ignore-scripts`; run `npm start`. This process is an HTTPS origin behind a
trusted reverse proxy; it does not terminate TLS itself.

Required configuration:

| Environment variable | Value |
| --- | --- |
| `HOOMY_AUTH_DB` | Absolute path outside a checkout; auth database and signing key persist here |
| `HOOMY_GITHUB_CLIENT_ID` | GitHub App OAuth client ID |
| `HOOMY_GITHUB_CLIENT_SECRET` | GitHub App client secret from protected server configuration |
| `HOOMY_ALLOWED_GITHUB_IDS` | Comma-separated stable numeric IDs; no open signup |
| `HOOMY_COOKIE_KEYS` | JSON array of secrets with at least 32 characters each, newest first |
| `HOOMY_PUBLIC_ORIGIN` | Canonical HTTPS origin; default `https://my.hoomy.dev` |
| `HOOMY_PILOT_URL` | Loopback Python API; default `http://127.0.0.1:8765` |
| `HOOMY_GATEWAY_PORT` | Listening port; default `8780` |
| `HOOMY_TRUSTED_PROXY_IPS` | Exact reverse-proxy peer IP addresses, comma-separated; loopback is also trusted |
| `HOOMY_OAUTH_CLIENTS` | Optional JSON array of preconfigured public clients with exact callbacks |
| `HOOMY_AUTH_OUTCOME_DIAGNOSTICS` | Optional `1` enables bounded token outcome diagnostics; disabled by default |
| `HOOMY_INGEST_PROJECT_ID` | Optional single canonical pilot project ID; empty disables conversation collection |

Token diagnostics emit at most 60 outcomes per minute: a refresh-attempt
boolean, a fixed client-type category, HTTP status and an allowlisted OAuth
error code. They do not record bodies, tokens, client IDs, URLs, scopes, headers
or raw errors. Disable the option after diagnosing a live renewal issue.
For `invalid_scope`, diagnostics also include membership booleans for the
fixed public scope names and an unknown-scope flag; raw scope text is excluded.

The proxy must replace incoming `X-Forwarded-Proto` and preserve the canonical
Host. Requests with another Host or without trusted HTTPS are rejected. Keep
the auth state directory accessible only to the service user. Database backups
contain OAuth secrets and the signing key and require the same protection.
The SQLite database file has mode 0600; a new state directory has mode 0700.

GitHub callback: `/auth/github/callback`. State and S256 PKCE bind the callback
to an expiring server-side transaction and the initiating HttpOnly browser
cookie. Only allowed numeric GitHub IDs become members. Provider tokens are
used to retrieve identity and are not retained or sent to agents.

An explicitly requested `offline_access` marker with no `prompt` parameter
requires the provider's real consent prompt. The consent page explains ongoing,
revocable access. Explicit prompts, including `none`, are preserved. MCP access
tokens still contain only the granted project permissions. Native pilot grants
issued before this correction lack the offline marker: requesting that marker
during refresh requires a fresh explicit offline consent, not an automatic
widening of their existing grant. Clients can otherwise refresh using only
their already granted scopes.

## Authenticated routes

Discovery is `/.well-known/hoomy`, the issuer's standard metadata and protected
resource metadata at `/.well-known/oauth-protected-resource/mcp`,
`.../v1/projects` and `.../v1/collector`.

- `/mcp`: remote user tools and embedded UI through the existing host bridge.
- `/v1/projects`: active project ID/name summaries using only the lightweight
  private `/api/projects` route; no full-state reconstruction or source bodies.
- `/v1/projects/{id}` and `/v1/projects/{id}/context`: project API with preserved
  pilot revision conflict status and payload.
- `/v1/enrolments`: bounded metadata-policy proposal, expiring secret ticket.
- `/oauth/device_authorization`, `/activate`, `/oauth/token`: standard device
  authorization with phone/browser confirmation and explicit machine consent.
- `/v1/devices/self`, `/activate`, `/heartbeat`: persistent machine status.
- `/devices`: browser device list and CSRF-protected revocation.
- `/v1/devices`, `/v1/devices/{id}/revoke`: user project-API grant with
  `devices:manage`; collector grants cannot administer devices.
- `/oauth/revoke`: standards-based revocation of the caller's grant family.

The collector resource is an identifier, `/v1/collector`, not a blanket API
proxy. Only the built-in collector client can receive this audience. Collector
presence-only access grants `device:self`. Existing grants never acquire
ingestion permission implicitly. A successful heartbeat does not imply
uploaded or analyzed data.

## Conversation ingestion

The shared wire contract is [conversation-ingestion-contract.md](../../docs/architecture/conversation-ingestion-contract.md).
`HOOMY_INGEST_PROJECT_ID` enables only that canonical project, and remains empty
by default. An existing collector uses its `device:self` authorization to
propose `{proposed_policy: policy}` at `POST /v1/devices/self/policy-proposals`.
The response contains an expiring enrolment ticket and the next policy revision.
It does not modify the existing grant or policy. A separate device authorization
requests exactly `device:self evidence:ingest`; the same phone browser reviews
the server-resolved project name, working roots, history roots, exclusions and
visible conversation tier before approving. Refusal or expiry preserves the
prior presence grant. Approval replaces only that installation's grant. A
changed or revoked previous grant makes its pending proposal invalid.

`conversation_visible_v1` policies contain one project binding with positive,
normalized absolute working roots and explicit `.codex/sessions` or
`.codex/archived_sessions` roots. Parent credential directories are rejected.
The server checks the current configured project, policy revision, device and
grant on every ingestion operation. Remote path checks are lexical provenance
checks; the installed collector is responsible for refusing local symlinks and
changed files. The gateway cannot infer original source channels from redacted
text; only the approved collector's visible-message projection is transferred.

`POST /v1/events` requires `evidence:ingest` and an `Idempotency-Key` equal to the
event UUID. It accepts a closed JSON schema, rejects duplicate keys, unsafe
integers, invalid UTF-8 and unpaired Unicode surrogates, and verifies canonical
projection SHA-256. Original file SHA-256 is declared provenance and is not
centrally verified. No original JSONL bytes are accepted. Events carry bounded
omission counters, including `excluded_oversized_records`; omitted records are
not silently presented as complete coverage.

The separate `conversation.source_excluded` variant carries only the common
source identity, current policy, sequence and an exclusion record. Its fixed
reason is `internal_approval_review`, with `legacy_subagent_guardian` or
`internal_guardian` metadata discrimination and a declared header SHA-256.
It accepts no title, projection, coverage or full-source hash. The header digest
is agent-declared provenance, not a centrally verified classification proof.
The same grant, project and path checks apply. A stored receipt instead contains
`event_type` and `header_sha256`, with no `projected_sha256`; receipt retrieval
retains this exact variant. The backend preserves prior receipts and immutable
records while excluding the classified source from normal evidence reads.

The transport is uncompressed JSON, capped at 24 MiB; projection is capped at
18 MiB, 10,000 messages and 256 KiB UTF-8 per message. The collector streams
original sources up to 256 MiB with a bounded record buffer; original source
size is enforced locally. Authenticated uploads are bounded to 20 per device
per minute and two concurrent uploads in the gateway. The private fixed
`POST /api/ingestion/events` receives only the verified principal, approved
policy and event. Public 202 requires the backend's `stored` receipt with
matching event and projection or declared header hashes after its transaction commits. A missing
or inconsistent confirmation is an error, never an acknowledgement.

`GET /v1/receipts/{eventUUID}` uses a fixed private receipt lookup with verified
account/device identity. Revoked grants and stale policies cannot query receipts.
`GET /v1/projects/{id}/evidence?source_id=remote:...&offset=0&limit=20` requires
a user project-resource token with `projects:read`, not collector credentials.
It forwards a fixed cached-evidence query with the authenticated account and
workspace, limit 1–50 and offset 0–10,000. No local paths are opened and no model
is invoked. Discovery advertises the endpoints; per-device `collection_available`
is true only for a current approved ingestion policy and scope.

Isolated tests cover real provider approval, denial, expiry, scope escalation,
stale authorization, revocation and receipt ownership. A public gateway/real
Python fixture verifies canonical Unicode hashes, durable duplicate receipts
after both processes restart, independent locations and unchanged approved
context revisions. These fixture checks are not production ingestion evidence;
live rollout requires the operator's deployment and receipt checks.

Successful project edits record the authenticated account/client, project,
revision transition and field names in the gateway database. Field values are
not copied into this audit record. The gateway and pilot databases are separate,
so a process failure between the project update and the audit write is not a
cross-database transaction; rigorous transactional audit belongs in the domain
store before broad deployment. A synchronous audit failure preserves the
confirmed project update and returns an `audit_unavailable` warning, rather
than suggesting that the caller should repeat an already committed edit.

The remote plugin uses `projects:read` and `projects:context:write`. The project
API is a separate resource; an MCP token cannot be replayed against it. User
clients and the collector cannot swap audiences or privileges. The local
headless user broker and repository synchronization remain later work.

## Client registration and lifetime

Project-state synthesis uses the independent single-project operator gate and
separate `projects:state:refresh` / `projects:state:execute` user permissions.
Current refresh requires the displayed method hash; historical v1/v2 jobs remain readable
and incompatible with execution. See [the public/private state contract](../../docs/architecture/project-state-api.md)
for closed requests, exact native envelope limits and immutable retry semantics.

`/oauth/register` implements constrained Dynamic Client Registration for Codex
public clients. Accept exact HTTPS or HTTP loopback callbacks, authorization
code plus refresh grants, and user scopes only. S256 PKCE is mandatory. Remote
JWKS, sector identifiers and request-URI metadata are rejected before library
processing, preventing registration-driven metadata fetches. Registrations
persist and are capped at 1,000 for the private pilot.

Access tokens last 15 minutes, device codes and interactions 10 minutes,
refresh tokens and grants 90 days, browser sessions 7 days. The provider
rotates refresh tokens and detects reuse; clients must serialize refresh and
persist the replacement atomically. Current device status, grant availability
and the configured account allowlist are checked on every protected operation.
Explicit machine revocation does not revoke other devices or plugin grants.
Re-enrolling the same installation replaces its previous grant, without
creating duplicate devices.

Public route quotas are persisted per peer IP/path/minute: enrolment 5,
registration and device authorization 10, browser login 20, authorization 30,
token exchange 60, activation page 30 and activation-code attempts 20. Expired temporary records and OAuth objects are cleaned
at startup and every minute. In production the reverse proxy should also
limit request concurrency and connection lifetime. Public registration
capacity and signing-key rotation need an operational procedure before
broadening beyond the private pilot.

## Verification and rollout limit

`npm test` runs real library OAuth flows against isolated loopback servers,
with fake GitHub identity exchange and private-project API stubs. It covers
explicit device approval/denial/expiry, PKCE, resource/scope boundaries,
rotation/reuse, immediate machine revocation, browser state/CSRF and preserved
revision conflicts. No external provider login or production tokens are used.
The HTTPS navigation regression uses Python 3's standard cookie jar and
OpenSSL to generate a fixture certificate trusted by that client through
normal TLS validation. It checks Secure/path cookie behavior, form navigation
and two stale verification tabs without losing the active consent. This is
not a real-browser rendering test; no browser certificate exceptions or
system trust-store changes are used.

Device consent completes when the same browser follows the provider's `303`
resume redirect. Holding that redirect leaves the terminal polling with
`authorization_pending`, even after Hoomy records the user's approval. The
provider's `inFlight` state rejects reuse of that public code in another
browser before expiry. A phone flow must stay in the phone browser from the
initial verification URL through code confirmation, identity, consent and
the success page. An abandoned or expired request needs a fresh terminal
login and explicit approval. The interrupted-redirect regression verifies
these provider transitions; browser-specific return-page behavior remains
part of live acceptance.

Private integration checks exercised public TLS, the real GitHub callback,
native cached read/edit and natural-expiry renewal with explicit offline consent.
These historical observations do not replace installation-specific checks.
See [central deployment](../../deploy/central/README.md) and the authored
[collection/authentication lessons](../../docs/research/collection-and-authentication-lessons.md).

## Project-state synthesis

Project-state generation has an independent disabled-by-default
`HOOMY_STATE_PROJECT_ID` setting, configured on both gateway and private backend.
It does not reuse the ingestion allowlist or alter collector grants. Only the
configured canonical project can create or execute a state job; cached reads
remain available with `enabled:false` elsewhere.

User permissions `projects:state:refresh` (explicit job creation) and
`projects:state:execute` (leased worker transport) require new concrete consent
through the existing native OAuth connection. Existing tokens are not expanded.
State reads, job status and citations retain `projects:read`. No model operation
runs from a profile read, collector heartbeat or ingestion acknowledgement.

See the [closed API contract](../../docs/architecture/project-state-api.md) for
fixed public/private routes, paired failed-call accounting, request bounds and
lease/budget limits. Isolated tests exercise public facades against the real
Python backend with fictional two-device evidence, exact citations, idempotent
completion and post-publication exclusion. Live generation remains a separate
operator-enabled acceptance step.

The private bounded-method experiments recorded failed and successful trials,
including known-usage failures, a technically ready view with one-device citations,
and a later two-device view with transparent quality exclusions. Their aggregate
comparisons and limitations are retained in
[bounded-method research](../../docs/research/project-state-bounded-method.md).
No historical raw conversation or operator payload is an installation input.
Current source-backed reads and inference availability are separate: an enabled
project still needs user consent, current method identity and a compatible worker.
