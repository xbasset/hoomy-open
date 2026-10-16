---
name: hoomy
description: Open central Hoomy project profiles and cached evidence through Hoomy Remote Acceptance. Never substitute the local Hoomy connection.
---

# Central Hoomy only

This skill belongs to `hoomy-remote-acceptance`. Its MCP server is
`hoomy_remote_acceptance`, connected to `https://my.hoomy.dev/mcp`.

1. Find tools belonging to that exact remote server or this plugin's qualified
   namespace. Use the qualified `hoomy_home` tool from that server. An unqualified
   name is insufficient when both Hoomy plugins are installed.
2. Never call tools from server `hoomy`, which is the separate local plugin.
   Never substitute local data, a preview, or a manually configured server alias.
   If the remote tools are unavailable or require authentication, explain that
   this remote plugin must be connected in Codex. Stop without opening local Hoomy.
3. Verify the home result has `connection.mode: central`,
   `connection.origin: https://my.hoomy.dev`, and
   `capabilities.cached_project_evidence: true`. A missing or different marker
   is a connection mismatch; stop and report it.
4. The home tool opens the embedded project chooser. Let the user select a
   project. Its remote profile exposes **Sources liées au projet**, per-machine
   provenance, and **Lire les échanges en cache** for synchronized sources.
5. Read project context with this same server's `hoomy_project`. Read bounded
   cached exchanges with its `hoomy_project_evidence`, using project/source IDs
   returned by the profile. These reads never open another machine's paths or
   trigger inference. Legacy sources are unassigned; locations and unique
   projection contents have separate coverage counts. Disclose partial coverage.
6. Context edits require explicit user intent and the returned current revision.
   On conflict, reread and reconcile. Historical exchanges do not establish
   current completion and must not overwrite approved goals automatically.

Codex manages the OAuth connection and renewal. Do not request, read, copy, or
store tokens in this skill or the embedded UI. Do not message source chats.

## Cited state pilot

The NoFlinch profile can show centrally generated state with exact cached
citations. Use this server's `hoomy_project_state` for a cached read; it does not
create a job. Conclusions are dated reports or interpretations, not independent
verification or approved human goals. Open “Why?” through
`hoomy_project_state_citation` to inspect the original quote, immutable version,
machine, native conversation, lines and dates.

Call `hoomy_refresh_project_state` only when the user explicitly requests an
update, using the context/corpus revisions and current `method_hash` just read,
and a fresh request UUID. A missing or stale method hash requires a fresh read;
never silently switch the method. Historical results remain readable with their
recorded method, but an incompatible old run cannot be executed or retried.
Disclose the actual messages supplied, unread messages, truncated spans and
per-machine source coverage; partial selection is not a complete project audit.
Generation has its own consent and project allowlist. The dedicated Mac executor
consumes explicitly queued jobs; a missing/offline executor leaves the previous
state readable. Never turn collection into an implicit generation request.
`hoomy_project_state_worker` is its restricted transport, not a general tool for
publishing a hand-written summary or retrying an ambiguous model call.

After review, a user can explicitly authorize a **new attempt** for a stored
failed or review-required run with `retry_of_run_id`. Use the returned run ID and
unchanged current revisions and compatible current method hash. Keep the same request UUID for a lost-acknowledgment
replay. This creates at most one child of that run; it never resumes its dispatched
call or hides unknown prior usage. Do not add this field automatically to an
ordinary refresh, and never retry a terminal job from the executor's idle watcher.
