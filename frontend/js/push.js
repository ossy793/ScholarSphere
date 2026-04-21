/**
 * Pritis Push Notification subscription manager.
 * Call initPushNotifications() once after the user is authenticated.
 */

import { api } from './api.js';

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64  = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw     = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function getVapidPublicKey() {
  try {
    const data = await api.get('/push/vapid-public-key');
    return data?.public_key || null;
  } catch {
    return null;
  }
}

async function sendSubscriptionToServer(subscription) {
  try {
    const sub = subscription.toJSON();
    await api.post('/push/subscribe', {
      endpoint: sub.endpoint,
      keys:     sub.keys,
    });
  } catch (err) {
    console.warn('Push subscribe API error:', err);
  }
}

export async function initPushNotifications() {
  console.log('[Push] initPushNotifications called');

  if (!('Notification' in window)) { console.warn('[Push] Notification API not supported'); return; }
  if (!('serviceWorker' in navigator)) { console.warn('[Push] ServiceWorker not supported'); return; }
  if (Notification.permission === 'denied') { console.warn('[Push] Permission denied'); return; }

  // ── Step 1: Request permission ───────────────────────────────────────────────
  let permission = Notification.permission;
  console.log('[Push] Current permission:', permission);
  if (permission === 'default') {
    permission = await Notification.requestPermission();
  }
  if (permission !== 'granted') { console.warn('[Push] Permission not granted:', permission); return; }

  // ── Step 2: Subscribe to push ────────────────────────────────────────────────
  if (!('PushManager' in window)) { console.warn('[Push] PushManager not supported'); return; }

  const vapidKey = await getVapidPublicKey();
  console.log('[Push] VAPID key:', vapidKey ? vapidKey.slice(0, 20) + '…' : 'null');
  if (!vapidKey) return;

  console.log('[Push] Waiting for serviceWorker.ready…');
  let reg;
  try {
    // Timeout after 10 seconds — if SW never activates something is wrong
    reg = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) => setTimeout(() => reject(new Error('SW ready timeout')), 10000)),
    ]);
  } catch (err) {
    console.error('[Push] serviceWorker.ready failed:', err);
    return;
  }
  console.log('[Push] SW ready, scope:', reg.scope);

  let subscription = await reg.pushManager.getSubscription();
  console.log('[Push] Existing subscription:', subscription ? 'yes' : 'none');

  if (!subscription) {
    try {
      subscription = await reg.pushManager.subscribe({
        userVisibleOnly:      true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      });
      console.log('[Push] New subscription created:', subscription.endpoint.slice(0, 40) + '…');
    } catch (err) {
      console.error('[Push] pushManager.subscribe failed:', err);
      return;
    }
  }

  await sendSubscriptionToServer(subscription);
  console.log('[Push] Subscription synced to server — done.');
}
