# Phase 7 — Local Media → webOS TV

Purpose: index the movie files the couple owns on the home Mac (later a Windows desktop), match them to TMDB ids so they appear inside Mishka Hub (poster wall badge "you own this", recommendable even when on no streaming service), and play them on the LG webOS TV from a **"Play on TV"** button in the Mishka Hub UI. This doc records the researched serving-stack decision (Jellyfin), the comparison that led there, file indexing/matching, and the playback control flow. Tables: `media_files` in [DATA_MODEL.md](../DATA_MODEL.md); endpoints: [API.md](../API.md) §Phase 7.

**Status: planned**

---

## 1. Serving-stack decision: Jellyfin

### Comparison (researched 2026-07)

| | **DLNA/UPnP** (e.g. MiniDLNA/UMS) | **Jellyfin** | **Plain HTTP + webOS browser** |
|---|---|---|---|
| webOS client | Built-in Media Player browses DLNA servers (DMP). TV-as-renderer (DMR, needed for *push from Mishka Hub*) is inconsistent across webOS versions | **Official app in the LG Content Store, approved for *all* webOS versions since May 2024** ([jellyfin.org](https://jellyfin.org/posts/webos-july2022/), [jellyfin/jellyfin-webos](https://github.com/jellyfin/jellyfin-webos), [announcement](https://x.com/jellyfin/status/1770519636833956036)) | TV browser plays HTML5 `<video>` (MP4/H.264 reliably) |
| Codec mismatch handling | None — TV plays it or it doesn't | **Transcodes on demand** (ffmpeg) when the TV can't direct-play | None; must pre-encode everything to MP4/H.264/AAC |
| "Play on TV" from Mishka Hub | Requires TV DMR + a control point; fragile | **Remote-control API**: server can command a client session to play an item (`POST /Sessions/{id}/Playing`) | Impossible to push; human opens the browser and navigates |
| Subtitles, resume, watched-state | Minimal/none | Full (external SRT/ASS, resume positions, per-user) | Browser-dependent, no resume |
| Metadata/TMDB linkage | Filename-only | **Scrapes TMDB itself and exposes `ProviderIds.Tmdb` per item via API** — free join key to `films.id` | None |
| Windows-desktop future | Fine | **First-class Windows server installer** | Fine |
| Ops burden | Tiny | Moderate (one more service; ~200 MB idle) | Tiny |
| **Verdict** | ❌ can't do push-to-TV reliably | ✅ **chosen** | ❌ fallback only |

webOS native format envelope, for the direct-play happy path (verified: [webOS TV developer specs](https://webostv.developer.lge.com/develop/specifications/video-audio-60)): containers MKV/MP4/TS; video H.264, HEVC (VP9/AV1 on newer sets); audio AAC/AC3/EAC3/MP3 (DTS dropped on many post-2019 LGs → Jellyfin transcodes audio only — cheap); external `.srt`/`.ass`/`.smi` subtitles. Library files kept as MKV/H.264/HEVC + AAC/AC3 will direct-play; anything else Jellyfin handles.

**Decision:** run Jellyfin alongside the FastAPI server. Mishka Hub stays the brain (recs, history, taste); Jellyfin is the dumb-but-excellent playback pipe. The TV runs the official Jellyfin app; the couple can also use Jellyfin directly, but the flagship flow is Mishka Hub's "Play on TV".

## 2. File indexing & TMDB matching

- Media roots configured in `settings` (`media_roots`, e.g. `["/Volumes/Media/Films"]`).
- `POST /api/media/scan` walks roots for `.mkv .mp4 .m4v .avi .ts`, upserts `media_files` (path, size, container; codecs via `ffprobe` when available).
- **Matching to `films.id` (TMDB), in order:**
  1. **Jellyfin join (primary):** query Jellyfin's API for its library items + `ProviderIds.Tmdb`; match by path. Jellyfin's own scraper does the hard work; we inherit it.
  2. **Filename parse (fallback):** `guessit` (the battle-tested Python filename parser used by Radarr-adjacent tooling) → title+year → the same TMDB search/disambiguation pipeline as [Phase 2 §2](PHASE-2-letterboxd-import.md).
  3. **Manual:** unmatched files surface in the same resolution-queue UI pattern as imports (`POST /api/media/match/{file_id}`).
- Poster wall: owned films get a small `kraft` "reel" badge; the recommender gives owned-but-unseen films an availability boost equal to flatrate (you already "subscribe" to your own shelf — pool logic in [PHASE-3 §5](PHASE-3-recommender.md)).

## 3. "Play on TV" control flow

```
UI ▶ Play on TV ──► POST /api/media/play {film_id}
   server: media_files → jellyfin_item_id (via ProviderIds.Tmdb or path join)
           GET  {jellyfin}/Sessions?api_key=…          # find the TV
           pick session where Client == 'Jellyfin webOS' (else 404 tv_not_available)
           POST {jellyfin}/Sessions/{sessionId}/Playing?itemIds={itemId}&playCommand=PlayNow
   TV: Jellyfin app starts playback (direct play or transcode as needed)
   UI: toast "Playing on the living-room TV" + transport row (pause/stop via
       /Sessions/{id}/Playing/{command}) — nice-to-have v2
```

- Server-side config: `MISHKA_JELLYFIN_URL` (e.g. `http://127.0.0.1:8096`) + `MISHKA_JELLYFIN_API_KEY` (Jellyfin admin → API keys). The Jellyfin session-control endpoints require the target session's user to be logged in on the TV app once.
- Caveat: the TV must be **on with the Jellyfin app running** (webOS keeps recent apps warm; wake-over-LAN + `ssap://` webOS launch is a documented stretch goal, not v1). Failure → `503 tv_not_available` with a human hint ([API.md](../API.md)).
- Watched loop-back: Jellyfin marks items played; nightly job reads playback state for matched items and offers "you finished *Heat* on the TV — log it?" prompts (creates `watches` + optional [Phase 5](PHASE-5-letterboxd-writeback.md) Letterboxd log). Keeps Mishka Hub the single source of truth.

## 4. Windows-desktop variant notes

When the server migrates to the Windows machine:
- Jellyfin: native Windows installer/service; point it at the same library folders; `jellyfin_item_id`s change → re-run the Jellyfin join (idempotent by path).
- Mishka Hub `media_roots` become Windows paths; scanner is `pathlib`-portable already.
- `keyring` (Phase 5 credentials) switches to Windows Credential Locker automatically; launchd jobs become Task Scheduler / NSSM services — mapping table in [DEPLOYMENT.md](../DEPLOYMENT.md).
- ffprobe: ship via `ffmpeg` winget package.

## 5. Acceptance criteria

- [ ] Jellyfin installed, library scanned, official webOS app signed in on the LG TV, and a known MKV (H.264+AC3+SRT) **direct-plays** (verify no transcode in Jellyfin dashboard).
- [ ] `POST /api/media/scan` indexes the library; ≥90 % of files auto-match to TMDB ids via the Jellyfin join; remainder resolvable in the manual queue.
- [ ] Owned films show the badge on the poster wall and appear in recommendations even with zero streaming availability.
- [ ] "Play on TV" from a film's detail drawer starts playback on the TV in <10 s (TV on, app warm).
- [ ] TV off → clean `503 tv_not_available` with hint, no hang.
- [ ] Jellyfin playback of a matched film generates the "log it?" prompt within a day (loop-back job).
- [ ] Windows migration notes validated in a dry-run doc review when (if) the move happens.
