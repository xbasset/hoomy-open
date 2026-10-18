# Preparing the Hoomy 0.0.1 source candidate

This document describes the original candidate-export workflow. Version 0.0.1 is
now published; current `main` documentation can evolve beyond that immutable release.
`EXPORT-PROVENANCE.json`, `PUBLICATION-PROVENANCE.json` and the export manifest apply
to their recorded source pins and reviewed selection, not automatically to later
files or documentation changes. Keep the release tag, assets and provenance intact.
For current reader routes, start with [the README](../README.md).

This is an unsigned, reviewable source snapshot. No public repository, tag or
release is changed by the preparation tools. The candidate must be reviewed
before any publication to Hoomy Open.

## Export boundary

`public-export.json` lists each approved file with purpose and license. It also
classifies excluded private knowledge, operator artifacts, generated binaries,
private research inputs and local state by reason. Unknown added files stop the
export; a directory containing approved code is not a wildcard permission.
Excluded inputs are not opened or hashed. Symlinks, private/auth/data paths in
the approved list, changed source files and an existing output directory are
refused. Output is a fresh task-owned review directory.

```sh
mkdir -p artifacts/candidates
python3 tools/export_public.py --repository . \
  --manifest release/public-export.json \
  --output artifacts/candidates/source-preview
```

A preview explicitly records a dirty/unpinned source. Final packaging requires
a clean committed candidate and resolves its real HEAD after that commit:

```sh
python3 tools/package_candidate.py --repository . \
  --manifest release/public-export.json \
  --output artifacts/candidates/hoomy-0.0.1
```

The snapshot's `EXPORT-PROVENANCE.json` hashes the approved source files and the
manifest. It excludes its own bytes from that list. The deterministic archive
then includes and hashes this detached proof; `candidate.json` records the
archive hash and actual source commit. The tracked manifest records the base
commit, not a self-referential final commit hash. File content hashes and
normalized executable modes are separate. Counts of Git, caches and generated
state are excluded from public provenance, so a different ignored local cache
does not change the snapshot/archive bytes. Explicit excluded source-category
counts come from the pinned Git tree's filenames, never excluded file contents;
ignored local additions cannot change them. Outputs under `artifacts/` are ignored
and never become source inputs.

The signed collector distribution is independent. Never replace a published
archive with different bytes under the same component version or reuse its
signature for this platform source archive. A new collector build needs its own
reviewed version, dependency notices and release signing procedure.

## Review gates

1. Inspect the approved path list, exclusion summary and license grants.
2. Run export/packaging negative fixtures, component tests and documented install
   checks in isolation; no private dataset or live provider is needed.
3. Verify a clean candidate commit, exact snapshot hashes, provenance, preserved
   executable bits and absence of extra files. Inspect the generated source
   archive and current docs together.
4. Review portability: explicit origin/GitHub membership, private backend,
   external runtime volumes, project bootstrap and disabled project gates.
5. Obtain separate approval for any public export, push, merge, tag or release.

Public source tests do not establish a working GitHub deployment, a new native
Codex compatibility version, a trained learning policy or a successful provider
trial. Historical authored findings state their own limits. No private session
corpus or operator acceptance payload is an install prerequisite.

## Branch roles

The branch details below are historical preparation context, not clone or install
instructions and not the current public contribution workflow.

The private `main` baseline was `44e87e3`. The isolated
`xbt-agent/release-0-0-1` candidate started at `e4d20a3`; the original
`xbt-agent/experiment-act-learn` checkout preserves its three research edits.
The deterministic/private-discovery/hybrid experimental tips are ancestors of
that base. Preserve their refs; replay merges are unnecessary. Git preserves
chronology while the filesystem groups current components and thematic research.
The final candidate commit and possible private-main fast-forward are reviewed
separately. Hoomy Open's existing history remains untouched during preparation.

## Existing public baseline

The reviewed overlay retains the existing public charter, security policy,
attention illustration and EXP-0001 design bytes from Hoomy Open's
`d9f535332774c13c05430daef878638bdd0c7528` baseline. The original Apache/CC-BY
license texts are retained. Current README and source-ignore rules are intentional
updates for the optional central components, not evidence that EXP-0001's
ten-consecutive-session feasibility evaluation was run. No existing public Git
history is imported into the private preparation branch or changed here.

The private-linked learning proposal remains byte-identical in the private
candidate. Public selection uses a separately authored learning-pipeline note;
only the candidate research index/backlog links change to that selected note.
No imported source records are translated, rewritten or included in the export.
