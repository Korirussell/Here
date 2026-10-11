# HERE (Hearing Environmental Reality Engine)

Read **README.md** first (what HERE is, the technologies and their endpoints, how to run it). Engineering detail
(frames, timing, the message contract, attribution, the belt protocol) is in **docs/SPEC.md**; code comments cite
it as `SPEC §N`.

## Rules

- **Contract first.** Message shapes change only in `contracts/build_schemas.py`: regenerate, update the
  Python / Swift / TS mirrors and examples, bump `v` on breaking changes, update docs/SPEC.md §12, all in one commit.
- **No audio or video on the network** (SPEC §6). The laptop sends world bearings; the phone converts.
- **Every component runs without hardware** (mocks / fixtures) and keeps its tunables in one file.
- **Every sponsor technology has a fallback** and the system runs with all of them off (README, "Technologies and
  endpoints"). A new integration gets a row there and in `GET /api/integrations` (`extras/memory/api.py`).
- **Secrets** only in `.env` files (see the `.env.example`s); never commit them.
- Python 3.12 on the laptop (`.venv`), Swift/SwiftUI iOS 26 on the phone, Arduino C++ on the ESP32.
- `.\tasks.ps1 test` (or `make test`) must pass.
