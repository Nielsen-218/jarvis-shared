# Jarvis Link (Android helper)

A small Android app (package `com.jarvis.link`, Java only, no libraries, minSdk 26, targetSdk 34, version 1.3) that lets Jarvis
control and watch **Metrolist** (`com.metrolist.music`) on the owner's phone without a tap: play, pause, toggle,
next, previous, seek, like, start radio, play a chosen song, play a search, report what is playing.

Since 1.2 (versionCode 3) it also sends his **Garmin** data to Jarvis through **Android Health Connect**: the Garmin
Connect app writes his watch data into Health Connect, and Jarvis Link reads it (read-only, with his permission) and
posts compact summaries to `POST /api/link/health`. See "Health data (Garmin)" below.

## What it does and does not do

- It holds **Notification access** only because Android requires it before an app may see and control other apps'
  media sessions. It **never reads, stores or sends a notification**: `onNotificationPosted` is ignored.
- It only ever touches the media session whose package is `com.metrolist.music`.
- It talks to **one host only: your Jarvis** (default `https://jarvis-laptop.your-tailnet.ts.net`). HTTPS only;
  plain http is allowed just for `127.0.0.1`, `localhost` and `10.0.2.2` (testing). No analytics, no other hosts.
  Redirects are not followed. The device token is kept in the app's private storage and never logged.
- Battery: while nothing is going on, Jarvis answers each poll with "sleep 45 s" (one small HTTPS request, about 80 an
  hour; the app takes the number from the server, so this needed no rebuild); only during a workout / music request
  (`tools/pca_link.active(...)` / `keep(...)`: the Captain America page open with the picker on, an open workout, any
  music command) does it hold a 25 s long poll. Now-playing is reported when the song or
  play/pause changes, and every 30 s while playing.
- If a song cannot be started in the background, it posts one "Tap to play in Metrolist" notification (Android does
  not let background apps open activities) and reports the failure honestly.

The loop runs inside the notification listener (which the system keeps bound while access is granted), so there is no
foreground service and no permanent notification. See the comment at the top of `LinkEngine.java` for why no
foreground-service type fits.

## Install (owner)

1. On the phone, open the Captain America page > Performance audio > JARVIS LINK > **Download** (the page fetches
   `/api/link/apk` with its own sign-in), or download `https://jarvis-laptop.your-tailnet.ts.net/api/link/apk`
   (you must be signed in on that phone: the link needs your device token, e.g. open it from the Jarvis app page once
   the page links to it, or append `?t=<token>`).
2. Open the downloaded `JarvisLink.apk`. Android asks to allow **Install unknown apps** for your browser: allow it.
3. **Play Protect** may warn about an unknown developer: choose "Install anyway" (it may offer a scan: fine).
4. Open **Jarvis Link**. Allow notifications if asked (only used for the "Tap to play" fallback).
5. Tap **Grant notification access**, find Jarvis Link, turn it on.
   On Android 13+ a sideloaded app may show "Restricted setting": go to Settings > Apps > Jarvis Link > the ⋮ menu >
   **Allow restricted settings**, then grant notification access again.
6. Get a pairing code: on the Captain America page, Performance audio > JARVIS LINK > **Get a code** (POST
   `/api/link/paircode`: one use, 15 minutes), or ask Jarvis to pair a device, or on the laptop run
   `python manage.py pair <name>`. Type the
   6 digits into the app and tap **PAIR** (the app pairs itself as the device `jarvis-link`).
7. Tap **Allow background running** and allow it (battery-optimisation exemption).
8. Start Metrolist, play something, then in Jarvis Link tap **Test: pause / play / next**. The status list should show
   "Metrolist session: Playing: ..." and, within a minute, "Last contact with Jarvis: ...".

Unpair: the **Unpair** button deletes the token on the phone. Removing the device in Jarvis makes the app show
"Pairing was removed" at its next poll and stop.

## Rebuild

Gradle cannot build this offline on the PC (the local Gradle cache has the AGP 8.9.1 metadata but not its jars), so the
build is a plain SDK-tools script:

```
pwsh -File android\jarvis-link\build.ps1
```

It runs aapt2 compile/link, javac against `C:\Android\platforms\android-34\android.jar`, d8, adds `classes.dex`,
zipaligns, signs (v1 + v2 with apksigner from build-tools 35.0.0), verifies, prints `aapt2 dump badging`, and writes
`dist\JarvisLink.apk`. With internet access, `gradlew assembleRelease` (or the `dist` task) also works
(`local.properties` with `sdk.dir=C\:\\Android`, git-ignored).

**Signing key (never in git):** `C:\Users\you\ECC\jarvis-backups\jarvis-link-signing\` holds `jarvis-link.keystore`
and `signing.properties` (storeFile, storePassword, keyAlias, keyPassword). Keep that folder: an update must be signed
with the same key, or Android refuses to install it over the old one. `$env:JARVIS_LINK_SIGNING` points the build at a
different properties file.

The server serves the committed `dist/JarvisLink.apk` from `GET /api/link/apk` (auth required).

## Server protocol (tools/pca_link.py, core/appserver.py)

- `GET /api/link/cmd?wait=25` -> `{"commands": [{"id","name","args","ts"}], "sleep": s, "now": server_time}`
- `POST /api/link/state` `{"playing","title","artist","album","duration_ms","position_ms","media_id","package","liked","session","ts"}`
- `POST /api/link/result` `{"id","ok","detail","state"}`
- `GET /api/link/status` (any device of the person) and `GET /api/link/apk`
- `POST /api/link/health` (link device only, at most 256 KB) and `GET /api/health/status` (any device)
- The `/api/link/cmd` reply also carries `"health": true|false` (please sync Health Connect now)
- Only a device paired as `jarvis-link` may poll or post; commands older than 60 s are never executed.

## Pairing without typing (1.3)

The owner could not get through "read a code on the page, type it in the app". Since 1.3 (versionCode 4) the page's
**Connect this phone** button asks Jarvis for the one-use code and opens `jarvislink://pair?code=...&base=...`
(an Android `intent://` link limited to package `com.jarvis.link`); `MainActivity.handleLink` pairs with it. A base
address other than the one on the screen is only used after a yes in a dialog. The app's own button
"CONNECT TO JARVIS (no typing)" opens `<address>/captain.html?pair=1`, which shows the same button. Typing a code
still works.

## Health data (Garmin) through Health Connect (1.2)

Why this route: Garmin's official API is for approved businesses, and the old direct login (`garmin_login.py`) is
blocked by Garmin's portal (HTTP 403, a bot challenge). Garmin Connect on the phone can write to Android Health
Connect; this app reads it there. Code: `HealthSync.java` (when and what; no Health Connect classes) and `Hc.java`
(every `android.health.connect` call, loaded only on Android 14+). Server: `tools/pca_health.py`.

- **Android 14 (API 34) or newer only.** On Android 13 and older Health Connect is a separate app that only the Jetpack
  library can reach; this app has no libraries, so it says "Health data needs Android 14 or newer" and everything
  else keeps working.
- **Permissions (read only):** sleep, heart rate, resting heart rate, HRV, exercise, distance, steps, weight, VO2 max,
  total calories; on Android 15+ also `READ_HEALTH_DATA_IN_BACKGROUND` and `READ_HEALTH_DATA_HISTORY`. They are asked
  with `Activity.requestPermissions` (on Android 14+ health permissions are runtime permissions: Jetpack's own
  `HealthPermissionsRequestModuleContract` is `RequestMultiplePermissions`). The privacy screen (`PrivacyActivity`) is
  what Health Connect opens from "why does this app need this" (`VIEW_PERMISSION_USAGE` + `HEALTH_PERMISSIONS` alias,
  guarded by `START_VIEW_PERMISSION_USAGE`; plus the older `androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE`).
- **What is sent:** the first sync reads the last 30 days, later syncs re-read from 36 hours before the last good sync.
  Only records whose data origin is Garmin Connect (`com.garmin.android.apps.connectmobile`) unless "Include other
  sources" is on. Sleep per session (start, end, minutes asleep, minutes deep/light/REM/awake); heart rate as per-day
  min/avg/max and per-workout avg/max (never raw samples); resting HR per day; HRV RMSSD nightly average; workouts
  (type, start, end, title, distance, avg speed, calories, avg/max HR); steps per day (largest source per day);
  weight entries (kg; Jarvis stores lb); VO2 max entries; calories per day. Times are ISO 8601 with the phone's
  offset. Each POST stays under 180 000 characters; the last one carries `"final": true`; the sync cursor moves only
  after Jarvis answered 200 to every chunk.
- **When:** "Sync now"; when the app is opened (at most every 10 minutes); on Android 15+ with the background
  permission, from the link loop every 2 hours and when the poll reply says `"health": true` (Jarvis asks between
  05:00 and 10:00 when the last sync is over 3 hours old, when a workout starts, and from the page's Sync now). On
  Android 14 there is no background read: the screen says "On Android 14, open Jarvis Link to sync". The background
  loop lives in the notification listener, so background syncs need Notification access granted too.
- **Errors:** Health Connect missing or refusing, permission removed mid-sync, rate limits (15 minutes back-off,
  doubling to 4 hours), 401 (the pairing is forgotten), 404 (Jarvis too old) all end as one line on the screen. Logs
  carry error codes and counts only, never a health value or the token.
- **Stop sharing health data** stops syncing and opens Health Connect's permission screen for this app
  (`android.health.connect.action.MANAGE_HEALTH_PERMISSIONS` with the package name).

### Garmin's side (his steps)
1. Update Garmin Connect (reports say 5.14.1 or newer) and open it.
2. Turn on Health Connect in Garmin Connect (reported under Connected Apps in its settings; Garmin's own support
   page could not be opened from here, so the wording may differ), and allow the data types.
3. In Jarvis Link tap **Allow Jarvis to read health data** and allow the list, then **Sync now**.

Reported, not verified here: Garmin writes sleep (with stages), heart rate, steps, distance, calories, workouts,
weight and body fat after each watch sync; Body Battery, Training Readiness, HRV status, stress, and (per several
reports) resting heart rate, HRV and VO2 max are not shared. Jarvis shows only what arrives.

## Only testable on a real phone

- That Metrolist treats this app's controller as trusted (Notification-access holder) and accepts
  `playFromMediaId("auto:song:<b64>")`, the `auto:browse-song:` search form, `playFromSearch`, `TOGGLE_LIKE`,
  `TOGGLE_START_RADIO` and `setRating` from a legacy `android.media.session.MediaController`.
- Whether the session's media id / title changes within the 3 s / 6 s windows `play_id` waits for.
- Whether the like state is readable from the metadata's user rating.
- Whether the PLAY media key wakes Metrolist when it has no session.
- How reliably the listener stays bound (and the loop keeps polling) with the screen off, in Doze, and after a reboot,
  with and without the battery exemption; the phone maker's own battery manager may still kill it.
- Network behaviour on mobile data through the Tailscale funnel (long polls of 25 s).
- The "Restricted settings" step on his Android version, and the Play Protect prompt.
- Health Connect (1.2): the permission dialog for health types from `Activity.requestPermissions` on his Android
  version; which types Garmin Connect really writes (resting HR, HRV, VO2 max?) and how soon after a watch sync;
  whether sleep stages arrive; whether background reads work on his phone (Android 15+); real payload sizes.
