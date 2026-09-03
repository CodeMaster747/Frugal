import type { NextConfig } from "next";

// Origin of the API, when it is not served from this same host. Unset locally,
// where `NEXT_PUBLIC_API_URL` points straight at http://localhost:8000 and the
// browser treats both as `localhost` -- one site, so the cookie below is not an
// issue there.
//
// In production it is the Caddy endpoint in front of the API, e.g.
// https://frugal-api.duckdns.org. Deliberately *not* NEXT_PUBLIC_: the browser
// never learns this address, because it never talks to it directly.
const backendOrigin = process.env.BACKEND_ORIGIN;

const nextConfig: NextConfig = {
  // The dev tools badge defaults to bottom-left, which is where the sidebar
  // footer puts the theme control and sign-out. It sits on top of them and
  // swallows the clicks. Dev-only overlay, so this changes nothing in
  // production -- it just stops the toolbar covering real controls.
  devIndicators: { position: "bottom-right" },

  // Proxy the API through this origin instead of letting the browser call it
  // across hosts.
  //
  // The refresh token is an httpOnly cookie set with `SameSite=Lax`
  // (backend/app/modules/auth/router.py), which a browser sends only on
  // same-site requests. Frontend and API are on different registrable domains
  // in production -- and `onrender.com` is itself on the Public Suffix List, so
  // even two Render subdomains count as different sites. A direct cross-site
  // fetch would therefore carry no refresh cookie, and every session would die
  // silently at the 15-minute access-token expiry.
  //
  // Rewriting keeps every request same-origin: the cookie travels, and there is
  // no CORS preflight on any call. The alternative -- `SameSite=None; Secure`
  // -- would work too, but it re-opens the CSRF surface that scoping the cookie
  // to /api/v1/auth was meant to close.
  //
  // Returning [] when unset leaves local development exactly as it was.
  // Kept to exactly the two paths the browser calls. `/health` on its own is
  // deliberately absent: that is the Financial Health page in this app, not the
  // API's liveness probe. An array return is matched after the filesystem, so
  // the page would win today -- but moving these to `beforeFiles` later would
  // silently replace a real screen with a JSON body, and that is not a failure
  // anyone would think to look for here.
  async rewrites() {
    if (!backendOrigin) return [];
    return [
      { source: "/api/:path*", destination: `${backendOrigin}/api/:path*` },
      { source: "/health/ready", destination: `${backendOrigin}/health/ready` },
      // The APK reads this at boot to compare its native plugin version. Same
      // reason as the two above: keeping it same-origin means no CORS and no
      // second host for the WebView to trust.
      { source: "/system/providers", destination: `${backendOrigin}/system/providers` },
    ];
  },

  // The APKs are static files under `public/downloads/`, which Next serves
  // directly. Without the explicit type some browsers render the binary
  // instead of saving it, and Android's installer never sees the file.
  //
  // `no-store` because the download page publishes each build's SHA-256 next to
  // the link: a cached older APK whose checksum no longer matches the page is
  // exactly the mismatch that makes a user think the download was tampered
  // with.
  async headers() {
    return [
      {
        source: "/downloads/:file*.apk",
        headers: [
          { key: "Content-Type", value: "application/vnd.android.package-archive" },
          { key: "Content-Disposition", value: "attachment" },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
    ];
  },
};

export default nextConfig;
