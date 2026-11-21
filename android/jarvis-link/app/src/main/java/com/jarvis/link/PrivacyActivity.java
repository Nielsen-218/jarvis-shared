package com.jarvis.link;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** The plain-language privacy screen Android shows from Health Connect ("why does this app want health data?"): the
 *  manifest points VIEW_PERMISSION_USAGE / HEALTH_PERMISSIONS (Android 14+) and the older rationale action here. */
public class PrivacyActivity extends Activity {
    static final String TEXT =
            "What Jarvis Link reads\n"
            + "Only what you allow in Health Connect: sleep (start, end and minutes in each stage), heart rate (summarised "
            + "per day and per workout, never second by second), resting heart rate, heart rate variability, workouts "
            + "(type, time, distance, calories), steps per day, weight and VO2 max. By default only data written by the "
            + "Garmin Connect app is read.\n\n"
            + "Where it goes\n"
            + "Only to your own Jarvis server, the address on the main screen, over HTTPS with this phone's pairing "
            + "token. No other company, no analytics, no ads. Jarvis uses it to coach your training: sleep and resting "
            + "heart rate can make a hard day easier, and a recorded run can be logged with one tap.\n\n"
            + "What it never does\n"
            + "It never writes anything to Health Connect, never changes your Garmin data and never reads notifications.\n\n"
            + "How to stop\n"
            + "In Jarvis Link tap \"Stop sharing health data\", or open Settings > Health Connect > App permissions > "
            + "Jarvis Link and turn the permissions off. Unpairing the app stops all sending too. Data Jarvis already "
            + "received stays in your Jarvis data folder until you delete it there.";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        int pad = Math.round(16 * getResources().getDisplayMetrics().density);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(pad, pad, pad, pad);
        TextView title = new TextView(this);
        title.setText("Jarvis Link and your health data");
        title.setTextSize(24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(Color.rgb(52, 212, 240));
        col.addView(title);
        TextView body = new TextView(this);
        body.setText(TEXT);
        body.setTextSize(16);
        body.setTextColor(Color.rgb(230, 238, 245));
        body.setPadding(0, pad, 0, pad);
        col.addView(body);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.rgb(5, 9, 15));
        scroll.addView(col);
        setContentView(scroll);
    }
}
