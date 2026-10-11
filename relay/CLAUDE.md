# relay

Part of HERE. Read ../README.md §7 and §12 (transport, routing table §12.10) first.

## Job
The small WebSocket server every device connects to outbound (Vultr, Atlanta; Caddy terminates TLS).
Routes each message to the other clients in the same session that subscribed to its `type`, keeps the
latest `config` per session and replays it to new clients, drops stale positional messages for slow
clients instead of buffering, and logs every message as JSON lines. Not a place for logic.

## Inputs / outputs
`wss://<host>/ws?session=&role=&token=` (roles: laptop, phone, dashboard, memory, tool). `GET /health`.
Consumes and forwards everything; sets `session` on forwarded messages.

## Run
- Local / fallback: `python -m relay --host 0.0.0.0 --port 8080` (token from `HERE_RELAY_TOKEN` or relay/.env).
- Production: `python tools/deploy_vultr.py --ip <IPv4> --memory --save-token` (relay/DEPLOY.md; systemd unit
  `here-relay.service` + `Caddyfile`). The laptop keeps the local relay primary and mirrors to this one.
- Tests: `python -m pytest relay` (auth, routing, sessions, config replay, health, coalescing) and the
  end-to-end smoke test in `tests/`.

## Rules
- Single file (`relay.py`), stdlib + `websockets` only.
- Never queue stale data: `sources`/`scene`/`heartbeat` keep only the newest per sender; events are a bounded
  outbox that drops the oldest.
- Default subscriptions must equal `contracts` DEFAULT_SUBSCRIBE (tests/test_consistency.py checks).
- Token in env only.

## Current milestone
Done locally (tests + smoke); deploy scripted. Next: create the Vultr server (ADIL_TODO.md §2), run the deploy,
put the hostname in README §27, measure the round trip from campus Wi-Fi (README §21).
