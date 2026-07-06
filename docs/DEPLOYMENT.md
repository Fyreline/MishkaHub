# Mishka Hub — Deployment & Operations

Purpose: the runbook for putting Mishka Hub in production and keeping it there: GitHub Pages for the SPA, Cloudflare Tunnel + launchd for the Mac-hosted API, CORS wiring between the two, and SQLite backups. Written to be executed top-to-bottom on a fresh setup and consulted piecemeal later. Topology rationale: [ARCHITECTURE.md](ARCHITECTURE.md) §4.

**Status: §1-§3 all live in production as of 2026-07-05.** The repo is public (audited clean —
no secrets ever committed, in full git history, not just current files); Pages serves
`https://fyreline.github.io/MishkaHub/`; the domain is `mishka-hub.com` (real, owned by the
household); the tunnel and the API are both running as persistent services on the household
Mac. Verified end-to-end: a real `POST /api/auth/login` through the public URL, through
Cloudflare, through the tunnel, to the local FastAPI process, returns a valid token. §4
(nightly backups) is still ⬜ not set up.

**Two real gotchas hit during setup, worth knowing before repeating this:**
1. `sudo cloudflared service install` on this machine wrote a `/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`
   with **no `ProgramArguments` beyond the bare binary** — it did not automatically point at
   `~/.cloudflared/config.yml`. Fixed by hand-writing the plist with explicit
   `--config /Users/mack/.cloudflared/config.yml tunnel run` arguments, then
   `sudo launchctl unload`/`load` to pick it up. Check `ProgramArguments` in the installed plist
   before assuming the service will actually find your tunnel config.
2. A `LaunchAgent` (no `sudo`, runs as the logged-in user) invoking the venv's Python to serve
   `apps/server` crashed with `PermissionError: ... .venv/pyvenv.cfg` — macOS's TCC privacy
   protection blocks background/launchd-spawned processes from reading `~/Documents/**` even
   though an interactive Terminal session can. Fixed by granting **Full Disk Access** (System
   Settings → Privacy & Security → Full Disk Access) to the actual Python binary
   (`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12`, found by resolving the
   venv's `python3` symlink) — not to Terminal, since launchd doesn't spawn through Terminal at
   all. If a launchd-managed process under this repo's path mysteriously can't read its own
   files, check this first.

---

## 1. Frontend → GitHub Pages

### 1a. Repo setup (manual, one-time — do this yourself)

1. This repo (`Fyreline/MishkaHub`) is already private. **GitHub Pages only serves sites from
   private repos on paid plans (Pro/Team/Enterprise)** — the free plan requires the repo to be
   public. If you want to keep the repo private *and* use Pages, upgrade the account that owns
   it to GitHub Pro (or move Pages to a separate public deploy-only repo, but a paid plan is
   simpler and is what you said you'd rather do).
2. Repo → **Settings → Pages → Source: GitHub Actions.**
3. Repo → **Settings → Secrets and variables → Actions → Variables → New repository variable:**
   `VITE_API_BASE` = your Cloudflare Tunnel hostname once §2 below is set up (e.g.
   `https://mishka-api.example.com`). Until this is set, the deployed site falls back to
   `http://127.0.0.1:8000`, which won't reach anything from a browser that isn't on the same Mac.
4. Push to `main` — the workflow builds and deploys automatically. The app will live at
   `https://fyreline.github.io/MishkaHub/` (`VITE_BASE=/MishkaHub/` is already baked into the
   workflow to match this repo's actual name/case).

### 1b. Actions workflow

`.github/workflows/deploy-web.yml` (real file in the repo; uses the official Pages actions —
[upload-pages-artifact](https://github.com/actions/upload-pages-artifact),
[deploy-pages](https://github.com/actions/deploy-pages)) — triggers on push to `main` when
`apps/web/**` changes, builds with `VITE_BASE=/MishkaHub/` and the `VITE_API_BASE` repo
variable from §1a step 3, copies `index.html` to `404.html` for SPA-fallback insurance (this
app has no client-side routing today, so this is cheap future-proofing rather than something
currently load-bearing), and deploys via the standard Pages Actions.

Notes:
- `VITE_API_BASE` is baked at build time (`apps/web/src/api.ts` reads it); changing the tunnel hostname means updating the repo variable and re-running the workflow (or pushing any `apps/web/**` change).
- **Custom domain (optional):** Settings → Pages → custom domain `films.example.com` + a `CNAME` DNS record to `fyreline.github.io`; then set `VITE_BASE=/` in the workflow and add the custom-domain origin to `cors_origins` (§3). Since the API domain would be on Cloudflare anyway, keeping both app + API on one apex is tidy.

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

**CORS:** `https://fyreline.github.io` is already baked into `config.py`'s `cors_origins`
default (origins are scheme+host only, no path — this one entry covers the
`https://fyreline.github.io/MishkaHub/` project site regardless of the `/MishkaHub/` part), so
nothing needs to be set for the plain Pages URL to work. Override entirely via
`MISHKA_CORS_ORIGINS` in `apps/server/.env` only if you add a custom domain later:

```bash
MISHKA_CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173","https://fyreline.github.io","https://films.example.com"]
```

Restart the service after changing it. Verify from the Pages site: the status pill goes green (health check crosses origins), then a search round-trips.

**Server env checklist (`apps/server/.env`):** `MISHKA_TMDB_READ_TOKEN`, `MISHKA_REGION=GB`, `MISHKA_LANGUAGE=en-GB`, `MISHKA_JWT_SECRET` (Phase 4 — required for login to work), `MISHKA_CORS_ORIGINS=[…]` (only if overriding the built-in default above), `MISHKA_JELLYFIN_URL`/`_API_KEY` (Phase 7), `MISHKA_ENVIRONMENT=production`.

## 3b. Control Center server toggle (macOS Tahoe)

`scripts/mishka-server.sh [on|off|toggle|status] [-n]` starts/stops the API LaunchAgent (`-n` posts a macOS notification with the result). It deliberately leaves the cloudflared tunnel alone: that's a root daemon (password prompt on every toggle) with negligible idle power — the ML server is the process worth stopping. While off, the Pages site loads but shows "Server offline".

A true third-party Control Center widget requires a signed WidgetKit app (full Xcode — not installed). Same outcome via a Shortcut control, set up once by hand:

1. **Shortcuts app** → new shortcut → add **Run Shell Script** action → command: `~/Documents/Dev/MishkaHub/scripts/mishka-server.sh toggle -n`, shell `zsh`.
2. Name it **Mishka Hub**; click the icon in the title bar to pick a glyph (there's a cat 🐱 / pawprint) and color.
3. **Control Center** (menu bar) → **Edit Controls** (or System Settings → Control Center) → add a **Shortcut** control → point it at the Mishka Hub shortcut.

Tapping it toggles the server and pops a "Server ON 🐱 / OFF 💤" notification. First run may prompt once to allow the shortcut to run shell scripts (Shortcuts settings → Advanced → Allow Running Scripts).

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
