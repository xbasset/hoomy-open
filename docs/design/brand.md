# Hoomy visual identity

Source of truth: https://hoomy.dev/styles.css, observed 2026-10-09.
Use this identity across product surfaces, including the Codex plugin. Runtime
copy remains localized. Avoid introducing a separate green product palette or
an invented icon beside the Hoomy wordmark.

| Token | Value | Role |
| --- | --- | --- |
| paper | `#f7f8f9` | Page background |
| ink | `#121a21` | Main text |
| muted | `#4f5d68` | Secondary text |
| line | `rgba(18,26,33,.24)` | Dividers and control borders |
| signal | `#e56f2d` | Small attention accents and focus rings |
| signal-dark | `#174f76` | Emphasis, links and product primary actions |
| field | `#e8eef2` | Soft surfaces |
| night | `#142a3a` | Dark surfaces |
| night-muted | `#d3dbe0` | Text on dark surfaces |

Typography follows the website: Charter, Iowan Old Style, Palatino, Georgia for
headings; Inter/system sans-serif for body and controls; system monospace for
compact metadata. Fonts use local fallbacks without a third-party font request.
Headings have regular weight and tight tracking. The Hoomy wordmark uses bold
sans-serif with -0.055em tracking; it has no separate leaf or symbol.

Keep a quiet editorial composition: generous spacing, fine dividers, restrained
corners, few decorative elements. Orange is an attention signal, not a body-text
color on the light background. Blue primary buttons keep white labels. Product
headings are smaller than the marketing hero to leave room for actual work.

The plugin defines these tokens in `plugins/hoomy/ui/home.html`, embedded into the
MCP resource so it needs no external stylesheet or network asset. Its dark mode
is a product adaptation of the website's night palette, with light blue actions
and dark labels for contrast. It is not claimed to be an existing website mode.

When adding or changing a product surface, reuse these tokens and typography.
Verify narrow layouts, keyboard focus and the host's dark/light mode. Layout may
adapt to the task; brand colors and wordmark should not drift between surfaces.
