# Hoomy private discovery app

A native macOS review surface for the local pilot. Requires macOS 14 or later and
the local backend at `127.0.0.1:8765`. It does not read Codex files itself.

Build and check from this directory:

```sh
swift build
swift test
swift run HoomyPilot
```

The first screen confirms discovery scope and external inference before enabling
the worker. Do not enable discovery during a smoke test unless the user approves
processing the included conversations. Project lists stay in stable creation
order. Corrections, evidence, and before/after history are inspectable.

Development reviews require an explicit human verdict and evidence assessment.
Their timer runs only while the review is open and the app is active; it can be
paused. These are not a frozen pilot or evidence of research success.

Scope → Reset Hoomy removes the pilot’s copied/derived data and returns to scope
confirmation. It does not remove original Codex conversation files. There is no
selective deletion or undo for merges yet.

Models, networking, and review views use shared SwiftUI/Foundation code. The
macOS shell is deliberately small; an iOS target, adaptive layouts, signing,
and deployment are future work, not shipped support.


The **Agir & apprendre** workspace implements experiment 006. It reads the existing
Discover project list and requests an activity explicitly; it does not require
Understand or resume Discover. Suggestions, alternatives, evidence and feedback are
persisted by the backend. See [the execution plan](../../../docs/experiments/006-on-demand-act-learn.md).
