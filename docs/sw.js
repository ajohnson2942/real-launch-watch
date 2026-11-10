// Watch-A-Launch service worker.
// OneSignal uses this worker for optional in-app web push notifications.
// The app does not cache launches.json, so launch data stays fresh.
importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");
