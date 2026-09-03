#!/usr/bin/env bash
#
# Build the self-hosted vector basemap (ADR-014).
#
#   ./scripts/build-basemap.sh [BBOX] [MAXZOOM] [OUTPUT]
#
# Defaults to Chennai metro at zoom 14, which is ~7 MB and takes about twenty
# seconds. That size is the whole argument for self-hosting: there is no tile
# API to key, meter, or pay for, and no third party ever learns which part of a
# map a user is looking at.
#
# The extract reads the Protomaps daily planet build over HTTP range requests,
# so it downloads the region and not the 120 GB planet -- roughly forty requests
# for the default box.
#
# Adding a city is another run with a different bbox and output name. Serve
# whichever archives you build from the same directory; the frontend points at
# one URL, so combine regions into a single archive if you want one map.
#
# Requires the pmtiles CLI: https://github.com/protomaps/go-pmtiles/releases
#
# Licence: the basemap is an ODbL Produced Work derived from OpenStreetMap.
# Self-hosting is free for any use -- the sponsorship Protomaps asks for covers
# their *hosted* tile API, which this deliberately does not use. Attribution is
# required and is rendered by the map's attribution control.

set -euo pipefail

# Greater Chennai: Ennore in the north to Mahabalipuram Road in the south,
# Poonamallee in the west to the coast.
BBOX="${1:-80.10,12.83,80.35,13.25}"
MAXZOOM="${2:-14}"
OUTPUT="${3:-basemap.pmtiles}"

if ! command -v pmtiles >/dev/null 2>&1; then
  echo "pmtiles CLI not found." >&2
  echo "  https://github.com/protomaps/go-pmtiles/releases" >&2
  exit 1
fi

# The most recent daily build. Tried backwards rather than assuming today's
# exists: the build lands at a fixed hour, so "today" is a 404 for part of every
# day and a hardcoded date rots.
SOURCE=""
for offset in 1 2 3 4 5 6 7; do
  day=$(date -u -v-"${offset}"d +%Y%m%d 2>/dev/null || date -u -d "${offset} days ago" +%Y%m%d)
  candidate="https://build.protomaps.com/${day}.pmtiles"
  if [ "$(curl -s -o /dev/null -w '%{http_code}' -r 0-0 "${candidate}")" = "206" ]; then
    SOURCE="${candidate}"
    break
  fi
done

if [ -z "${SOURCE}" ]; then
  echo "No Protomaps daily build reachable in the last 7 days." >&2
  exit 1
fi

echo "source:  ${SOURCE}"
echo "bbox:    ${BBOX}"
echo "maxzoom: ${MAXZOOM}"
echo

pmtiles extract "${SOURCE}" "${OUTPUT}" \
  --bbox="${BBOX}" \
  --maxzoom="${MAXZOOM}" \
  --download-threads=8

echo
echo "Wrote ${OUTPUT} ($(du -h "${OUTPUT}" | cut -f1))"
echo
echo "Deploy: copy it to /opt/frugal/basemap/ on the API host, where the"
echo "Caddyfile serves /basemap/* with byte-range support, then set"
echo "NEXT_PUBLIC_BASEMAP_URL=https://<api-domain>/basemap/${OUTPUT} and rebuild"
echo "the frontend. Unset, the map still renders every pin over a plain"
echo "background and says so on screen."
