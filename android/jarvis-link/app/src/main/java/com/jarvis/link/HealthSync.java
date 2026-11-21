package com.jarvis.link;

import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.time.Instant;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Garmin -> Jarvis through Android Health Connect. The Garmin Connect app writes his watch data into Health Connect;
 * this reads it (with his permission, read-only) and POSTs compact summaries to his own Jarvis (/api/link/health).
 *
 * Android 14 (API 34) or newer only: Health Connect is part of the OS there and readable with the framework API
 * (android.health.connect, see Hc). On Android 13 and older Health Connect is a separate app reachable only through the
 * Jetpack library, which this app does not use: the screen says so and everything else keeps working.
 *
 * When: "Sync now", whenever the app is opened (foreground), and on Android 15+ with the background-read permission
 * from the link loop (LinkEngine) at most every 2 hours or when Jarvis's poll reply says "health": true.
 * What: changes since the last successful sync (the first sync: the last 30 days), Garmin Connect's records only unless
 * "include other sources" is on. The cursor moves only after Jarvis answered 200 to every chunk.
 * Never on the main thread (sync() blocks); never logs a health value or the token.
 */
final class HealthSync {
    private static final String TAG = "JarvisLink";
    static final String P_SLEEP = "android.permission.health.READ_SLEEP";
    static final String P_HR = "android.permission.health.READ_HEART_RATE";
    static final String P_RESTING = "android.permission.health.READ_RESTING_HEART_RATE";
    static final String P_HRV = "android.permission.health.READ_HEART_RATE_VARIABILITY";
    static final String P_EXERCISE = "android.permission.health.READ_EXERCISE";
    static final String P_DISTANCE = "android.permission.health.READ_DISTANCE";
    static final String P_STEPS = "android.permission.health.READ_STEPS";
    static final String P_WEIGHT = "android.permission.health.READ_WEIGHT";
    static final String P_VO2 = "android.permission.health.READ_VO2_MAX";
    static final String P_CALORIES = "android.permission.health.READ_TOTAL_CALORIES_BURNED";
    /** Android 15 (API 35): read while the app is in the background. */
    static final String P_BACKGROUND = "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND";
    /** Android 15 (API 35): read data older than 30 days before the first grant (asked for, not needed for 30 days). */
    static final String P_HISTORY = "android.permission.health.READ_HEALTH_DATA_HISTORY";
    static final String[] TYPES = {P_SLEEP, P_HR, P_RESTING, P_HRV, P_EXERCISE, P_DISTANCE, P_STEPS, P_WEIGHT, P_VO2, P_CALORIES};

    static final long BACKGROUND_EVERY_MS = 2 * 3600_000L;
    static final long ASKED_GAP_MS = 10 * 60_000L;         // a server request is honoured at most every 10 minutes
    static final long OPEN_GAP_MS = 10 * 60_000L;          // opening the app syncs at most every 10 minutes
    static final long FIRST_DAYS = 30;
    static final long OVERLAP_MS = 36 * 3600_000L;         // re-read the last day and a half: late sleep, partial days
    static final int CHUNK_CHARS = 180_000;                // each POST stays under 200 KB
    static final String[] KINDS = {"sleep", "resting_hr", "hr_daily", "hrv", "exercise", "steps", "weight", "vo2max", "calories"};

    private static final AtomicBoolean RUNNING = new AtomicBoolean(false);

    private HealthSync() {}

    static boolean supported() { return Build.VERSION.SDK_INT >= 34; }

    static boolean available(Context c) { return supported() && Hc.manager(c) != null; }

    /** The permissions the "Allow" button asks for: the read types, plus background and history where the OS has them. */
    static String[] wanted() {
        List<String> out = new ArrayList<>();
        for (String p : TYPES) out.add(p);
        if (Build.VERSION.SDK_INT >= 35) { out.add(P_BACKGROUND); out.add(P_HISTORY); }
        return out.toArray(new String[0]);
    }

    static Set<String> granted(Context c) {
        Set<String> out = new HashSet<>();
        if (!supported()) return out;
        for (String p : wanted()) {
            if (c.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED) out.add(p);
        }
        return out;
    }

    static int typesGranted(Context c) {
        Set<String> g = granted(c);
        int n = 0;
        for (String p : TYPES) if (g.contains(p)) n++;
        return n;
    }

    static boolean backgroundAllowed(Context c) {
        return Build.VERSION.SDK_INT >= 35 && granted(c).contains(P_BACKGROUND);
    }

    /** Should a sync start now? foreground = the screen is open; asked = Jarvis's poll said "health": true. */
    static boolean due(Context c, Prefs prefs, boolean foreground, boolean asked) {
        if (!available(c) || !prefs.paired() || prefs.flag("health_off") || typesGranted(c) == 0 || RUNNING.get()) return false;
        long now = System.currentTimeMillis();
        if (now < prefs.num("health_backoff_until")) return false;
        long last = prefs.num("health_try_at");
        if (foreground) return now - last >= OPEN_GAP_MS;
        if (!backgroundAllowed(c)) return false;
        return asked ? now - last >= ASKED_GAP_MS : now - last >= BACKGROUND_EVERY_MS;
    }

    static boolean running() { return RUNNING.get(); }

    /** One sync, blocking (call from a worker thread). -> a sentence for the screen. */
    static String sync(Context context, String why) {
        Context c = context.getApplicationContext();
        Prefs prefs = new Prefs(c);
        if (!supported()) return "Health data needs Android 14 or newer.";
        if (!available(c)) return "Health Connect is not available on this phone. Update Android or install Health Connect from the Play Store.";
        if (!prefs.paired()) return "Pair Jarvis Link with Jarvis first.";
        Set<String> granted = granted(c);
        if (typesGranted(c) == 0) return "Tap \"Allow Jarvis to read health data\" first.";
        if (!RUNNING.compareAndSet(false, true)) return "A sync is already running.";
        long started = System.currentTimeMillis();
        prefs.setNum("health_try_at", started);
        try {
            long cursor = prefs.num("health_cursor");
            Instant to = Instant.ofEpochMilli(started);
            Instant from = cursor <= 0
                    ? to.minusSeconds(FIRST_DAYS * 86400)
                    : Instant.ofEpochMilli(cursor - OVERLAP_MS);
            from = from.atZone(ZoneId.systemDefault()).toLocalDate().atStartOfDay(ZoneId.systemDefault()).toInstant();
            boolean garminOnly = !prefs.flag("health_other");
            JSONObject data = Hc.read(c, from, to, garminOnly, granted);
            int found = data.optInt("_found", 0);
            data.remove("_found");
            if (found > 0) prefs.setFlag("garmin_seen", true);
            List<JSONObject> chunks = chunks(data, prefs.flag("garmin_seen"));
            for (JSONObject chunk : chunks) {
                Net.call(prefs.url(), "POST", "/api/link/health", prefs.token(), chunk, 30000);
            }
            prefs.contact();
            prefs.setNum("health_cursor", started);
            prefs.setNum("health_ok_at", System.currentTimeMillis());
            prefs.setNum("health_backoff_ms", 0);
            prefs.setNum("health_backoff_until", 0);
            prefs.setText("health_counts", counts(data));
            prefs.setText("health_error", "");
            String msg = "Synced " + counts(data) + (garminOnly && found == 0 && !prefs.flag("garmin_seen")
                    ? ". No Garmin data yet: turn on Health Connect in the Garmin Connect app." : ".");
            Log.i(TAG, "Health sync done (" + why + "), " + chunks.size() + " post(s).");
            return msg;
        } catch (Hc.Failure e) {
            if (e.rateLimited()) backoff(prefs);
            prefs.setText("health_error", e.getMessage());
            Log.i(TAG, "Health sync stopped: Health Connect code " + e.code);
            return e.getMessage();
        } catch (Net.HttpError e) {
            String msg;
            if (e.status == 401) {
                prefs.unpair("Pairing was removed in Jarvis. Pair again with a new code.");
                msg = "Jarvis no longer knows this phone: pair again.";
            } else if (e.status == 404) {
                msg = "This Jarvis does not take health data yet (it needs an update).";
            } else {
                msg = e.getMessage();
                if (e.status == 429 || e.status >= 500) backoff(prefs);
            }
            prefs.setText("health_error", msg);
            Log.i(TAG, "Health sync not accepted: HTTP " + e.status);
            return msg;
        } catch (IOException e) {
            prefs.setText("health_error", "Cannot reach Jarvis (" + e.getClass().getSimpleName() + ").");
            return prefs.text("health_error");
        } catch (JSONException | RuntimeException e) {
            prefs.setText("health_error", "The sync failed (" + e.getClass().getSimpleName() + ").");
            Log.i(TAG, "Health sync failed: " + e.getClass().getSimpleName());
            return prefs.text("health_error");
        } finally {
            RUNNING.set(false);
        }
    }

    private static void backoff(Prefs prefs) {
        long next = Math.min(Math.max(15 * 60_000L, prefs.num("health_backoff_ms") * 2), 4 * 3600_000L);
        prefs.setNum("health_backoff_ms", next);
        prefs.setNum("health_backoff_until", System.currentTimeMillis() + next);
    }

    /** "3 nights, 2 workouts, 7 days of steps" (counts only, for the screen). */
    static String counts(JSONObject data) {
        StringBuilder s = new StringBuilder();
        add(s, data.optJSONArray("sleep"), "night", "nights");
        add(s, data.optJSONArray("exercise"), "workout", "workouts");
        add(s, data.optJSONArray("resting_hr"), "resting HR day", "resting HR days");
        add(s, data.optJSONArray("steps"), "day of steps", "days of steps");
        add(s, data.optJSONArray("weight"), "weight", "weights");
        add(s, data.optJSONArray("vo2max"), "VO2 max", "VO2 max values");
        add(s, data.optJSONArray("hrv"), "HRV night", "HRV nights");
        return s.length() == 0 ? "nothing new" : s.toString();
    }

    private static void add(StringBuilder s, JSONArray a, String one, String many) {
        int n = a == null ? 0 : a.length();
        if (n == 0) return;
        if (s.length() > 0) s.append(", ");
        s.append(n).append(' ').append(n == 1 ? one : many);
    }

    /** Split the lists into POST bodies of at most CHUNK_CHARS characters; the last one says "final": true. */
    static List<JSONObject> chunks(JSONObject data, boolean garminSeen) throws JSONException {
        List<JSONObject> out = new ArrayList<>();
        JSONObject cur = header(garminSeen);
        int size = cur.toString().length();
        for (String kind : KINDS) {
            JSONArray rows = data.optJSONArray(kind);
            if (rows == null) continue;
            for (int i = 0; i < rows.length(); i++) {
                Object row = rows.get(i);
                int len = String.valueOf(row).length() + 2;
                if (size + len > CHUNK_CHARS && size > 200) {
                    out.add(cur);
                    cur = header(garminSeen);
                    size = cur.toString().length();
                }
                JSONArray list = cur.optJSONArray(kind);
                if (list == null) { list = new JSONArray(); cur.put(kind, list); size += kind.length() + 6; }
                list.put(row);
                size += len;
            }
        }
        cur.put("final", true);
        out.add(cur);
        return out;
    }

    private static JSONObject header(boolean garminSeen) throws JSONException {
        return new JSONObject().put("v", 1).put("app", "1.2").put("final", false).put("garmin_found", garminSeen)
                .put("sources", new JSONArray().put(Hc.GARMIN));
    }
}
