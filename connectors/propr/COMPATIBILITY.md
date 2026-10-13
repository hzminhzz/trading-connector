# Propr compatibility evidence (work in progress)

- Inspected local `ml4t-live` distribution in `/home/quant/dev/quant/quant-research/.venv`: version **0.1.2** (verified with `importlib.metadata`).
- Propr website SDK plan references **v0.1.0**, but current REST and WS response shapes remain **unverified**.
- Propr Beta environment is **not proven simulation-only**. `assert_paper_trading` fails closed and all remote mutations are disabled.
- The REST `page`/`limit` and `data` pagination format in `rest.py` is **provisional** and must not be claimed provider-qualified.
- The implemented HTTP transport is read-only. `TransportPolicy(allow_mutations=True)` is an internal testing primitive only, not production arming; do not instantiate it with a real network client.
- No provider account snapshot or actual execution has been verified.

## Outstanding issue #1 checks

Inspect and record exact `AsyncBrokerProtocol`, `DataFeedProtocol`, order and capability signatures from installed source; compare with Propr official API and WS schemas; add contract tests. Until then #1 remains open.
