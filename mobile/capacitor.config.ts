import type { CapacitorConfig } from "@capacitor/cli";

/**
 * The Android shell around the deployed web app.
 *
 * `server.url` rather than bundled static assets, and the reason is the refresh
 * cookie. `frugal_refresh` is httpOnly and `SameSite=Lax`, scoped to
 * `/api/v1/auth`, and it works today because `frontend/next.config.ts` rewrites
 * `/api/*` so the browser only ever talks to one origin. Bundling the app would
 * put the WebView on `https://localhost`, making every call to the API
 * cross-site -- the cookie would be dropped and **every session would die
 * silently at the 900-second access-token expiry**. Pointing the WebView at the
 * real host keeps every request same-origin and needs no auth changes at all.
 *
 * It also keeps React Server Components, which a static export cannot have.
 *
 * The cost, stated plainly: Capacitor documents `server.url` as a live-reload
 * tool, not a production setting, and the app is useless offline -- it is a
 * browser pointed at one site, so if the site is down the app is down. The
 * Capacitor bridge is still injected into the remote page, which is what lets
 * the native SMS plugin work in the `smsreader` flavour.
 */
const HOST = process.env.CAPACITOR_SERVER_URL ?? "https://frugal.example.com";

/**
 * True only when pointed at a plain-HTTP host, which in practice means a laptop
 * on the same Wi-Fi during development.
 *
 * Derived rather than configured separately, so a production build *cannot*
 * accidentally ship with cleartext enabled: the only way to get it is to set an
 * http:// URL, which is itself the mistake you would be trying to catch. The
 * WebView carries a live session, and on a hostile network plain HTTP puts it
 * on the wire in the clear.
 */
const IS_LOCAL = HOST.startsWith("http://");

const config: CapacitorConfig = {
  appId: "app.frugal",
  appName: "Frugal",
  // Only the offline fallback ships inside the APK. Everything else is served.
  webDir: "shell",
  android: {
    // The WebView carries a live session. Cleartext would put it on the
    // network in plain text on any hostile Wi-Fi.
    allowMixedContent: false,
  },
  server: {
    url: HOST,
    cleartext: IS_LOCAL,
    // The exact host, never a wildcard. A permissive entry here is an open
    // redirect *into* the authenticated origin, inside a WebView that is
    // holding the user's session -- the worst possible place for one.
    allowNavigation: [new URL(HOST).host],
    errorPath: "offline.html",
  },
};

export default config;
