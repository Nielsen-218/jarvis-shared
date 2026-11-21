package com.jarvis.link;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.service.notification.NotificationListenerService;
import android.text.InputType;
import android.text.format.DateUtils;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** One plain screen: address, pairing, status, permission buttons, local tests. Network and media calls run on a
 *  worker thread; the screen only shows results. */
public class MainActivity extends Activity {
    private static final int BG = Color.rgb(5, 9, 15), FG = Color.rgb(230, 238, 245), ACCENT = Color.rgb(52, 212, 240);

    private static final int REQ_HEALTH = 2;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final ExecutorService healthWorker = Executors.newSingleThreadExecutor();   // Health Connect reads + posts
    private final Handler main = new Handler(Looper.getMainLooper());
    private Prefs prefs;
    private Metro metro;
    private EditText url, code;
    private TextView status, message, healthStatus;
    private volatile String sessionLine = "Metrolist session: checking...";
    private final Runnable refresher = new Runnable() {
        @Override public void run() {
            refresh();
            main.postDelayed(this, 2000);
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = new Prefs(this);
        metro = new Metro(this);

        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(16);
        col.setPadding(pad, pad, pad, pad);

        TextView title = text("Jarvis Link", 28);
        title.setTextColor(ACCENT);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        col.addView(title);
        col.addView(text("Lets Jarvis play, pause and skip Metrolist for you. It never reads your notifications and talks only to your Jarvis.", 15));

        col.addView(label("Jarvis address"));
        url = field(prefs.url(), InputType.TYPE_TEXT_VARIATION_URI);
        col.addView(url);
        col.addView(button("CONNECT TO JARVIS (no typing)", v -> openPairPage()));
        col.addView(text("Opens your Jarvis page. Tap \"Connect this phone\" there and it pairs by itself.", 14));
        col.addView(label("Or type a pairing code from the Jarvis page"));
        code = field("", InputType.TYPE_CLASS_NUMBER);
        col.addView(code);
        col.addView(button("PAIR", v -> pair()));

        status = text("", 17);
        status.setPadding(0, dp(12), 0, dp(12));
        col.addView(status);
        message = text("", 16);
        message.setTextColor(ACCENT);
        col.addView(message);

        col.addView(button("Grant notification access", v -> startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))));
        col.addView(button("Allow background running", v -> askBattery()));
        LinearLayout tests = new LinearLayout(this);
        tests.addView(button("Test: pause", v -> test("pause")), weight());
        tests.addView(button("play", v -> test("play")), weight());
        tests.addView(button("next", v -> test("next")), weight());
        col.addView(tests);
        col.addView(button("Start link again", v -> {
            prefs.setStopped(false);
            LinkEngine.nudge();
            say("Link started.");
        }));
        col.addView(button("Unpair", v -> {
            prefs.unpair(null);
            LinkEngine.nudge();
            say("Unpaired. The token is deleted from this phone.");
        }));

        // ---- Health data (Garmin) through Android Health Connect: read-only, to his own Jarvis only ----
        TextView healthTitle = text("Health data (Garmin)", 22);
        healthTitle.setTextColor(ACCENT);
        healthTitle.setTypeface(Typeface.DEFAULT_BOLD);
        healthTitle.setPadding(0, dp(24), 0, dp(4));
        col.addView(healthTitle);
        col.addView(text("Garmin Connect writes your watch data to Health Connect. Jarvis Link reads sleep, heart rate, "
                + "workouts, steps, weight and VO2 max from it and sends them only to your Jarvis. It never writes anything.", 15));
        healthStatus = text("", 16);
        healthStatus.setPadding(0, dp(8), 0, dp(8));
        col.addView(healthStatus);
        col.addView(button("Allow Jarvis to read health data", v -> askHealth()));
        col.addView(button("Sync now", v -> syncHealth("button")));
        Switch other = new Switch(this);
        other.setText("Include other sources (not only Garmin Connect)");
        other.setTextColor(FG);
        other.setTextSize(15);
        other.setChecked(prefs.flag("health_other"));
        other.setOnCheckedChangeListener((b, on) -> {
            prefs.setFlag("health_other", on);
            prefs.setNum("health_cursor", 0);                   // re-read the last 30 days with the new choice
        });
        col.addView(other);
        col.addView(button("What is read, and where it goes", v -> startActivity(new Intent(this, PrivacyActivity.class))));
        col.addView(button("Stop sharing health data", v -> stopHealth()));

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        scroll.addView(col);
        setContentView(scroll);

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
        if (state == null) handleLink(getIntent());            // opened by the page's "Connect this phone" link
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleLink(intent);
    }

    /** jarvislink://pair?code=123456&base=https://his-jarvis : the Jarvis page hands over a one-use pairing code, so
     *  nothing is typed. The code only works on the Jarvis that made it. An address other than the one on this screen
     *  is never used without asking: a link must not be able to point the app at somebody else's server silently. */
    private void handleLink(Intent intent) {
        Uri u = intent == null ? null : intent.getData();
        if (u == null || !"jarvislink".equals(u.getScheme()) || !"pair".equals(u.getHost())) return;
        intent.setData(null);                                   // handled once: not again after a rotation
        String c, rawBase;
        try {
            c = u.getQueryParameter("code");
            rawBase = u.getQueryParameter("base");
        } catch (RuntimeException e) {
            say("That link is not a Jarvis pairing link.");
            return;
        }
        String here = Net.cleanBase(url.getText().toString());
        String base = rawBase == null || rawBase.isEmpty() ? here : Net.cleanBase(rawBase);
        if (base == null) { say("That link has no usable Jarvis address (it must start with https://)."); return; }
        if (c == null || !c.matches("\\d{6}")) { say("That link has no pairing code. Tap Connect this phone on the Jarvis page again."); return; }
        String code6 = c;
        // A code works once. Android hands the same link over again when the app is reopened from Recents, and a second
        // tap on the page sends it twice: never send a used code again (it would show "not valid" over a good pairing
        // and count as a wrong guess). Links are also limited to one try per 10 seconds, so a hostile page cannot burn
        // the server's wrong-code allowance.
        long now = System.currentTimeMillis();
        if (code6.equals(prefs.text("link_code"))) { if (!prefs.paired()) say("That link was already used. Tap Connect this phone on the Jarvis page again."); return; }
        if (now - prefs.num("link_try_at") < 10000) { say("One moment: wait 10 seconds, then tap Connect this phone again."); return; }
        prefs.setText("link_code", code6);
        prefs.setNum("link_try_at", now);
        if (base.equalsIgnoreCase(here)) { pair(base, code6); return; }
        String host;
        try { host = new java.net.URL(base).getHost(); } catch (java.io.IOException e) { say("That link has no usable Jarvis address."); return; }
        new AlertDialog.Builder(this)
                .setTitle("Connect to a different Jarvis?")
                .setMessage("This link wants Jarvis Link to talk to\n" + host + "\ninstead of the address on this screen. "
                        + "Say yes only if that is your own Jarvis.")
                .setPositiveButton("Yes, connect", (d, w) -> pair(base, code6))
                .setNegativeButton("No", (d, w) -> say("Not connected. Nothing was changed."))
                .show();
    }

    /** Open his Jarvis page in the browser he is signed in with; its "Connect this phone" button comes back here. */
    private void openPairPage() {
        String base = Net.cleanBase(url.getText().toString());
        if (base == null) { say("The address must start with https://"); return; }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(base + "/captain.html?pair=1")));
            say("On the Jarvis page, tap Connect this phone.");
        } catch (RuntimeException e) {
            say("No browser could open the Jarvis page. Open it yourself and tap Connect this phone.");
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code != REQ_HEALTH) return;
        int n = HealthSync.typesGranted(this);
        if (n == 0) {
            say("No health data was allowed. Tap the button again, or allow it in Health Connect.");
        } else {
            say("Allowed " + n + " of " + HealthSync.TYPES.length + " kinds of health data. Syncing...");
            syncHealth("granted");
        }
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (accessGranted() && !LinkEngine.isRunning()) {
            NotificationListenerService.requestRebind(new ComponentName(this, LinkListener.class));
        }
        main.post(refresher);
        if (HealthSync.due(this, prefs, true, false)) syncHealth("opened");   // the app is open: send what is new
    }

    @Override
    protected void onPause() {
        main.removeCallbacks(refresher);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        worker.shutdownNow();
        healthWorker.shutdownNow();
        super.onDestroy();
    }

    // ---- actions ----------------------------------------------------------------------------------------------------
    private void pair() {
        String base = Net.cleanBase(url.getText().toString());
        String c = code.getText().toString().trim();
        if (base == null) { say("The address must start with https://"); return; }
        if (!c.matches("\\d{6}")) { say("The pairing code is 6 digits."); return; }
        pair(base, c);
    }

    private void pair(String base, String c) {
        say("Pairing...");
        worker.execute(() -> {
            String msg;
            try {
                JSONObject body = new JSONObject().put("code", c).put("device", "jarvis-link");
                JSONObject r = Net.call(base, "POST", "/api/pair", null, body, 20000);
                String token = r.optString("token", "");
                if (token.isEmpty()) {
                    msg = "Jarvis did not return a token.";
                } else {
                    prefs.pair(base, token);
                    prefs.contact();
                    LinkEngine.nudge();
                    msg = "Paired" + (r.optString("name").isEmpty() ? "" : " as " + r.optString("name")) + ". Jarvis Link is connected.";
                }
            } catch (Net.HttpError e) {
                msg = e.getMessage();
            } catch (JSONException | java.io.IOException e) {
                msg = "Cannot reach Jarvis (" + e.getClass().getSimpleName() + ").";
            }
            String shown = msg;
            boolean ok = prefs.paired() && base.equals(prefs.url());
            main.post(() -> { code.setText(""); if (ok) url.setText(base); say(shown); });   // the address changes only after success
        });
    }

    private void askBattery() {
        PowerManager pm = getSystemService(PowerManager.class);
        if (pm != null && pm.isIgnoringBatteryOptimizations(getPackageName())) {
            say("Background running is already allowed.");
            return;
        }
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (RuntimeException e) {
            startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
        }
    }

    // ---- health data ------------------------------------------------------------------------------------------------
    private void askHealth() {
        if (!HealthSync.supported()) { say("Health data needs Android 14 or newer. Everything else in Jarvis Link works."); return; }
        if (!HealthSync.available(this)) { say("Health Connect is not available on this phone. Update Android, then try again."); return; }
        prefs.setFlag("health_off", false);
        requestPermissions(HealthSync.wanted(), REQ_HEALTH);  // Android 14+: health permissions are runtime permissions
    }

    private void syncHealth(String why) {
        if (!HealthSync.supported()) { say("Health data needs Android 14 or newer."); return; }
        if (prefs.flag("health_off") && !"button".equals(why)) return;
        if (HealthSync.running()) { say("A health sync is already running."); return; }
        say("Syncing health data...");
        healthWorker.execute(() -> {
            if ("button".equals(why)) prefs.setFlag("health_off", false);
            String msg = HealthSync.sync(this, why);
            main.post(() -> { say(msg); refresh(); });
        });
    }

    private void stopHealth() {
        prefs.setFlag("health_off", true);                      // no more syncs from this app until "Allow" or "Sync now"
        say("Health sharing stopped. To remove the permission too, turn off Jarvis Link on the next screen.");
        try {
            startActivity(new Intent("android.health.connect.action.MANAGE_HEALTH_PERMISSIONS")
                    .putExtra(Intent.EXTRA_PACKAGE_NAME, getPackageName()));
        } catch (RuntimeException e) {
            try {
                startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName())));
            } catch (RuntimeException ignored) {
                say("Health sharing stopped. Remove the permission in Settings > Health Connect > App permissions.");
            }
        }
    }

    private String healthLines() {
        StringBuilder s = new StringBuilder();
        if (!HealthSync.supported()) {
            return "Health data needs Android 14 or newer (this phone runs Android " + Build.VERSION.RELEASE + "). Everything else works.";
        }
        boolean available = HealthSync.available(this);
        s.append("Android ").append(Build.VERSION.RELEASE).append(": OK\n");
        s.append("Health Connect: ").append(available ? "available" : "NOT available (update Android or Health Connect)").append('\n');
        if (!available) return s.toString().trim();
        java.util.Set<String> g = HealthSync.granted(this);
        int n = HealthSync.typesGranted(this);
        s.append("Allowed: ").append(n).append(" of ").append(HealthSync.TYPES.length).append(" kinds");
        if (n > 0 && n < HealthSync.TYPES.length) {
            StringBuilder missing = new StringBuilder();
            for (String p : HealthSync.TYPES) if (!g.contains(p)) missing.append(missing.length() > 0 ? ", " : "").append(shortName(p));
            s.append(" (not: ").append(missing).append(')');
        }
        s.append('\n');
        if (Build.VERSION.SDK_INT >= 35) {
            s.append("Background sync: ").append(g.contains(HealthSync.P_BACKGROUND)
                    ? "allowed (every 2 hours, and when Jarvis asks)" : "not allowed (syncs when you open the app)").append('\n');
        } else {
            s.append("On Android 14, open Jarvis Link to sync (no background reading).\n");
        }
        if (prefs.flag("health_off")) s.append("Sharing: stopped (tap Sync now or Allow to start again)\n");
        s.append("Garmin Connect found as a source: ").append(prefs.flag("garmin_seen") ? "yes"
                : "not yet (in the Garmin Connect app, turn on Health Connect)").append('\n');
        long ok = prefs.num("health_ok_at");
        s.append("Last sync: ").append(ago(ok));
        if (ok > 0 && !prefs.text("health_counts").isEmpty()) s.append(" (").append(prefs.text("health_counts")).append(')');
        if (!prefs.text("health_error").isEmpty() && prefs.num("health_try_at") > ok) s.append("\nProblem: ").append(prefs.text("health_error"));
        return s.toString();
    }

    private static String shortName(String permission) {
        return permission.replace("android.permission.health.READ_", "").replace('_', ' ').toLowerCase(java.util.Locale.ROOT);
    }

    private void test(String name) {
        if (!accessGranted()) { say("Grant notification access first."); return; }
        worker.execute(() -> {
            JSONObject r = metro.run(name, new JSONObject());
            String line = "Test " + name + ": " + (r.optBoolean("ok") ? "OK, " : "failed, ") + r.optString("detail");
            prefs.note("last_cmd", "test " + name);
            prefs.note("last_result", r.optString("detail"));
            main.post(() -> say(line));
        });
    }

    // ---- status -----------------------------------------------------------------------------------------------------
    private boolean accessGranted() {
        String flat = Settings.Secure.getString(getContentResolver(), "enabled_notification_listeners");
        ComponentName me = new ComponentName(this, LinkListener.class);
        return flat != null && (flat.contains(me.flattenToString()) || flat.contains(me.flattenToShortString()));
    }

    private void refresh() {
        boolean access = accessGranted();
        if (access) {
            worker.execute(() -> sessionLine = "Metrolist session: " + (metro.controller() != null
                    ? LinkEngine.describe(metro.state()) : "not found (open Metrolist and play something)"));
        } else {
            sessionLine = "Metrolist session: needs notification access";
        }
        PowerManager pm = getSystemService(PowerManager.class);
        boolean battery = pm != null && pm.isIgnoringBatteryOptimizations(getPackageName());
        StringBuilder s = new StringBuilder();
        String removed = prefs.removed();
        s.append(prefs.paired() ? "Paired: yes" : (removed.isEmpty() ? "Paired: no" : "Paired: no - " + removed)).append('\n');
        if (prefs.stopped()) s.append("Link: stopped (tap Start link again)\n");
        s.append("Notification access: ").append(access ? "granted" : "NOT granted").append('\n');
        s.append("Background running: ").append(battery ? "allowed" : "not allowed").append('\n');
        s.append(sessionLine).append('\n');
        s.append("Last contact with Jarvis: ").append(ago(prefs.contactAt())).append('\n');
        if (!prefs.text("error").isEmpty() && prefs.at("error") > prefs.contactAt()) {
            s.append("Problem: ").append(prefs.text("error")).append('\n');
        }
        s.append("Last command: ").append(prefs.text("last_cmd").isEmpty() ? "none" : prefs.text("last_cmd") + " (" + ago(prefs.at("last_cmd")) + ")").append('\n');
        s.append("Result: ").append(prefs.text("last_result").isEmpty() ? "-" : prefs.text("last_result")).append('\n');
        s.append("Reported to Jarvis: ").append(prefs.text("playing").isEmpty() ? "-" : prefs.text("playing"));
        status.setText(s.toString());
        if (healthStatus != null) healthStatus.setText(healthLines());
    }

    private static String ago(long ms) {
        if (ms <= 0) return "never";
        return DateUtils.getRelativeTimeSpanString(ms, System.currentTimeMillis(), DateUtils.SECOND_IN_MILLIS).toString();
    }

    // ---- views ------------------------------------------------------------------------------------------------------
    private void say(String s) { message.setText(s); }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    private TextView text(String s, int sp) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(FG);
        t.setPadding(0, dp(4), 0, dp(4));
        return t;
    }

    private TextView label(String s) {
        TextView t = text(s, 14);
        t.setPadding(0, dp(14), 0, dp(2));
        return t;
    }

    private EditText field(String value, int type) {
        EditText e = new EditText(this);
        e.setText(value);
        e.setTextSize(20);
        e.setTextColor(FG);
        e.setHintTextColor(Color.GRAY);
        e.setInputType(type == InputType.TYPE_CLASS_NUMBER ? type : InputType.TYPE_CLASS_TEXT | type);
        e.setSingleLine(true);
        return e;
    }

    private Button button(String s, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(s);
        b.setTextSize(17);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        return b;
    }

    private static LinearLayout.LayoutParams weight() {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
    }
}
