# Hoomy Codex pilot

A minimal MCP App and skill around the existing local Hoomy backend. There is no
new suggestion engine, database, model configuration or API key in this package.
See [experiment 011](../../docs/experiments/011-codex-plugin-first-test.md).

## Install locally

Requires Node.js 18.17 or newer, the Codex CLI, and the existing pilot backend and
worker. Run from the repository root:

```sh
npm ci --prefix plugins/hoomy --ignore-scripts
codex plugin marketplace add "$PWD"
codex plugin add hoomy@hoomy-local
```

The repository's `.agents/plugins/marketplace.json` points to this package. Codex
copies it, including installed dependencies, into its plugin cache. After a change,
run `codex plugin add hoomy@hoomy-local` again; reload Codex if it retains the old
MCP server. Do not edit the installed copy independently.

`mcp.json` uses the portable plugin format and `${PLUGIN_ROOT}` expansion. The MCP
server is started by Codex over stdio. It connects only to the existing loopback
backend, normally `http://127.0.0.1:8765`. `HOOMY_BACKEND_URL` can select another
loopback backend for isolated tests. No credential is sent to the UI.

## First use

The `hoomy_home` tool declares a global MCP App entrypoint. The desktop host must
support and expose that extension for a sidebar launch independent of a chat.
Installation, MCP transport and global sidebar opening are verified (the user
opened the native screen on 2026-10-08). In a chat, invoke the Hoomy plugin and ask
it to open Hoomy.

## Authenticated central connection

`mcp.remote.json` is the central pilot manifest candidate. It declares the
Streamable HTTP endpoint `https://my.hoomy.dev/mcp`. Native protected-resource
discovery supplies host-managed OAuth with dynamic client registration. The
URL-only configuration avoids a confirmed native alpha parser incompatibility
with per-server authentication extensions. Keep `mcp.json` unchanged for the
existing local installation. The separately named, remote-only acceptance
package is [hoomy-remote-acceptance](../hoomy-remote-acceptance/README.md).
For the remote rollout, package a separate copy of this plugin with
`mcp.remote.json` as its root `mcp.json`; change the HTTPS URL if using an isolated
test server. Install that prepared package only after the gateway's OAuth
discovery, GitHub sign-in and callback are available. The normal local plugin
marketplace entry remains unchanged until the rollout is explicitly selected.

The gateway serves the same UI and tool definitions, with the first milestone
limited to `hoomy_home`, `hoomy_project`, `hoomy_update_project` and
`hoomy_project_guide`. The UI hides research actions in this mode. Source chats
remain readable provenance; their IDs and paths do not prove they can be opened
on the current machine, so remote profiles disable local chat links. A guided
new conversation prepares a prompt without assigning a server-local working path.

The Codex host owns the OAuth connection and refresh credentials. Embedded UI
requests use the host's `tools/call` bridge and never receive credentials or call
the remote API directly. Every HTTP MCP request revalidates its user grant,
resource and read scope; approved edits also require `projects:context:write`.
Collector grants are rejected by MCP. Revision conflicts are returned to the UI
for a fresh read and reconciliation, preserving the private backend's rules.

See [the authentication design](../../docs/architecture/authentication-and-device-enrolment.md)
and the [official remote manifest format](https://developers.openai.com/plugins/build/plugins).
Protocol integration tests are in
`services/hoomy-gateway/tests/mcp-authorization.test.mjs`; they use isolated
loopback servers and fabricated project data. Passing them does not establish
native Codex OAuth/UI compatibility: verify that with the configured pilot
server and a real host-managed connection before switching the installed plugin.

Start in **Mes projets**: select a project, edit its goals and save. The optional
guided conversation helps formulate them before saving approved wording. The
profile also shows task state, uncertainties, sources and feedback. See
[experiment 012](../../docs/experiments/012-project-profile.md).

In **Pour maintenant**, enter a request, optionally narrow the project and time, and answer the current
question. Opening the screen does not start research. During research it reads
progress from the same backend. A previous selection can be reopened without
generating another one.

After a technical interruption, use the retry action to preserve the original
request, project, budget and answered-question count. A failed service call is
distinct from an inconclusive recommendation; changing the goal is not its remedy.

Once an activity is ready, the handoff screen provides a prepared new-chat link
and up to three related source-chat links. The new-chat link prefills a composer;
the user sends the brief. An existing-chat link only opens that chat: copy and paste
the brief explicitly. Source chats are references, not a guaranteed best task to
resume. No chat is created or messaged by the MCP server.

## Local preview and checks

```sh
npm test --prefix plugins/hoomy
npm run preview --prefix plugins/hoomy
```

Open `http://127.0.0.1:8766/`. The preview serves the same HTML and dispatches the
same tools; it is a development surface, not evidence of native MCP UI support.
It is loopback-only and rejects foreign Host/Origin and cross-site requests.
Answering in this preview writes to the actual pilot unless an isolated backend
is configured. Do not fabricate user feedback during UI testing.

## Files and boundaries

- `bridge.mjs`: thin mapping to existing `/api/state`, `/api/now` and `/api/act`.
- `server.mjs`: MCP schemas, UI resource, tool registration and local preview.
- `ui/home.html`: one question or activity at a time, no framework or asset build.
- `skills/hoomy/SKILL.md`: safe continuation of the selected brief in Codex.
- `tests/bridge.test.mjs`: isolated contract and MCP protocol checks.

The backend remains authoritative for pending/completed/unknown states, evidence
expiry, revision checks, the candidate pool, question admission and user intent.
The plugin never converts an unknown answer into pending work. Handoff rereads
the authoritative selection and excludes chats outside the selected project.

This test does not add proactive scheduling, native Codex research orchestration,
completion assessment UI, discovery controls, a graph explorer or full history.
The skill lets Codex investigate with its available tools after the user initiates
the activity. Reading all conversations is not an automatic capability granted to
the plugin's MCP process.

## Visual identity

Version 0.2.1 aligns the interface with [hoomy.dev](https://hoomy.dev/).
See the shared [brand reference](../../docs/design/brand.md) for palette,
typography and product adaptations. All styling stays inside the MCP resource.
## Cached project evidence

The remote connection adds the read-only `hoomy_project_evidence` tool for
project-scoped public conversation projections. It returns at most 50 messages,
8 KiB of UTF-8 text per message and 256 KiB of text per page, with immutable
version IDs, declared source hashes, machine provenance and omission coverage.
It reads the central cache and never opens a source path or starts inference.
Legacy sources remain explicitly unassigned; synchronized locations and unique
projection blobs have separate coverage counts. Local stdio keeps its existing
tool inventory. A locally fenced project directs edits to the central connection.
