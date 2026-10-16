# Ready-idea Decisions prototype

Isolated preparation for [experiment 008](../../../docs/experiments/008-ready-ideas-decisions.md).
No live database, background scheduling, Swift changes, or deployment.

- `decisions.py`: direct official HTTPS endpoint and typed response validation.
- `policy.py`: evidence/version/freshness gate and delivery recheck.
- `fixtures.py`: ten fictional cases matching observed failure types.
- `benchmark.py`: measured calls, including ineligible inputs for classifier evaluation.
- `selection_smoke.py`: final choice between two eligible fictional ideas.

Python 3.12+, no third-party packages. Run from the repository root:

```sh
python3 -m unittest discover -s backend/experiments/decision_ready -v
python3 backend/experiments/decision_ready/benchmark.py \
  --key-file /path/to/authorized.env \
  --output backend/runtime/decisions-benchmark.json
python3 backend/experiments/decision_ready/selection_smoke.py \
  --key-file /path/to/authorized.env \
  --output backend/runtime/decisions-selection.json
```

For a macOS Python installation without default CA paths, set
`SSL_CERT_FILE=/etc/ssl/cert.pem` if that system trust file exists. Never disable
certificate verification. The environment file is read as data; it is not executed.
Only `OPENAI_API_KEY` is extracted. Requests go only to `api.openai.com`.

Live runs spend API credits and contain fictional data only. There are no automatic
retries or provider fallbacks. Stop on unavailable access rather than switching
models. A five-second socket timeout bounds individual network operations; this
prototype does not implement the proposed two-second end-to-end UI deadline.

The observation envelopes passed to `gate` are trusted application data. A production
reducer must authenticate provenance, bind each observation to its exact criterion,
validate timestamps, and reject stale state revisions; a model cannot create its own
trusted `supports_exact_claim` flag. This prototype proves routing rules, not that
future evidence ingestion is correct.
