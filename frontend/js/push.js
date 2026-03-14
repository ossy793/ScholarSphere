/**
 * Pritis Push Notification subscription manager.
 *
 * Call initPushNotifications() once after the user is authenticated.
 * It will ask for permission and register the device subscription.
 */

import { api } from './api.js';

const BASE_URL = 'https://www.pritis.name.ng/api';

// Convert a base64url string to a Uint8Array (required for VAPID subscribe)
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
  // Browser support check
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

  // Don't re-prompt if already granted or denied
  if (Notification.permission === 'denied') return;

  try {
    const vapidKey = await getVapidPublicKey();
    if (!vapidKey) return;  // VAPID not configured on server yet

    const reg = await navigator.serviceWorker.ready;

    // Check if already subscribed
    let subscription = await reg.pushManager.getSubscription();

    if (!subscription) {
      // Request permission + subscribe
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') return;

      subscription = await reg.pushManager.subscribe({
        userVisibleOnly:      true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      });
    }

    // Always sync subscription to server (handles token refresh)
    await sendSubscriptionToServer(subscription);

  } catch (err) {
    console.warn('Push notification setup failed:', err);
  }
}
