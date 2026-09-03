# The Android app

Two APKs from one codebase, distributed from the project's own website. Not from
Google Play, and that is the decision everything else here follows from.

## Why two builds

Google Play Protect's enhanced fraud protection **blocks the installation** of
any app sideloaded from a browser, messaging app, or file manager that *declares*
`READ_SMS`, `RECEIVE_SMS`, `BIND_NOTIFICATION_LISTENER_SERVICE`, or an
accessibility service. It is a static scan of the manifest at install time —
before the app ever runs — so removing the runtime request changes nothing.

It is live in India ([announced October
2024](https://blog.google/intl/en-in/products/launching-enhanced-fraud-protection-pilot-in-india/),
since [expanded to 185
markets](https://blog.google/security/keeping-google-play-android-app-ecosystem-safe-2025/)),
and there is no published exemption or allowlist for legitimate developers.

That makes "download an SMS-reading APK from our website" and "our users can
install it" mutually exclusive for an ordinary Indian user. So:

| Build | `applicationId` | SMS permission | Installs from the website? |
|---|---|---|---|
| **`standard`** | `app.frugal` | none | **Yes**, cleanly, for everyone |
| **`smsreader`** | `app.frugal.sms` | `READ_SMS`, `RECEIVE_SMS` | No — needs `adb install` |

**`standard` is the real product.** It reaches transactions through the SMS
backup import (years of history in one upload, no permission at all) and the
Android share sheet. `smsreader` demonstrates that the architecture supports live
capture; its realistic adoption is close to zero, and it should never gate a
release.

### Why distinct application IDs, not a suffix

A user must not be able to "upgrade" from the no-permission build into the SMS
build — that is a permission escalation they never agreed to. Separately, Play
Protect's verdict on `app.frugal.sms` must not attach to `app.frugal`. The cost
is that they are two installs rather than one, which is the correct trade:
`standard`'s entire promise is that it *never* trips Play Protect.

## The permission separation

It is manifest-merger and nothing else:

- `android/app/src/main/AndroidManifest.xml` — no SMS permission, no receiver.
  Only the `ACTION_SEND` filter, which needs no permission.
- `android/app/src/smsreader/AndroidManifest.xml` — the two permissions and the
  `SMS_RECEIVED` receiver.

A `<uses-permission>` that drifts into `main/` is a two-character change that
silently breaks `standard`'s whole reason to exist, and nothing else catches it.
`.github/workflows/android.yml` runs `aapt dump permissions` over both outputs
and fails the build. **If only one Android test is ever written, it is that one.**

## What live capture actually is

Not real-time, and it cannot be. The access token lives in a JavaScript module
closure (`frontend/src/lib/api/client.ts`) and the refresh cookie is httpOnly and
path-scoped, so **native code has no credentials and cannot get any**. The
receiver therefore only ever *buffers*: it applies the sender allowlist, appends
matching messages to a capped local store, and does nothing else — no network, no
notification, no wake-up. The WebView drains that buffer on foreground, when JS
holds the token.

So the honest description is "captured now, uploaded when you next open the app".

## Version skew

With `server.url`, the JavaScript comes from the server and the native plugins
come from an APK that has no update channel. Ship a frontend that calls a plugin
method an installed build does not have and it fails on every device at once,
silently. The plugin reports `nativeVersion`; the app compares it against
`min_native_version` from `GET /system/providers` and blocks with a "download the
latest APK" screen below it.

## Building

Needs a JDK 21 and the Android SDK. `android/` is **generated and not
committed** — Capacitor owns it, and `setup-android.sh` is the complete
description of what this project needs on top of its scaffold. That means the
platform can be thrown away and rebuilt at any time, which is what CI does on
every run.

```bash
npm install
npx cap add android      # Capacitor scaffolds a single-flavour, Java-only project
./setup-android.sh       # applies the overlay; idempotent
npx cap sync android
cd android && ./gradlew assembleStandardDebug assembleSmsreaderDebug
```

### What the overlay adds, and why none of it is optional

| | Why |
|---|---|
| Kotlin Gradle plugin | Capacitor scaffolds Java-only. The receiver, its buffer and the generated sender filter are Kotlin. |
| Product flavors + `signingConfigs` | The two builds, and a self-signed release key read from the environment. |
| Removes `app_name`/`title_activity_main` from `strings.xml` | The flavours set them via `resValue`; leaving both defined is a duplicate-resource error. |
| `PluginRegistrar` per flavour | `MainActivity` is in `src/main` and compiles into both builds, so it cannot name `SmsReceiverPlugin` — that class only exists in `smsreader`. Each flavour registers its own set. |
| `ACTION_SEND` filter in `src/main` | The share sheet, in both builds, needing no permission. |

### Releasing

```bash
export FRUGAL_KEYSTORE="$PWD/../keystore/frugal.jks"
export FRUGAL_KEYSTORE_PASSWORD='…'
export FRUGAL_KEY_ALIAS='frugal'
export FRUGAL_KEY_PASSWORD='…'
./gradlew assembleStandardRelease assembleSmsreaderRelease
```

Then verify the separation held, publish the APKs to
`frontend/public/downloads/`, and put their SHA-256 sums on the download page:

```bash
AAPT=$(find "$ANDROID_HOME/build-tools" -name aapt2 | sort -V | tail -1)
"$AAPT" dump permissions app/build/outputs/apk/standard/release/*.apk | grep -i sms   # must print nothing
```

### One dependency worth watching

`capacitor-sms-inbox` is installed for both flavours and currently declares no
permissions in its own manifest, which is why the standard build stays clean. If
a future version adds `READ_SMS` there, it merges into **both** builds and the
standard APK silently becomes uninstallable. The CI permission check is what
catches that; do not remove it.
