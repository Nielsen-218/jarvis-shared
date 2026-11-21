package com.jarvis.link;

import android.content.ComponentName;
import android.content.Context;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.media.MediaMetadata;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/**
 * The link loop, hosted by LinkListener (the notification listener the system keeps bound while Notification access
 * is granted), so no foreground service is needed: a foreground service would need a type on Android 14+, and none
 * fits ("mediaPlayback" is Metrolist's job, "dataSync" gets a 6-hour daily cap on Android 15 for apps targeting it,
 * "specialUse" is a Play-review escape hatch, "connectedDevice" needs Bluetooth/USB-style permissions).
 *
 * Threads (never the main thread for network): "link-poll" long-polls /api/link/cmd and runs commands one at a time;
 * "link-state" posts now-playing reports; "link-media" receives media-session callbacks.
 */
final class LinkEngine {
    private static final String TAG = "JarvisLink";
    private static final Set<String> COMMANDS = new HashSet<>(Arrays.asList(
            "play", "pause", "toggle", "next", "previous", "seek", "like", "radio", "play_id", "play_search", "state", "stop_link"));
    private static final int POLL_WAIT_S = 25;
    private static final long MAX_BACKOFF_MS = 60_000;
    private static final double COMMAND_TTL_S = 60;

    private static LinkEngine instance;

    private final Context context;
    private final Prefs prefs;
    private final Metro metro;
    private final Object wake = new Object();
    private volatile boolean running = true;
    private boolean nudged;
    private Thread poller;
    private HandlerThread mediaThread;
    private ScheduledExecutorService stateExec;
    private java.util.concurrent.ExecutorService healthExec;
    private ScheduledFuture<?> pendingPost;
    private MediaSessionManager.OnActiveSessionsChangedListener sessionsListener;
    private MediaController watched;
    private String lastSignature = "";
    private final MediaController.Callback callback = new MediaController.Callback() {
        @Override public void onPlaybackStateChanged(PlaybackState state) { stateChanged(); }
        @Override public void onMetadataChanged(MediaMetadata metadata) { stateChanged(); }
        @Override public void onSessionDestroyed() { attach(); stateChanged(); }
    };

    private LinkEngine(Context context) {
        this.context = context.getApplicationContext();
        this.prefs = new Prefs(this.context);
        this.metro = new Metro(this.context);
    }

    static synchronized void start(Context context) {
        if (instance != null) return;
        instance = new LinkEngine(context);
        instance.begin();
    }

    static synchronized void stop() {
        if (instance == null) return;
        instance.end();
        instance = null;
    }

    static synchronized boolean isRunning() { return instance != null; }

    /** Wake the loop now (paired, unpaired, music started). */
    static synchronized void nudge() {
        if (instance != null) instance.wakeUp();
    }

    private void begin() {
        mediaThread = new HandlerThread("link-media");
        mediaThread.start();
        Handler h = new Handler(mediaThread.getLooper());
        stateExec = Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "link-state"));
        h.post(() -> {
            try {
                MediaSessionManager msm = context.getSystemService(MediaSessionManager.class);
                sessionsListener = controllers -> attach();
                if (msm != null) msm.addOnActiveSessionsChangedListener(sessionsListener, new ComponentName(context, LinkListener.class), h);
            } catch (SecurityException e) {
                Log.w(TAG, "No notification access; media sessions are not visible.");
            }
            attach();
        });
        // at least every 30 s while playing (the server's position/age stays fresh during a workout)
        stateExec.scheduleWithFixedDelay(() -> {
            if (running && linked() && Metro.playing(metro.controller())) postState(true);
        }, 30, 30, TimeUnit.SECONDS);
        healthExec = Executors.newSingleThreadExecutor(r -> new Thread(r, "link-health"));
        poller = new Thread(this::loop, "link-poll");
        poller.start();
    }

    private void end() {
        running = false;
        wakeUp();
        if (poller != null) poller.interrupt();
        Handler h = mediaThread == null ? null : new Handler(mediaThread.getLooper());
        if (h != null) {
            h.post(() -> {
                try {
                    MediaSessionManager msm = context.getSystemService(MediaSessionManager.class);
                    if (msm != null && sessionsListener != null) msm.removeOnActiveSessionsChangedListener(sessionsListener);
                } catch (RuntimeException ignored) { }
                if (watched != null) {
                    try { watched.unregisterCallback(callback); } catch (RuntimeException ignored) { }
                    watched = null;
                }
                mediaThread.quitSafely();
            });
        }
        if (stateExec != null) stateExec.shutdownNow();
        if (healthExec != null) healthExec.shutdownNow();
    }

    /** Health Connect in the background (Android 15+ with the background-read permission only): every 2 hours, or when
     *  Jarvis asked ("health": true in the poll reply), on its own thread so the poll never waits for it. */
    private void maybeHealth(boolean asked) {
        if (healthExec == null || healthExec.isShutdown() || !HealthSync.due(context, prefs, false, asked)) return;
        try {
            healthExec.execute(() -> HealthSync.sync(context, asked ? "asked" : "periodic"));
        } catch (RuntimeException ignored) { }
    }

    private boolean linked() { return prefs.paired() && !prefs.stopped(); }

    // ---- media session watching (on link-media) ----------------------------------------------------------------------
    private void attach() {
        MediaController c = metro.controller();
        if (watched != null && c != null && watched.getSessionToken().equals(c.getSessionToken())) return;
        if (watched != null) {
            try { watched.unregisterCallback(callback); } catch (RuntimeException ignored) { }
        }
        watched = c;
        if (c != null && mediaThread != null) c.registerCallback(callback, new Handler(mediaThread.getLooper()));
        stateChanged();
    }

    private void stateChanged() {
        if (running && linked()) postState(false);
    }

    /** Debounced (0.5 s) state report; only when something visible changed, unless forced. */
    private synchronized void postState(boolean force) {
        if (stateExec == null || stateExec.isShutdown()) return;
        if (pendingPost != null && !pendingPost.isDone()) return;
        pendingPost = stateExec.schedule(() -> {
            JSONObject st = metro.state();
            String sig = st.optBoolean("playing") + "|" + st.optString("media_id") + "|" + st.optString("title") + "|"
                    + st.opt("liked") + "|" + st.optBoolean("session");
            if (!force && sig.equals(lastSignature)) return;
            try {
                JSONObject r = Net.call(prefs.url(), "POST", "/api/link/state", prefs.token(), st, 20000);
                lastSignature = sig;
                prefs.contact();
                prefs.note("playing", describe(st));
                if (r.optInt("sleep", 300) == 0) wakeUp();       // a workout is on: poll now instead of after the sleep
            } catch (Net.HttpError e) {
                handleAuth(e);
            } catch (IOException e) {
                Log.i(TAG, "State report failed: " + e.getClass().getSimpleName());
            }
        }, 500, TimeUnit.MILLISECONDS);
    }

    static String describe(JSONObject st) {
        if (!st.optBoolean("session", true)) return "Metrolist: no active session";
        String t = st.optString("title", ""), a = st.optString("artist", "");
        if (t.isEmpty() || t.equals("null")) return st.optBoolean("playing") ? "Playing (no title)" : "Paused";
        return (st.optBoolean("playing") ? "Playing: " : "Paused: ") + t + (a.isEmpty() || a.equals("null") ? "" : " - " + a);
    }

    // ---- the poll loop (on link-poll) -------------------------------------------------------------------------------
    private void loop() {
        long backoff = 1000;
        while (running) {
            if (!linked()) {
                sleepUntilNudged(0);
                continue;
            }
            long started = SystemClock.elapsedRealtime();
            try {
                JSONObject r = Net.call(prefs.url(), "GET", "/api/link/cmd?wait=" + POLL_WAIT_S, prefs.token(), null,
                        (POLL_WAIT_S + 15) * 1000);
                long received = SystemClock.elapsedRealtime();   // a command's age keeps growing while earlier ones run
                prefs.contact();
                backoff = 1000;
                JSONArray cmds = r.optJSONArray("commands");
                double now = r.optDouble("now", 0);
                int n = cmds == null ? 0 : cmds.length();
                for (int i = 0; i < n && running && linked(); i++) {
                    JSONObject cmd = cmds.optJSONObject(i);
                    if (cmd != null) execute(cmd, now, received);
                }
                maybeHealth(r.optBoolean("health", false));
                long sleep = Math.max(0, Math.min(3600, r.optLong("sleep", 300)));
                if (sleep > 0) sleepUntilNudged(sleep * 1000);
                else if (n == 0 && SystemClock.elapsedRealtime() - started < 1000) sleepUntilNudged(1000);  // never spin
            } catch (Net.HttpError e) {
                if (!handleAuth(e)) {
                    prefs.note("error", e.getMessage());
                    sleepUntilNudged(backoff);
                    backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
                }
            } catch (IOException | RuntimeException e) {
                prefs.note("error", "Cannot reach Jarvis (" + e.getClass().getSimpleName() + ")");
                sleepUntilNudged(backoff);
                backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
            }
        }
    }

    /** 401: the pairing was removed in Jarvis -> forget the token and stop politely. 403: not a link pairing. */
    private boolean handleAuth(Net.HttpError e) {
        if (e.status == 401) {
            prefs.unpair("Pairing was removed in Jarvis. Pair again with a new code.");
            return true;
        }
        if (e.status == 403) {
            prefs.setStopped(true);
            prefs.note("error", e.getMessage());
            return true;
        }
        return false;
    }

    /** A command's age at the moment it would start: the server's age at the poll response plus the real time that has
     *  passed on this phone since then (a command queued behind a slow play_id must not run 80 s late). */
    static boolean tooOld(double serverNow, double ts, long receivedMs, long nowMs) {
        if (serverNow <= 0) return false;
        return serverNow - ts + Math.max(0, nowMs - receivedMs) / 1000.0 > COMMAND_TTL_S;
    }

    private void execute(JSONObject cmd, double serverNow, long receivedMs) {
        String id = cmd.optString("id", ""), name = cmd.optString("name", "");
        JSONObject args = cmd.optJSONObject("args");
        if (args == null) args = new JSONObject();
        double ts = cmd.optDouble("ts", 0);
        JSONObject res;
        if (tooOld(serverNow, ts, receivedMs, SystemClock.elapsedRealtime())) {
            res = Metro.result(false, "Dropped: the command was older than 60 s.");
        } else if (!COMMANDS.contains(name)) {
            res = Metro.result(false, "Unknown command; refused.");
        } else if (name.equals("stop_link")) {
            prefs.setStopped(true);
            res = Metro.result(true, "Jarvis Link stopped. Open the app to start it again.");
        } else {
            res = metro.run(name, args);
        }
        prefs.note("last_cmd", name);
        prefs.note("last_result", res.optString("detail"));
        try {
            res.put("id", id);
            res.put("state", metro.state());
        } catch (JSONException ignored) { }
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                Net.call(prefs.url(), "POST", "/api/link/result", prefs.token(), res, 20000);
                prefs.contact();
                return;
            } catch (Net.HttpError e) {
                if (handleAuth(e) || e.status == 400) return;
            } catch (IOException e) {
                SystemClock.sleep(1000);
            }
        }
    }

    private void wakeUp() {
        synchronized (wake) {
            nudged = true;
            wake.notifyAll();
        }
    }

    private void sleepUntilNudged(long ms) {
        synchronized (wake) {
            long end = SystemClock.elapsedRealtime() + ms;
            while (running && !nudged) {
                long left = ms == 0 ? 0 : end - SystemClock.elapsedRealtime();
                if (ms != 0 && left <= 0) break;
                try {
                    wake.wait(left);
                } catch (InterruptedException e) {
                    return;
                }
            }
            nudged = false;
        }
    }
}
