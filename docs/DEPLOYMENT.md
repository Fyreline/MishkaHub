# Mishka Hub — Deployment & Operations

Purpose: the runbook for putting Mishka Hub in production and keeping it there: GitHub Pages for the SPA, Cloudflare Tunnel + launchd for the Mac-hosted API, CORS wiring between the two, and SQLite backups. Written to be executed top-to-bottom on a fresh setup and consulted piecemeal later. Topology rationale: [ARCHITECTURE.md](ARCHITECTURE.md) §4.

**Status: planned**

---

## 1. Frontend → GitHub Pages

### 1a. Repo setup

1. Push the repo to GitHub (`<user>/mishka-hub`, private is fine — Pages works on private repos for public sites on free plans only if the repo is public; if keeping the repo private requires it, a public *deploy-only* repo or GitHub Pro is the workaround — decide at setup ⚠️).
2. Repo → Settings → Pages → Source: **GitHub Actions**.
3. The app will live at `https://<user>.github.io/mishka-hub/` (project site) → build with `VITE_BASE=/mishka-hub/` (already supported by `apps/web/vite.config.ts`).

### 1b. Actions workflow

`.github/workflows/deploy-web.yml` (uses the official Pages actions — [upload-pages-artifact](https://github.com/actions/upload-pages-artifact), [deploy-pages](https://github.com/actions/deploy-pages)):

```yaml
name: Deploy web to GitHub Pages
on:
  push:
    branches: [main]
    paths: ['apps/web/**', '.github/workflows/deploy-web.yml']
  workflow_dispatch:

permissions:
  contents: read
  pages: write        # create Pages deployment
  id-token: write     # OIDC for deploy-pages

concurrency: { group: pages, cancel-in-progress: true }

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm, cache-dependency-path: apps/web/package-lock.json }
      - run: npm ci
        working-directory: apps/web
      - run: npm run build
        working-directory: apps/web
        env:
          VITE_BASE: /mishka-hub/
          VITE_API_BASE: https://mishka-api.example.com   # the tunnel hostname (§2)
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v4
        with: { path: apps/web/dist }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: github-pages, url: ${{ steps.deployment.outputs.page_url }} }
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

Notes:
- `VITE_API_BASE` is baked at build time (`apps/web/src/api.ts` reads it); changing the tunnel hostname means one-line edit + push.
- SPA deep-link 404s: GitHub Pages serves `404.html` — copy `index.html` to `404.html` in the build step when client-side routing lands (`cp dist/index.html dist/404.html`).
- **Custom domain (optional):** Settings → Pages → custom domain `films.example.com` + a `CNAME` DNS record to `<user>.github.io`; then `VITE_BASE=/` and add the origin to CORS (§3). Since the API domain is on Cloudflare anyway, keeping both app + API on one apex is tidy.

## 2. Backend → Cloudflare Tunnel (named) on macOS

Steps verified against [Cloudflare's tunnel guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/). Prereq: a domain on Cloudflare (free plan is fine).

```bash
# 1. install
brew install cloudflared

# 2. authenticate (browser opens; pick the zone/domain)
cloudflared tunnel login          # writes ~/.cloudflared/cert.pem

# 3. create the named tunnel (persistent credentials, stable UUID)
cloudflared tunnel create mishka-hub   # writes ~/.cloudflared/<UUID>.json

# 4. route a stable hostname to it (CNAME created in Cloudflare DNS)
cloudflared tunnel route dns mishka-hub mishka-api.example.com
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: <UUID>
credentials-file: /Users/<mac-user>/.cloudflared/<UUID>.json
ingress:
  - hostname: mishka-api.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404        # catch-all: everything else is a 404
```

```bash
# 5. test foreground, then install as a service
cloudflared tunnel run mishka-hub                 # smoke test
sudo cloudflared service install                # installs launch daemon; auto-start on boot
```

Cloudflared installs itself as a launch agent/daemon on macOS ([service docs](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/as-a-service/)); confirm with `sudo launchctl list | grep cloudflared`. Only outbound connections are made — no router ports opened. Optional hardening: put Cloudflare Access (Zero Trust free tier) in front of the hostname for a second auth wall; the SPA would then need the Access cookie flow, so this is deliberately **not** default.

## 3. Backend as a launchd service + CORS

`~/Library/LaunchAgents/com.mishka-hub.api.plist` (LaunchAgent = runs at login of the always-logged-in Mac user; use a LaunchDaemon only if the Mac runs headless without login):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.mishka-hub.api</string>
  <key>WorkingDirectory</key><string>/Users/<mac-user>/Documents/Dev/mishka-hub/apps/server</string>
  <key>ProgramArguments</key><array>
    <string>/Users/<mac-user>/Documents/Dev/mishka-hub/apps/server/.venv/bin/uvicorn</string>
    <string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8000</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/<mac-user>/Library/Logs/mishka-hub/api.log</string>
  <key>StandardErrorPath</key><string>/Users/<mac-user>/Library/Logs/mishka-hub/api.err.log</string>
</dict></plist>
```

```bash
mkdir -p ~/Library/Logs/mishka-hub
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mishka-hub.api.plist
launchctl kickstart -k gui/$(id -u)/com.mishka-hub.api    # restart after deploys
```

Pre-flight inside the service (wrap uvicorn in a small `run.sh` once migrations exist): `alembic upgrade head && exec uvicorn …` — see [DATA_MODEL.md](DATA_MODEL.md) §4. Note for [Phase 5](phases/PHASE-5-letterboxd-writeback.md): the LaunchAgent user session gives Playwright + macOS Keychain access; run `playwright install chromium` once as that user. Prevent sleep: System Settings → Energy → prevent automatic sleeping on power, or `sudo pmset -a sleep 0`.

**CORS:** add the Pages origin in `apps/server/.env` (origins are the scheme+host, no path):

```bash
MISHKA_CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173","https://<user>.github.io"]
```

(Custom-domain variant adds `https://films.example.com`.) Restart the service. Verify from the Pages site: the status pill goes green (health check crosses origins), then a search round-trips.

**Server env checklist (`apps/server/.env`):** `MISHKA_TMDB_READ_TOKEN`, `MISHKA_REGION=GB`, `MISHKA_LANGUAGE=en-GB`, `MISHKA_CORS_ORIGINS=[…]`, `MISHKA_JWT_SECRET` (Phase 4), `MISHKA_DEV_TOKEN` (interim, Phases 2–3), `MISHKA_JELLYFIN_URL`/`_API_KEY` (Phase 7), `MISHKA_ENVIRONMENT=production`.

## 4. SQLite backup strategy

The DB is small ([DATA_MODEL.md](DATA_MODEL.md) §5); model artefacts are reproducible (skip them); Playwright/session secrets are re-creatable (skip). **Back up: `mishka-hub.db` + `apps/server/.env` + `~/.cloudflared/`.**

- **Nightly online backup** (WAL-safe — never `cp` a live SQLite file):

```bash
#!/bin/zsh  # scripts/backup.sh
set -euo pipefail
SRC="$HOME/Documents/Dev/mishka-hub/data/mishka.db"
DST_DIR="$HOME/Backups/mishka-hub"; mkdir -p "$DST_DIR"
STAMP=$(date +%Y%m%d)
sqlite3 "$SRC" ".backup '$DST_DIR/mishka-$STAMP.db'"
gzip -f "$DST_DIR/mishka-$STAMP.db"
ls -t "$DST_DIR"/mishka-*.db.gz | tail -n +15 | xargs rm -f   # keep 14
```

- Scheduled by a second LaunchAgent (`com.mishka-hub.backup.plist`, `StartCalendarInterval` 03:30) — cron is deprecated on macOS.
- **Off-machine copy:** point `DST_DIR` inside an iCloud Drive/Dropbox-synced folder, or add an `rclone copy` line to any remote. A backup on the same failing disk is not a backup ⚠️ (setup decision for the user).
- **Before schema migrations:** deploy script runs `backup.sh` first (also in §3 pre-flight when a migration is pending).
- **Restore drill (do once):** stop service → `gunzip -c mishka-YYYYMMDD.db.gz > data/mishka.db` → start → verify poster wall + `sync_state` timestamps. Document actual time-to-restore in this file after the drill.

## 5. Windows-desktop migration (future, brief)

| macOS piece | Windows equivalent |
|---|---|
| launchd agents | NSSM-wrapped services or Task Scheduler |
| `brew install cloudflared` | `winget install Cloudflare.cloudflared`; `cloudflared service install` (Windows service) |
| macOS Keychain (via `keyring`) | Windows Credential Locker (same `keyring` API) |
| `pmset` no-sleep | Power settings: never sleep |
| paths in plists/scripts | re-generate; everything else (SQLite file, `.env`, tunnel credentials dir) copies across |

## 6. Acceptance criteria

- [ ] Push to `main` auto-deploys the SPA; site loads at the Pages URL with correct asset paths (`VITE_BASE`).
- [ ] Tunnel hostname serves `/api/health` over HTTPS from anywhere (phone on mobile data).
- [ ] SPA on Pages talks to the API: green status pill, search works, no CORS errors in console.
- [ ] Mac reboot → both cloudflared and the API come back without human action (pull the power, test).
- [ ] uvicorn unreachable directly from LAN (`curl http://<mac-lan-ip>:8000` fails) — loopback binding proven.
- [ ] Nightly backup produces a dated gzip; restore drill performed and timed.
- [ ] `.env`, `data/`, `~/.cloudflared` are all gitignored (secret-scan the repo before first push).
