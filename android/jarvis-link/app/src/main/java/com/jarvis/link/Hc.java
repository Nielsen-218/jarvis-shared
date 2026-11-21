package com.jarvis.link;

import android.content.Context;
import android.health.connect.HealthConnectException;
import android.health.connect.HealthConnectManager;
import android.health.connect.ReadRecordsRequestUsingFilters;
import android.health.connect.ReadRecordsResponse;
import android.health.connect.TimeInstantRangeFilter;
import android.health.connect.datatypes.DataOrigin;
import android.health.connect.datatypes.DistanceRecord;
import android.health.connect.datatypes.ExerciseSessionRecord;
import android.health.connect.datatypes.HeartRateRecord;
import android.health.connect.datatypes.HeartRateVariabilityRmssdRecord;
import android.health.connect.datatypes.IntervalRecord;
import android.health.connect.datatypes.Record;
import android.health.connect.datatypes.RestingHeartRateRecord;
import android.health.connect.datatypes.SleepSessionRecord;
import android.health.connect.datatypes.StepsRecord;
import android.health.connect.datatypes.TotalCaloriesBurnedRecord;
import android.health.connect.datatypes.Vo2MaxRecord;
import android.health.connect.datatypes.WeightRecord;
import android.os.OutcomeReceiver;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Everything that touches android.health.connect (the framework Health Connect API, Android 14 / API 34+). Only
 * HealthSync calls in here, and only after it checked Build.VERSION.SDK_INT >= 34, so this class is never loaded on an
 * older phone. Every call blocks its caller (a worker thread, never the main thread) until Health Connect answers.
 *
 * Reads only: nothing is ever written to Health Connect. Values are summarised here (per day, per night, per workout)
 * so raw second-by-second heart rate never leaves the phone. Nothing here logs a health value.
 */
final class Hc {
    static final String GARMIN = "com.garmin.android.apps.connectmobile";
    private static final int PAGE = 1000;
    private static final int MAX_PAGES = 200;          // 200 000 records per type at most, whatever happens
    private static final long WAIT_S = 60;
    private static final DateTimeFormatter DAY = DateTimeFormatter.ISO_LOCAL_DATE;
    private static final DateTimeFormatter TIME = DateTimeFormatter.ISO_OFFSET_DATE_TIME;

    /** A refusal from Health Connect, with its error code (HealthConnectException.ERROR_*) or -1. */
    static final class Failure extends Exception {
        final int code;
        Failure(int code, String message) { super(message); this.code = code; }
        boolean rateLimited() { return code == HealthConnectException.ERROR_RATE_LIMIT_EXCEEDED; }
        boolean security() { return code == HealthConnectException.ERROR_SECURITY; }
    }

    private Hc() {}

    static HealthConnectManager manager(Context c) {
        try {
            return c.getSystemService(HealthConnectManager.class);
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** One read of every page of one record type in [from, to), only records written by Garmin Connect when garminOnly. */
    private static <T extends Record> List<T> readAll(HealthConnectManager m, Class<T> type, Instant from, Instant to,
                                                      boolean garminOnly) throws Failure {
        List<T> out = new ArrayList<>();
        long token = -1;
        for (int page = 0; page < MAX_PAGES; page++) {
            ReadRecordsRequestUsingFilters.Builder<T> b = new ReadRecordsRequestUsingFilters.Builder<>(type)
                    .setTimeRangeFilter(new TimeInstantRangeFilter.Builder().setStartTime(from).setEndTime(to).build())
                    .setPageSize(PAGE);
            if (garminOnly) b.addDataOrigins(new DataOrigin.Builder().setPackageName(GARMIN).build());
            if (token != -1) b.setPageToken(token);
            ReadRecordsResponse<T> r = await(m, b.build());
            for (T rec : r.getRecords()) {
                if (!garminOnly || GARMIN.equals(origin(rec))) out.add(rec);
            }
            token = r.getNextPageToken();
            if (token == -1) break;
        }
        return out;
    }

    private static <T extends Record> ReadRecordsResponse<T> await(HealthConnectManager m, ReadRecordsRequestUsingFilters<T> req)
            throws Failure {
        final Object[] box = new Object[2];
        final CountDownLatch done = new CountDownLatch(1);
        try {
            m.readRecords(req, Runnable::run, new OutcomeReceiver<ReadRecordsResponse<T>, HealthConnectException>() {
                @Override public void onResult(ReadRecordsResponse<T> result) { box[0] = result; done.countDown(); }
                @Override public void onError(HealthConnectException error) { box[1] = error; done.countDown(); }
            });
        } catch (SecurityException e) {
            throw new Failure(HealthConnectException.ERROR_SECURITY, "Permission to read health data was removed.");
        } catch (RuntimeException e) {
            throw new Failure(-1, "Health Connect refused the read (" + e.getClass().getSimpleName() + ").");
        }
        try {
            if (!done.await(WAIT_S, TimeUnit.SECONDS)) throw new Failure(-1, "Health Connect did not answer in time.");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new Failure(-1, "The sync was stopped.");
        }
        if (box[1] != null) {
            HealthConnectException e = (HealthConnectException) box[1];
            String why;
            switch (e.getErrorCode()) {
                case HealthConnectException.ERROR_RATE_LIMIT_EXCEEDED: why = "Health Connect asked to slow down; trying again later."; break;
                case HealthConnectException.ERROR_SECURITY: why = "Permission to read health data was removed."; break;
                case HealthConnectException.ERROR_UNSUPPORTED_OPERATION: why = "Health Connect on this phone does not support this; update it from the Play Store."; break;
                case HealthConnectException.ERROR_DATA_SYNC_IN_PROGRESS: why = "Health Connect is busy syncing; trying again later."; break;
                default: why = "Health Connect could not be read (error " + e.getErrorCode() + ").";
            }
            throw new Failure(e.getErrorCode(), why);
        }
        @SuppressWarnings("unchecked") ReadRecordsResponse<T> r = (ReadRecordsResponse<T>) box[0];
        return r;
    }

    private static String origin(Record r) {
        try {
            DataOrigin o = r.getMetadata().getDataOrigin();
            return o == null ? null : o.getPackageName();
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static String time(Instant t, ZoneId zone) { return t.atZone(zone).toOffsetDateTime().format(TIME); }
    private static String day(Instant t, ZoneId zone) { return t.atZone(zone).toLocalDate().format(DAY); }

    static String typeName(int t) {
        switch (t) {
            case 33: return "running";
            case 34: return "running_treadmill";
            case 53: return "walking";
            case 21: return "hiking";
            case 4: return "biking";
            case 5: return "biking_stationary";
            case 48: return "swimming_open_water";
            case 49: return "swimming_pool";
            case 45: return "strength_training";
            case 55: return "weightlifting";
            case 20: return "hiit";
            case 31: return "rowing";
            case 61: return "rowing_machine";
            case 60: return "elliptical";
            case 57: return "yoga";
            case 8: return "calisthenics";
            default: return "other";
        }
    }

    /** Running sums for one day's heart rate. */
    private static final class Acc {
        long n, sum, min = Long.MAX_VALUE, max;
        void add(long v) { n++; sum += v; min = Math.min(min, v); max = Math.max(max, v); }
    }

    /**
     * Read [from, to) and summarise -> {"sleep": [...], "resting_hr": [...], "hr_daily": [...], "hrv": [...],
     * "exercise": [...], "steps": [...], "weight": [...], "vo2max": [...], "calories": [...], "_found": n of Garmin
     * records}. Types the app was not allowed to read are skipped (an empty list), never an error.
     */
    static JSONObject read(Context c, Instant from, Instant to, boolean garminOnly, java.util.Set<String> granted)
            throws Failure, JSONException {
        HealthConnectManager m = manager(c);
        if (m == null) throw new Failure(-1, "Health Connect is not available on this phone.");
        ZoneId zone = ZoneId.systemDefault();
        JSONObject out = new JSONObject();
        int found = 0;

        // sleep: one entry per session, with minutes per stage when the watch wrote stages
        JSONArray sleep = new JSONArray();
        if (granted.contains(HealthSync.P_SLEEP)) {
            for (SleepSessionRecord s : readAll(m, SleepSessionRecord.class, from, to, garminOnly)) {
                found += GARMIN.equals(origin(s)) ? 1 : 0;
                JSONObject o = new JSONObject().put("start", time(s.getStartTime(), zone)).put("end", time(s.getEndTime(), zone));
                long deep = 0, light = 0, rem = 0, awake = 0, asleep = 0;
                List<SleepSessionRecord.Stage> stages = s.getStages();
                for (SleepSessionRecord.Stage st : stages == null ? new ArrayList<SleepSessionRecord.Stage>() : stages) {
                    long min = Math.max(0, (st.getEndTime().getEpochSecond() - st.getStartTime().getEpochSecond()) / 60);
                    switch (st.getType()) {
                        case SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING_DEEP: deep += min; asleep += min; break;
                        case SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING_LIGHT: light += min; asleep += min; break;
                        case SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING_REM: rem += min; asleep += min; break;
                        case SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING: asleep += min; break;
                        case SleepSessionRecord.StageType.STAGE_TYPE_AWAKE:
                        case SleepSessionRecord.StageType.STAGE_TYPE_AWAKE_IN_BED:
                        case SleepSessionRecord.StageType.STAGE_TYPE_AWAKE_OUT_OF_BED: awake += min; break;
                        default: break;
                    }
                }
                if (asleep > 0) {
                    o.put("minutes", asleep).put("deep_min", deep).put("light_min", light).put("rem_min", rem).put("awake_min", awake);
                }
                String src = origin(s);
                if (src != null) o.put("src", src);
                sleep.put(o);
            }
        }
        out.put("sleep", sleep);

        // exercise sessions first: heart rate, distance and calories are then matched to them
        List<ExerciseSessionRecord> sessions = granted.contains(HealthSync.P_EXERCISE)
                ? readAll(m, ExerciseSessionRecord.class, from, to, garminOnly) : new ArrayList<>();
        int ns = sessions.size();
        double[] dist = new double[ns], kcal = new double[ns];
        Acc[] hrOf = new Acc[ns];
        for (int i = 0; i < ns; i++) hrOf[i] = new Acc();

        // heart rate: per day min/avg/max and per session avg/max; the samples themselves are never sent
        TreeMap<String, Acc> hrDay = new TreeMap<>();
        if (granted.contains(HealthSync.P_HR)) {
            for (HeartRateRecord r : readAll(m, HeartRateRecord.class, from, to, garminOnly)) {
                found += GARMIN.equals(origin(r)) ? 1 : 0;
                for (HeartRateRecord.HeartRateSample s : r.getSamples()) {
                    long bpm = s.getBeatsPerMinute();
                    if (bpm < 25 || bpm > 240) continue;
                    Acc a = hrDay.get(day(s.getTime(), zone));
                    if (a == null) { a = new Acc(); hrDay.put(day(s.getTime(), zone), a); }
                    a.add(bpm);
                    for (int i = 0; i < ns; i++) {
                        ExerciseSessionRecord e = sessions.get(i);
                        if (!s.getTime().isBefore(e.getStartTime()) && s.getTime().isBefore(e.getEndTime())) hrOf[i].add(bpm);
                    }
                }
            }
        }
        if (granted.contains(HealthSync.P_DISTANCE)) {
            for (DistanceRecord r : readAll(m, DistanceRecord.class, from, to, garminOnly)) {
                int i = sessionOf(sessions, r);
                if (i >= 0) dist[i] += r.getDistance().getInMeters();
            }
        }
        TreeMap<String, Double> kcalDay = new TreeMap<>();
        if (granted.contains(HealthSync.P_CALORIES)) {
            for (TotalCaloriesBurnedRecord r : readAll(m, TotalCaloriesBurnedRecord.class, from, to, garminOnly)) {
                double k = r.getEnergy().getInCalories() / 1000.0;     // the framework's Energy is in small calories
                int i = sessionOf(sessions, r);
                if (i >= 0) kcal[i] += k;
                String d = day(r.getStartTime(), zone);
                kcalDay.put(d, (kcalDay.containsKey(d) ? kcalDay.get(d) : 0) + k);
            }
        }
        JSONArray ex = new JSONArray();
        for (int i = 0; i < ns; i++) {
            ExerciseSessionRecord e = sessions.get(i);
            found += GARMIN.equals(origin(e)) ? 1 : 0;
            JSONObject o = new JSONObject().put("start", time(e.getStartTime(), zone)).put("end", time(e.getEndTime(), zone))
                    .put("type", typeName(e.getExerciseType())).put("type_id", e.getExerciseType());
            CharSequence title = e.getTitle();
            if (title != null && title.length() > 0) o.put("title", title.toString());
            long secs = e.getEndTime().getEpochSecond() - e.getStartTime().getEpochSecond();
            if (dist[i] > 0) {
                o.put("distance_m", Math.round(dist[i]));
                if (secs > 0) o.put("avg_speed_mps", Math.round(dist[i] / secs * 100) / 100.0);
            }
            if (hrOf[i].n > 0) o.put("avg_hr", Math.round((double) hrOf[i].sum / hrOf[i].n)).put("max_hr", hrOf[i].max);
            if (kcal[i] > 0) o.put("kcal", Math.round(kcal[i]));
            String src = origin(e);
            if (src != null) o.put("src", src);
            ex.put(o);
        }
        out.put("exercise", ex);
        JSONArray hrd = new JSONArray();
        for (Map.Entry<String, Acc> d : hrDay.entrySet()) {
            Acc a = d.getValue();
            hrd.put(new JSONObject().put("date", d.getKey()).put("min", a.min).put("avg", Math.round((double) a.sum / a.n)).put("max", a.max));
        }
        out.put("hr_daily", hrd);
        JSONArray cal = new JSONArray();
        for (Map.Entry<String, Double> d : kcalDay.entrySet()) cal.put(new JSONObject().put("date", d.getKey()).put("kcal", Math.round(d.getValue())));
        out.put("calories", cal);

        // resting heart rate: the last value of each day
        TreeMap<String, Long> rhr = new TreeMap<>();
        if (granted.contains(HealthSync.P_RESTING)) {
            for (RestingHeartRateRecord r : readAll(m, RestingHeartRateRecord.class, from, to, garminOnly)) {
                found += GARMIN.equals(origin(r)) ? 1 : 0;
                rhr.put(day(r.getTime(), zone), r.getBeatsPerMinute());
            }
        }
        JSONArray rh = new JSONArray();
        for (Map.Entry<String, Long> d : rhr.entrySet()) rh.put(new JSONObject().put("date", d.getKey()).put("bpm", d.getValue()));
        out.put("resting_hr", rh);

        // HRV (RMSSD): the night's average per day, when the watch wrote any
        TreeMap<String, double[]> hrv = new TreeMap<>();
        if (granted.contains(HealthSync.P_HRV)) {
            for (HeartRateVariabilityRmssdRecord r : readAll(m, HeartRateVariabilityRmssdRecord.class, from, to, garminOnly)) {
                double[] a = hrv.get(day(r.getTime(), zone));
                if (a == null) { a = new double[2]; hrv.put(day(r.getTime(), zone), a); }
                a[0] += r.getHeartRateVariabilityMillis();
                a[1] += 1;
            }
        }
        JSONArray hv = new JSONArray();
        for (Map.Entry<String, double[]> d : hrv.entrySet()) {
            hv.put(new JSONObject().put("date", d.getKey()).put("rmssd_ms", Math.round(d.getValue()[0] / d.getValue()[1] * 10) / 10.0));
        }
        out.put("hrv", hv);

        // steps per day: per source, then the largest source per day (two apps counting the same walk never add up)
        Map<String, TreeMap<String, Long>> stepsBySrc = new HashMap<>();
        if (granted.contains(HealthSync.P_STEPS)) {
            for (StepsRecord r : readAll(m, StepsRecord.class, from, to, garminOnly)) {
                found += GARMIN.equals(origin(r)) ? 1 : 0;
                String src = String.valueOf(origin(r));
                TreeMap<String, Long> per = stepsBySrc.get(src);
                if (per == null) { per = new TreeMap<>(); stepsBySrc.put(src, per); }
                String d = day(r.getStartTime(), zone);
                per.put(d, (per.containsKey(d) ? per.get(d) : 0L) + r.getCount());
            }
        }
        TreeMap<String, Long> steps = new TreeMap<>();
        for (TreeMap<String, Long> per : stepsBySrc.values()) {
            for (Map.Entry<String, Long> d : per.entrySet()) {
                if (!steps.containsKey(d.getKey()) || steps.get(d.getKey()) < d.getValue()) steps.put(d.getKey(), d.getValue());
            }
        }
        JSONArray st = new JSONArray();
        for (Map.Entry<String, Long> d : steps.entrySet()) st.put(new JSONObject().put("date", d.getKey()).put("count", d.getValue()));
        out.put("steps", st);

        JSONArray w = new JSONArray();
        if (granted.contains(HealthSync.P_WEIGHT)) {
            for (WeightRecord r : readAll(m, WeightRecord.class, from, to, garminOnly)) {
                w.put(new JSONObject().put("time", time(r.getTime(), zone)).put("kg", Math.round(r.getWeight().getInGrams() / 100.0) / 10.0));
            }
        }
        out.put("weight", w);
        JSONArray v = new JSONArray();
        if (granted.contains(HealthSync.P_VO2)) {
            for (Vo2MaxRecord r : readAll(m, Vo2MaxRecord.class, from, to, garminOnly)) {
                v.put(new JSONObject().put("time", time(r.getTime(), zone)).put("value", Math.round(r.getVo2MillilitersPerMinuteKilogram() * 10) / 10.0));
            }
        }
        out.put("vo2max", v);
        out.put("_found", found);
        return out;
    }

    /** The exercise session an interval record falls inside (by its middle), or -1. */
    private static int sessionOf(List<ExerciseSessionRecord> sessions, IntervalRecord r) {
        Instant mid = r.getStartTime().plusMillis((r.getEndTime().toEpochMilli() - r.getStartTime().toEpochMilli()) / 2);
        for (int i = 0; i < sessions.size(); i++) {
            ExerciseSessionRecord e = sessions.get(i);
            if (!mid.isBefore(e.getStartTime()) && mid.isBefore(e.getEndTime())) return i;
        }
        return -1;
    }
}
