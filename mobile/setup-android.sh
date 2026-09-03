#!/usr/bin/env bash
#
# Apply the Frugal overlay to a freshly generated Capacitor Android project.
#
# `npx cap add android` scaffolds a single-flavour, Java-only project. Everything
# this project needs on top of that is applied here rather than described in a
# README, because the platform directory is regenerable: delete `android/`, run
# `cap add android` again, run this, and you are back where you were.
#
#   npm install
#   npx cap add android
#   ./setup-android.sh
#   npx cap sync android
#
# Idempotent. Every step checks whether it has already been applied.
set -euo pipefail

cd "$(dirname "$0")"
APP=android/app

[ -d android ] || { echo "android/ not found — run 'npx cap add android' first"; exit 1; }

# --- 1. Kotlin -------------------------------------------------------------
# The receiver, its buffer and the generated sender filter are Kotlin. Capacitor
# scaffolds a Java-only build, so the plugin is added by hand.
if ! grep -q kotlin-gradle-plugin android/build.gradle; then
  python3 - <<'PY'
import pathlib
p = pathlib.Path("android/build.gradle")
s = p.read_text()
anchor = "        classpath 'com.google.gms:google-services"
line = [l for l in s.splitlines() if l.startswith(anchor)][0]
p.write_text(s.replace(
    line,
    line + "\n        classpath 'org.jetbrains.kotlin:kotlin-gradle-plugin:2.0.21'",
    1))
PY
  echo "  + kotlin-gradle-plugin"
fi

if ! grep -q "kotlin-android" $APP/build.gradle; then
  python3 - <<'PY'
import pathlib
p = pathlib.Path("android/app/build.gradle")
s = p.read_text()
p.write_text(s.replace(
    "apply plugin: 'com.android.application'",
    "apply plugin: 'com.android.application'\napply plugin: 'kotlin-android'", 1))
PY
  echo "  + kotlin-android"
fi

# --- 2. Product flavors and signing ----------------------------------------
if ! grep -q flavorDimensions $APP/build.gradle; then
  python3 - <<'PY'
import pathlib
p = pathlib.Path("android/app/build.gradle")
s = p.read_text()
block = pathlib.Path("android-overlay/app/build.gradle.flavors").read_text()
# Drop the header comment lines that are not Gradle.
block = "\n".join(l for l in block.splitlines() if not l.startswith("//"))
anchor = "    buildTypes {\n        release {\n            minifyEnabled false"
assert anchor in s, "buildTypes anchor missing — Capacitor's template changed"
s = s.replace(
    anchor,
    block.rstrip() + "\n\n" + anchor + "\n            signingConfig signingConfigs.release",
    1)
p.write_text(s)
PY
  echo "  + product flavors + signingConfigs"
fi

# The flavours set app_name and title_activity_main via resValue, which collides
# with the generated strings.xml entries.
python3 - <<'PY'
import pathlib, re
p = pathlib.Path("android/app/src/main/res/values/strings.xml")
s = p.read_text()
new = re.sub(r'\s*<string name="(app_name|title_activity_main)">[^<]*</string>', "", s)
if new != s:
    p.write_text(new)
    print("  + removed colliding strings.xml entries")
PY

# --- 3. Sources ------------------------------------------------------------
cp -R android-overlay/app/src/smsreader "$APP/src/"
mkdir -p "$APP/src/main/java/app/frugal/share"
cp android-overlay/app/src/main/java/app/frugal/share/ShareIntentPlugin.kt \
   "$APP/src/main/java/app/frugal/share/"
mkdir -p "$APP/src/standard/java/app/frugal"
cp android-overlay/app/src/standard/java/app/frugal/PluginRegistrar.java \
   "$APP/src/standard/java/app/frugal/"
cp android-overlay/app/src/smsreader/java/app/frugal/PluginRegistrar.java \
   "$APP/src/smsreader/java/app/frugal/"
cp android-overlay/app/src/main/java/app/frugal/MainActivity.java \
   "$APP/src/main/java/app/frugal/"
rm -f "$APP/src/main/java/app/frugal/MainActivity.kt"
echo "  + sources"

# --- 4. Share-sheet intent filter ------------------------------------------
if ! grep -q "android.intent.action.SEND" "$APP/src/main/AndroidManifest.xml"; then
  python3 - <<'PY'
import pathlib
p = pathlib.Path("android/app/src/main/AndroidManifest.xml")
s = p.read_text()
fragment = pathlib.Path(
    "android-overlay/app/src/main/AndroidManifest.xml.fragment").read_text()
# Strip the explanatory comment; keep the element.
body = fragment[fragment.index("<intent-filter>"):]
anchor = ('                <category android:name="android.intent.category.LAUNCHER" />\n'
          "            </intent-filter>\n")
assert anchor in s, "launcher intent-filter not found — Capacitor's template changed"
indented = "\n".join("            " + l if l.strip() else l for l in body.strip().splitlines())
p.write_text(s.replace(anchor, anchor + "\n" + indented + "\n", 1))
PY
  echo "  + share-sheet intent filter"
fi

# --- 5. SDK location -------------------------------------------------------
if [ ! -f android/local.properties ] && [ -d "$HOME/Library/Android/sdk" ]; then
  echo "sdk.dir=$HOME/Library/Android/sdk" > android/local.properties
  echo "  + local.properties"
fi

echo
echo "Overlay applied. Next:"
echo "  npx cap sync android"
echo "  cd android && ./gradlew assembleStandardDebug assembleSmsreaderDebug"
