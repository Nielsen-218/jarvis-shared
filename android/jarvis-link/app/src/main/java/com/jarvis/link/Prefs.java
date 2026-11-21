package com.jarvis.link;

import android.content.Context;
import android.content.SharedPreferences;

/** Private app settings: the Jarvis URL, the device token and a few status lines for the screen. Never logged. */
final class Prefs {
    static final String DEFAULT_URL = "https://jarvis-laptop.your-tailnet.ts.net";

    private final SharedPreferences p;

    Prefs(Context context) {
        p = context.getApplicationContext().getSharedPreferences("link", Context.MODE_PRIVATE);
    }

    String url() { return p.getString("url", DEFAULT_URL); }
    String token() { return p.getString("token", ""); }
    boolean paired() { return !token().isEmpty(); }
    boolean stopped() { return p.getBoolean("stopped", false); }

    void setUrl(String url) { p.edit().putString("url", url).apply(); }

    void pair(String url, String token) {
        p.edit().putString("url", url).putString("token", token).putBoolean("stopped", false)
                .remove("removed").apply();
    }

    void unpair(String why) {
        SharedPreferences.Editor e = p.edit().remove("token");
        if (why == null) e.remove("removed"); else e.putString("removed", why);
        e.apply();
    }

    void setStopped(boolean stopped) { p.edit().putBoolean("stopped", stopped).apply(); }
    String removed() { return p.getString("removed", ""); }

    void note(String key, String value) { p.edit().putString(key, value).putLong(key + "_at", System.currentTimeMillis()).apply(); }
    String text(String key) { return p.getString(key, ""); }
    long at(String key) { return p.getLong(key + "_at", 0); }

    void contact() { p.edit().putLong("contact_at", System.currentTimeMillis()).apply(); }
    long contactAt() { return p.getLong("contact_at", 0); }

    // ---- Health Connect sync state (HealthSync). Counts and times only: never a health value. ----
    long num(String key) { return p.getLong(key, 0); }
    void setNum(String key, long value) { p.edit().putLong(key, value).apply(); }
    boolean flag(String key) { return p.getBoolean(key, false); }
    void setFlag(String key, boolean value) { p.edit().putBoolean(key, value).apply(); }
    void setText(String key, String value) { p.edit().putString(key, value).apply(); }
}
