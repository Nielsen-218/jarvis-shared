package com.jarvis.link;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.Rating;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.net.Uri;
import android.os.SystemClock;
import android.util.Base64;
import android.view.KeyEvent;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;

/**
 * Metrolist (com.metrolist.music) through Android's media-session API. Only the controller whose package is
 * com.metrolist.music is ever touched.
 *
 * What Metrolist accepts, read in MetrolistGroup/Metrolist at commit 0fe6709c9c2ce70ff86ff074cfaae6a8dd064dd1
 * (2026-11-20), androidApp/src/main/kotlin/com/metrolist/android/:
 *  - constants/MediaSessionConstants.kt:  ACTION_TOGGLE_START_RADIO = "TOGGLE_START_RADIO",
 *    ACTION_TOGGLE_LIKE = "TOGGLE_LIKE" (also ADD_TO_QUEUE, TOGGLE_REPEAT, TOGGLE_SHUFFLE, ADD_TO_TARGET_PLAYLIST;
 *    all SessionCommand(action, Bundle.EMPTY): no extras).
 *  - playback/MusicService.kt AutomotiveSessionCallback: a trusted controller (controller.isTrusted: Android grants
 *    that to an app holding Notification access) gets DEFAULT_SESSION_AND_LIBRARY_COMMANDS + TOGGLE_LIKE / REPEAT /
 *    SHUFFLE / START_RADIO / ADD_TO_QUEUE / ADD_TO_TARGET_PLAYLIST. onCustomCommand(TOGGLE_LIKE) -> toggleFavorite();
 *    TOGGLE_START_RADIO -> playMix(current song). onSetRating accepts ThumbRating or HeartRating and SETS (not
 *    toggles) the like (MediaSessionPlayer.setMediaSessionRating). The metadata's user rating is a ThumbRating that is
 *    thumbs-up when the song is liked (MediaSessionPlayer: setUserRating(ThumbRating(isLiked))).
 *  - onSetMediaItems: a search query (playFromSearch) resolves a voice search and plays it; a media id goes through
 *    AutomotiveMediaLibrary.resolveSong: "auto:song:" + base64url(videoId) (no padding) or a bare id, found only when the
 *    song is in Metrolist's database/cache; "auto:browse-song:" + b64(parent) + ":" + index + ":" + b64(videoId) with
 *    parent "auto:search:<query>" makes Metrolist run that search and play the result whose id is videoId
 *    (resolveSongQueue). The session's current media id is "auto:queue:<entryKey>:" + b64(videoId) or the bare id.
 */
final class Metro {
    static final String PACKAGE = "com.metrolist.music";
    static final String ACTION_TOGGLE_LIKE = "TOGGLE_LIKE";
    static final String ACTION_TOGGLE_START_RADIO = "TOGGLE_START_RADIO";

    private final Context context;

    Metro(Context context) { this.context = context.getApplicationContext(); }

    /** The Metrolist controller, or null (no session, or notification access was revoked). */
    MediaController controller() {
        try {
            MediaSessionManager msm = context.getSystemService(MediaSessionManager.class);
            if (msm == null) return null;
            List<MediaController> list = msm.getActiveSessions(new ComponentName(context, LinkListener.class));
            if (list == null) return null;
            for (MediaController c : list) {
                if (c != null && PACKAGE.equals(c.getPackageName())) return c;
            }
        } catch (SecurityException e) {
            return null;                                  // Notification access is off
        }
        return null;
    }

    // ---- state ------------------------------------------------------------------------------------------------------
    static boolean playing(MediaController c) {
        PlaybackState s = c == null ? null : c.getPlaybackState();
        return s != null && (s.getState() == PlaybackState.STATE_PLAYING || s.getState() == PlaybackState.STATE_BUFFERING);
    }

    /** null = not readable, else whether the current song is liked (Metrolist's thumb rating). */
    static Boolean liked(MediaController c) {
        MediaMetadata md = c == null ? null : c.getMetadata();
        Rating r = md == null ? null : md.getRating(MediaMetadata.METADATA_KEY_USER_RATING);
        if (r == null) return null;
        if (r.getRatingStyle() == Rating.RATING_THUMB_UP_DOWN) return r.isRated() && r.isThumbUp();
        if (r.getRatingStyle() == Rating.RATING_HEART) return r.isRated() && r.hasHeart();
        return null;
    }

    JSONObject state() {
        return state(controller());
    }

    static JSONObject state(MediaController c) {
        JSONObject o = new JSONObject();
        try {
            o.put("package", PACKAGE);
            o.put("ts", System.currentTimeMillis() / 1000.0);
            if (c == null) {
                o.put("playing", false);
                o.put("session", false);
                return o;
            }
            o.put("session", true);
            o.put("playing", playing(c));
            MediaMetadata md = c.getMetadata();
            if (md != null) {
                o.put("title", cut(md.getString(MediaMetadata.METADATA_KEY_TITLE)));
                o.put("artist", cut(md.getString(MediaMetadata.METADATA_KEY_ARTIST)));
                o.put("album", cut(md.getString(MediaMetadata.METADATA_KEY_ALBUM)));
                o.put("media_id", cut(md.getString(MediaMetadata.METADATA_KEY_MEDIA_ID)));
                long d = md.getLong(MediaMetadata.METADATA_KEY_DURATION);
                if (d > 0) o.put("duration_ms", d);
            }
            PlaybackState s = c.getPlaybackState();
            if (s != null) {
                long pos = s.getPosition();
                if (s.getState() == PlaybackState.STATE_PLAYING && s.getLastPositionUpdateTime() > 0) {
                    pos += (long) ((SystemClock.elapsedRealtime() - s.getLastPositionUpdateTime()) * s.getPlaybackSpeed());
                }
                if (pos >= 0) o.put("position_ms", pos);
            }
            Boolean liked = liked(c);
            if (liked != null) o.put("liked", liked.booleanValue());
        } catch (JSONException | RuntimeException ignored) {
            // a controller that died mid-read: report what we have
        }
        return o;
    }

    private static Object cut(String s) {
        if (s == null) return JSONObject.NULL;
        return s.length() > 300 ? s.substring(0, 300) : s;
    }

    // ---- commands ---------------------------------------------------------------------------------------------------
    /** One command -> {"ok", "detail"}; never throws. */
    JSONObject run(String name, JSONObject args) {
        try {
            switch (name) {
                case "state": return result(true, controller() == null ? "Metrolist has no active session." : "Reported.");
                case "play": return play();
                case "pause": return transport(name);
                case "toggle": return toggle();
                case "next": return transport(name);
                case "previous": return transport(name);
                case "seek": return seek(args.optLong("position_ms", -1));
                case "like": return like();
                case "radio": return radio();
                case "play_id": return playId(args.optString("video_id", ""), args.optString("title", ""), args.optString("artist", ""));
                case "play_search": return playSearch(args.optString("query", ""));
                default: return result(false, "Unknown command; refused.");
            }
        } catch (RuntimeException e) {
            // e.g. DeadObjectException wrapped: Metrolist's session went away mid-command
            return result(false, "Metrolist stopped answering (" + e.getClass().getSimpleName() + ").");
        }
    }

    static JSONObject result(boolean ok, String detail) {
        JSONObject o = new JSONObject();
        try { o.put("ok", ok); o.put("detail", detail); } catch (JSONException ignored) { }
        return o;
    }

    private JSONObject transport(String name) {
        MediaController c = controller();
        if (c == null) return result(false, "Metrolist has no active session.");
        MediaController.TransportControls t = c.getTransportControls();
        if (name.equals("pause")) t.pause();
        else if (name.equals("next")) t.skipToNext();
        else t.skipToPrevious();
        return result(true, "Sent " + name + ".");
    }

    /** Media session first; when Metrolist has none, one PLAY media key (wakes the last player) and an honest report. */
    private MediaController wake() {
        MediaController c = controller();
        if (c != null) return c;
        AudioManager am = context.getSystemService(AudioManager.class);
        if (am == null) return null;
        long now = SystemClock.uptimeMillis();
        am.dispatchMediaKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_MEDIA_PLAY, 0));
        am.dispatchMediaKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_UP, KeyEvent.KEYCODE_MEDIA_PLAY, 0));
        for (int i = 0; i < 16 && c == null; i++) {
            SystemClock.sleep(250);
            c = controller();
        }
        return c;
    }

    private JSONObject play() {
        MediaController c = controller();
        if (c == null) {
            c = wake();
            if (c == null) return result(false, "Metrolist has no session and nothing answered the play key. Open Metrolist once.");
            if (playing(c)) return result(true, "Woke Metrolist with the play key.");
        }
        c.getTransportControls().play();
        return result(true, "Sent play.");
    }

    private JSONObject toggle() {
        MediaController c = controller();
        if (c == null) return play();
        boolean was = playing(c);
        if (was) c.getTransportControls().pause(); else c.getTransportControls().play();
        return result(true, was ? "Sent pause." : "Sent play.");
    }

    private JSONObject seek(long ms) {
        MediaController c = controller();
        if (c == null) return result(false, "Metrolist has no active session.");
        if (ms < 0) return result(false, "Seek needs a position.");
        c.getTransportControls().seekTo(ms);
        return result(true, "Seeked to " + (ms / 1000) + " s.");
    }

    private JSONObject like() {
        MediaController c = controller();
        if (c == null) return result(false, "Metrolist has no active session.");
        Boolean before = liked(c);
        if (Boolean.TRUE.equals(before)) return result(true, "Already liked (nothing sent).");
        String used;
        if (Boolean.FALSE.equals(before)) {
            c.getTransportControls().sendCustomAction(ACTION_TOGGLE_LIKE, null);   // not liked -> the toggle likes it
            used = "TOGGLE_LIKE";
        } else {
            c.getTransportControls().setRating(Rating.newHeartRating(true));       // unreadable: setRating sets, never toggles
            used = "setRating(heart)";
        }
        for (int i = 0; i < 8; i++) {
            SystemClock.sleep(250);
            MediaController now = controller();
            if (now == null) break;
            if (Boolean.TRUE.equals(liked(now))) return result(true, "Liked via " + used + ".");
        }
        return result(before == null, "Sent " + used + (before != null ? "; the like did not show within 2 s." : "; Metrolist does not report likes, so unconfirmed."));
    }

    private JSONObject radio() {
        MediaController c = controller();
        if (c == null) return result(false, "Metrolist has no active session.");
        c.getTransportControls().sendCustomAction(ACTION_TOGGLE_START_RADIO, null);
        return result(true, "Sent TOGGLE_START_RADIO.");
    }

    static String b64(String s) {
        return Base64.encodeToString(s.getBytes(StandardCharsets.UTF_8), Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
    }

    private static String norm(String s) {
        return s == null ? "" : s.toLowerCase(Locale.ROOT).replaceAll("[^\\p{L}\\p{N}]+", " ").trim();
    }

    /** Is the session now on this song? media id (bare or base64url in its last ':' segment), else the title. */
    static boolean onSong(MediaController c, String videoId, String title) {
        if (c == null || !playing(c)) return false;
        MediaMetadata md = c.getMetadata();
        if (md == null) return false;
        String mid = md.getString(MediaMetadata.METADATA_KEY_MEDIA_ID);
        if (mid != null) {
            if (mid.equals(videoId)) return true;
            String last = mid.substring(mid.lastIndexOf(':') + 1);
            if (last.equals(b64(videoId))) return true;
        }
        String want = norm(title), got = norm(md.getString(MediaMetadata.METADATA_KEY_TITLE));
        return !want.isEmpty() && !got.isEmpty() && (got.equals(want) || got.contains(want) || want.contains(got));
    }

    private boolean waitFor(String videoId, String title, long ms) {
        long end = SystemClock.elapsedRealtime() + ms;
        while (SystemClock.elapsedRealtime() < end) {
            SystemClock.sleep(250);
            if (onSong(controller(), videoId, title)) return true;
        }
        return false;
    }

    private JSONObject playId(String videoId, String title, String artist) {
        if (!videoId.matches("[A-Za-z0-9_-]{11}")) return result(false, "Not a video id; refused.");
        MediaController c = wake();
        if (c == null) {
            openNotification(videoId, title);
            return result(false, "Metrolist has no session and nothing answered the play key. Posted an Open-in-Metrolist notification.");
        }
        String query = (title + " " + artist).trim();
        // 1. the song id itself (works when Metrolist already knows the song)
        c.getTransportControls().playFromMediaId("auto:song:" + b64(videoId), null);
        if (waitFor(videoId, title, 3000)) return result(true, "Playing (media id).");
        // 2. Metrolist searches the title and plays the result with this id
        if (!query.isEmpty()) {
            MediaController c2 = controller();
            if (c2 != null) {
                String browse = "auto:browse-song:" + b64("auto:search:" + query) + ":0:" + b64(videoId);
                c2.getTransportControls().playFromMediaId(browse, null);
                if (waitFor(videoId, title, 6000)) return result(true, "Playing (search for this id).");
            }
            // 3. plain voice search: plays Metrolist's best match, maybe another version
            MediaController c3 = controller();
            if (c3 != null) {
                c3.getTransportControls().playFromSearch(query, null);
                if (waitFor(videoId, title, 6000)) return result(true, "Playing (search match by title).");
                MediaController after = controller();
                if (playing(after)) {
                    MediaMetadata md = after.getMetadata();
                    String got = md == null ? "" : md.getString(MediaMetadata.METADATA_KEY_TITLE);
                    return result(false, "Search started a different song: " + (got == null ? "?" : got) + ".");
                }
            }
        }
        openNotification(videoId, title);
        return result(false, "Metrolist did not start the song. Posted an Open-in-Metrolist notification.");
    }

    private JSONObject playSearch(String query) {
        if (query.trim().isEmpty()) return result(false, "Nothing to search for.");
        MediaController c = wake();
        if (c == null) return result(false, "Metrolist has no session and nothing answered the play key.");
        MediaMetadata md0 = c.getMetadata();
        String before = md0 == null ? null : md0.getString(MediaMetadata.METADATA_KEY_MEDIA_ID);
        c.getTransportControls().playFromSearch(query, null);
        long end = SystemClock.elapsedRealtime() + 6000;
        while (SystemClock.elapsedRealtime() < end) {
            SystemClock.sleep(250);
            MediaController now = controller();
            if (now == null || !playing(now)) continue;
            MediaMetadata md = now.getMetadata();
            String mid = md == null ? null : md.getString(MediaMetadata.METADATA_KEY_MEDIA_ID);
            if (mid != null && !mid.equals(before)) {
                String t = md.getString(MediaMetadata.METADATA_KEY_TITLE);
                return result(true, "Playing " + (t == null ? "a search result" : t) + ".");
            }
        }
        return result(false, "The search did not change the song within 6 s.");
    }

    /** Last resort: a notification that opens the song in Metrolist (a tap is needed: background activity starts
     *  are not allowed). Needs the notification permission on Android 13+; silently skipped without it. */
    private void openNotification(String videoId, String title) {
        try {
            NotificationManager nm = context.getSystemService(NotificationManager.class);
            if (nm == null) return;
            nm.createNotificationChannel(new NotificationChannel("open", "Open in Metrolist", NotificationManager.IMPORTANCE_HIGH));
            Intent view = new Intent(Intent.ACTION_VIEW, Uri.parse("https://music.youtube.com/watch?v=" + videoId))
                    .setPackage(PACKAGE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            PendingIntent pi = PendingIntent.getActivity(context, 1, view, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            Notification n = new Notification.Builder(context, "open")
                    .setSmallIcon(R.drawable.ic_link)
                    .setContentTitle("Tap to play in Metrolist")
                    .setContentText(title == null || title.isEmpty() ? videoId : title)
                    .setContentIntent(pi)
                    .setAutoCancel(true)
                    .build();
            nm.notify(1, n);
        } catch (RuntimeException ignored) {
            // no permission or no Metrolist: the result already says it failed
        }
    }
}
