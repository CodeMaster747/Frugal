#!/usr/bin/env bash
#
# Put the published APKs' real SHA-256 sums on the download page.
#
# The page offers a self-signed binary from a website; without a checksum next
# to it a user has no way to tell a good download from a tampered one. Doing it
# by hand means that the day someone forgets, the page shows the *previous*
# build's hash -- which is worse than showing none, because a mismatch reads as
# evidence of tampering rather than as a stale page.
set -euo pipefail
cd "$(dirname "$0")/.."

PAGE="frontend/src/app/(marketing)/download/page.tsx"
DIR="frontend/public/downloads"

for f in frugal-latest.apk frugal-sms-latest.apk; do
  [ -f "$DIR/$f" ] || { echo "missing $DIR/$f -- build and copy the APKs first"; exit 1; }
done

python3 - "$PAGE" "$DIR" <<'PY'
import hashlib, pathlib, re, sys

page = pathlib.Path(sys.argv[1])
downloads = pathlib.Path(sys.argv[2])
text = page.read_text()

for key, name in (("standard", "frugal-latest.apk"), ("sms", "frugal-sms-latest.apk")):
    digest = hashlib.sha256((downloads / name).read_bytes()).hexdigest()
    pattern = re.compile("(" + key + r":\s*\{[^}]*?sha256:\s*\")[^\"]*(\")", re.S)
    text, n = pattern.subn(r"\g<1>" + digest + r"\g<2>", text, count=1)
    if n != 1:
        raise SystemExit("could not find the %s sha256 field in %s" % (key, page))
    print("  %-24s %s" % (name, digest))

page.write_text(text)
PY
echo "download page updated"
