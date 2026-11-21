package com.jarvis.link;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/** HTTP to his Jarvis only. Always called from a worker thread (never the main thread). The token goes in the
 *  Authorization header, never in a URL, and is never logged. */
final class Net {
    /** A non-2xx answer: the status code and the server's sentence. */
    static final class HttpError extends IOException {
        final int status;
        HttpError(int status, String message) { super(message); this.status = status; }
    }

    private Net() {}

    /** -> a cleaned base URL (no trailing slash), or null when it is not allowed: https anywhere, http only for the
     *  local test hosts. */
    static String cleanBase(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        try {
            URL u = new URL(s);
            String scheme = u.getProtocol().toLowerCase(Locale.ROOT);
            String host = u.getHost().toLowerCase(Locale.ROOT);
            if (host.isEmpty() || u.getQuery() != null || u.getUserInfo() != null) return null;
            if (scheme.equals("https")) return s;
            if (scheme.equals("http") && (host.equals("127.0.0.1") || host.equals("localhost") || host.equals("10.0.2.2"))) return s;
        } catch (IOException e) {
            return null;
        }
        return null;
    }

    static JSONObject call(String base, String method, String path, String token, JSONObject body, int readTimeoutMs)
            throws IOException {
        String clean = cleanBase(base);
        if (clean == null) throw new HttpError(0, "The Jarvis address must start with https://");
        HttpURLConnection c = (HttpURLConnection) new URL(clean + path).openConnection();
        try {
            c.setRequestMethod(method);
            c.setConnectTimeout(15000);
            c.setReadTimeout(readTimeoutMs);
            c.setUseCaches(false);
            c.setInstanceFollowRedirects(false);       // a redirect could carry the token elsewhere
            c.setRequestProperty("Accept", "application/json");
            if (token != null && !token.isEmpty()) c.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                byte[] out = body.toString().getBytes(StandardCharsets.UTF_8);
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(out.length);
                try (OutputStream os = c.getOutputStream()) { os.write(out); }
            }
            int status = c.getResponseCode();
            String text = read(status >= 400 ? c.getErrorStream() : c.getInputStream());
            if (status < 200 || status >= 300) {
                String detail = "HTTP " + status;
                try { detail = new JSONObject(text).optString("detail", detail); } catch (JSONException ignored) { }
                throw new HttpError(status, detail);
            }
            try {
                return text.isEmpty() ? new JSONObject() : new JSONObject(text);
            } catch (JSONException e) {
                throw new IOException("Jarvis answered something that is not JSON.");
            }
        } finally {
            c.disconnect();
        }
    }

    private static String read(InputStream in) throws IOException {
        if (in == null) return "";
        try (InputStream is = in) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n, total = 0;
            while ((n = is.read(chunk)) > 0) {
                total += n;
                if (total > 256 * 1024) throw new IOException("Jarvis answered too much.");
                buf.write(chunk, 0, n);
            }
            return buf.toString("UTF-8");
        }
    }
}
