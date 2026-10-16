# Central plugin acceptance package

This separate package preserves the installed local `hoomy@hoomy-local` plugin.
Its persistent source and marketplace are reviewable in this repository. The
gateway serves the embedded UI; this package contains no duplicate UI or tokens.

The native Codex 0.162.0-alpha.17.2 parser recognizes this portable URL-only MCP
configuration. In read-only fixture comparisons, adding an
`extensions.com.openai.auth` object made its declared MCP server disappear.
Authentication therefore uses protected-resource discovery and host-managed
OAuth instead. The gateway advertises project read, context-write, state-refresh,
state-execute and revocable ongoing access scopes. State refresh and execution
require separate explicit consent for `projects:state:refresh` and
`projects:state:execute`; existing read/write grants never gain them silently.

Use the official native `plugin/install` API with the selected checkout path.
The absolute path below is a fictional placeholder:

```json
{
  "pluginName": "hoomy-remote-acceptance",
  "marketplacePath": "/absolute/path/to/hoomy/plugins/hoomy-remote-acceptance/.agents/plugins/marketplace.json"
}
```

Then inspect native `mcpServerStatus/list` for the plugin's registered server and
connect it through native OAuth if requested. A read-only `plugin/read` confirms
manifest parsing; it does not prove that an existing desktop chat has refreshed
its tool catalog. Verify the actual embedded panel separately. Its visible
`Projets centralisés · https://my.hoomy.dev` marker distinguishes central data.
Do not edit the installed cache, overwrite global MCP aliases, or use the local
server as a substitute for this acceptance test.
