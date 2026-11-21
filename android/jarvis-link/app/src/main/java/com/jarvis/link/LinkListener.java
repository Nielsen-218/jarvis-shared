package com.jarvis.link;

import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

/**
 * Exists only because Notification access is what Android requires before an app may see and control other apps'
 * media sessions. It never reads, stores or sends a notification: every callback about notifications is ignored.
 * While the system keeps it bound (access granted), it hosts the link loop; when access is revoked the system unbinds
 * it and the loop stops.
 */
public class LinkListener extends NotificationListenerService {
    @Override
    public void onListenerConnected() {
        LinkEngine.start(this);
    }

    @Override
    public void onListenerDisconnected() {
        LinkEngine.stop();
    }

    @Override
    public void onDestroy() {
        LinkEngine.stop();
        super.onDestroy();
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        // deliberately ignored: notification contents are never read
    }

    @Override
    public void onNotificationRemoved(StatusBarNotification sbn) {
        // deliberately ignored
    }
}
