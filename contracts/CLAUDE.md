# contracts

Part of HERE. Read ../README.md §12 first: this folder implements it.

## Job
The wire contract (v3) between laptop, relay, phone and extras: one JSON Schema per message type,
generated from `build_schemas.py`, plus hand-mirrored types for Python (`python/here_contract.py`),
Swift (`swift/HereContract.swift`, compiled straight into the iOS app) and TypeScript (`ts/contract.ts`),
and one valid example per type (`examples/`, the README §12.11 lines verbatim for sources/speech_start/utterance).

## Inputs / outputs
None at runtime. Everything else imports from here.

## Run
- `python contracts/build_schemas.py` after editing the shapes (rewrites `schemas/`).
- `python -m pytest contracts`: examples validate + round-trip in Python, `tsc --strict` accepts them, the
  Swift mirror has a decode case and CodingKey for every type/field, schemas match the generator.
- Swift decoding of the examples runs in the iOS test target (`ContractTests`).

## Rules
- Contract changes go through the integrator (Kori). Edit `build_schemas.py` -> regenerate -> update the three
  mirrors + examples -> bump `v` if breaking -> update README §12, all in one commit. Log it in `CHANGES.md`
  (which also lists known drift for the next whole-system accordance pass), and add what the app must do to
  `../IOS_INTEGRATION.md`.
- Nullable fields (`tone`, `bearing_deg` on sound_event/alert, `person_label`) are always sent, as null.
  Optional fields (`seq`, `session`, `stage_ms`, `ref`, `detail`...) are omitted when absent.
- Receivers ignore unknown types (Python `Unknown`, Swift `.unknown`).
- Bearings: world frame, clockwise, (-180, 180] (the schema rejects -180).

## Current milestone
Done: v3 complete for all 15 types (perception_enrichment added 2026-10-10, additive); 33 contract tests pass.
