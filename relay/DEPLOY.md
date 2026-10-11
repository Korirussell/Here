# Deploying the relay on Vultr (SPEC §7)

About 15 minutes. Result: `wss://<host>/ws` with automatic HTTPS (Caddy), the relay as a systemd
service on 127.0.0.1:8080, a firewall that only opens SSH/HTTP/HTTPS, and `/health` to check it.

## Fast path: `tools/deploy_vultr.py`

Once the server exists (step 1) and `~/.ssh/here_vultr.pub` is on it, one command does steps 3-8, from the repo root:

```powershell
.\.venv\Scripts\python tools/deploy_vultr.py --ip <IPv4> --memory --save-token      # add --dry-run first to see every command
```

- Re-runnable: packages, firewall, the `here` user, code (`git archive HEAD`), venv, token (created only if missing,
  never printed), the relay and memory services, Caddy with `<ip-with-dashes>.sslip.io` (or `--host`), then
  `https://HOST/health` and `/api/health`.
- `--save-token` writes the token into `laptop/.env` and adds `wss://HOST/ws` to `HERE_RELAY_MIRRORS`: the laptop
  keeps publishing to the local relay (the live path) and mirrors to Vultr.
- `--memory` takes `NEON_DATABASE_URL` from `extras/memory/.env` (`--memory-env` for another file) over ssh's stdin.
- Later: `python tools/deploy_vultr.py update --ip <IPv4> --memory` (new code + restart), `... status --ip <IPv4>`.

The manual steps below are what it runs.

## 1. Create the server

Vultr → Deploy → **Cloud Compute, Shared CPU**, location **Atlanta** (closest to Alabama, SPEC §5),
image **Ubuntu 24.04 LTS**, the cheapest plan (1 vCPU / 1 GB is plenty: the relay forwards small JSON).
Add your SSH key. Note the public IPv4, e.g. `203.0.113.7`.

## 2. Pick a hostname

iOS refuses plain `ws://` to internet hosts, so we need TLS, so we need a name:

- **No domain:** use sslip.io: `203-0-113-7.sslip.io` resolves to `203.0.113.7`. Nothing to set up.
- **Your own domain:** add an `A` record `here.example.com -> 203.0.113.7`.

Below, `HOST` is that name. (TODO: write the chosen one into SPEC §27 / laptop/.env / the app.)

## 3. Packages and firewall

```bash
ssh root@203.0.113.7
apt update && apt -y upgrade
apt -y install python3-venv git debian-keyring debian-archive-keyring apt-transport-https curl ufw
# Caddy (official apt repo)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt -y install caddy
# firewall: SSH + HTTP (certificate challenge) + HTTPS only; 8080 stays private
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

Vultr also has a cloud firewall; if you enable it, open the same three ports.

## 4. The relay code

```bash
useradd --system --create-home --home-dir /opt/here here
```

The GitHub repo is private, so copy the code from a laptop checkout instead of cloning (PowerShell or bash, from
the repo root; `git archive` sends only committed files, never `.env`s):

```bash
git archive HEAD relay contracts extras/memory extras/cloud | ssh root@203.0.113.7 "tar -x -C /opt/here && chown -R here:here /opt/here"
```

Then on the server:

```bash
sudo -u here python3 -m venv /opt/here/.venv
sudo -u here /opt/here/.venv/bin/pip install -r /opt/here/relay/requirements.txt -r /opt/here/extras/memory/requirements.txt
mkdir -p /var/log/here && chown here:here /var/log/here

# the shared token (laptop + phone use the same one)
TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))"); echo "token: $TOKEN"
printf "HERE_RELAY_TOKEN=%s\n" "$TOKEN" > /opt/here/relay/.env
chown here:here /opt/here/relay/.env && chmod 600 /opt/here/relay/.env
```

Keep the token out of git: it goes in `laptop/.env` (`HERE_RELAY_TOKEN=`) and in the app's Settings → Relay.

## 5. Run it as a service

```bash
cp /opt/here/relay/here-relay.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now here-relay
systemctl status here-relay --no-pager        # "HERE relay on ws://127.0.0.1:8080/ws ... auth on"
curl -s http://127.0.0.1:8080/health           # {"ok": true, ...}
```

## 6. Caddy (HTTPS)

```bash
cp /opt/here/relay/Caddyfile /etc/caddy/Caddyfile
echo "HERE_HOST=HOST" >> /etc/default/caddy             # e.g. HERE_HOST=203-0-113-7.sslip.io
mkdir -p /etc/systemd/system/caddy.service.d
printf "[Service]\nEnvironmentFile=/etc/default/caddy\n" > /etc/systemd/system/caddy.service.d/env.conf
systemctl daemon-reload && systemctl restart caddy
curl -s https://HOST/health                              # from your laptop: {"ok": true, ...}
```

The first request takes a few seconds while Caddy gets the certificate.

## 7. Point everything at it

- **Laptop:** `laptop/.env` → `HERE_RELAY_URL=wss://HOST/ws`, `HERE_RELAY_TOKEN=<token>`, then
  `python tools/check_gpu.py` (last lines check the relay) and `python -m laptop --real`.
- **Phone:** Settings → Relay → `wss://HOST/ws`, token, session `demo`. The status pill turns green.
- **Watch traffic:** `python tools/relay_tap.py` (from the laptop), or on the server
  `tail -f /var/log/here/relay.jsonl`.
- **Round-trip time:** SPEC §21 spike: the app's debug overlay shows `clock rtt`.

## 8. Memory service (optional, never on the live path)

The memory API (`extras/memory`) runs next to the relay as its own service; Caddy already proxies `/api/*` to it.

```bash
mkdir -p /opt/here/extras/memory/data /opt/here/logs && chown -R here:here /opt/here/extras/memory/data /opt/here/logs
cat > /opt/here/extras/memory/.env <<ENV
HERE_RELAY_URL=ws://127.0.0.1:8080/ws
HERE_RELAY_TOKEN=$(sed -n 's/^HERE_RELAY_TOKEN=//p' /opt/here/relay/.env)
HERE_SESSION=demo
NEON_DATABASE_URL=
ENV
chown here:here /opt/here/extras/memory/.env && chmod 600 /opt/here/extras/memory/.env
cp /opt/here/extras/memory/here-memory.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now here-memory
curl -s http://127.0.0.1:8790/api/health          # {"ok": true, "db": "local" | "neon", ...}
curl -s https://HOST/api/health                   # through Caddy; other /api endpoints need ?token=<token>
```

Paste the Neon connection string into `NEON_DATABASE_URL` (then `systemctl restart here-memory`); empty = SQLite.
Logs: `journalctl -u here-memory -f`.

## 9. Server status and session bundles (optional)

- **Status:** `https://HOST/api/status.html?token=<token>` (JSON at `/api/status`): relay health, message rates
  and laptop -> relay ages from the relay log, memory database, which insights are served. Needs the memory service
  (step 8); `tools/deploy_vultr.py` points it at `/var/log/here/relay.jsonl`, and `status` prints it.
- **Bundles to Object Storage:** create a bucket in Vultr Object Storage, put `VULTR_S3_ENDPOINT`,
  `VULTR_S3_BUCKET`, `VULTR_S3_ACCESS_KEY`, `VULTR_S3_SECRET_KEY` in `extras/cloud/.env`, then
  `python -m extras.cloud.export_bundle --upload` (from the laptop: memory lives in Neon). See
  `extras/cloud/README.md`.

## Updating

From the laptop checkout: the same `git archive ... | ssh ...` line as step 4, then
`systemctl restart here-relay here-memory`.

## Local fallback relay (SPEC §7 backups)

Internet down but the devices can see each other on the same network: on the laptop,

```powershell
$env:HERE_RELAY_TOKEN="<token>"; .\.venv\Scripts\python -m relay --host 0.0.0.0 --port 8080
```

and point both at `ws://<laptop-ip>:8080/ws` (the app allows `ws://` to local addresses only). Test it
once before the event; campus networks often block device-to-device traffic.
