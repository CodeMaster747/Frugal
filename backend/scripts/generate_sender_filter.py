#!/usr/bin/env python
"""Generate the Kotlin sender filter from the Python one.

The device-side filter is the privacy boundary: what it rejects never reaches
disk on the handset and never reaches the server. Two hand-maintained copies of
that rule in two languages would drift, and the drift would be invisible --
the app would quietly start capturing (or quietly stop capturing) messages that
the server-side documentation says it does not.

So there is one source of truth, `app/modules/sms/parser/senders.py`, and this
emits the other. `test_device_allowlist_matches_kotlin` regenerates and compares,
so a change to the Python that is not regenerated fails the build.

Usage:
    python scripts/generate_sender_filter.py > \\
        ../mobile/android-overlay/app/src/smsreader/java/app/frugal/sms/SenderFilter.kt
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.sms.parser.senders import (
    DISQUALIFYING_PHRASES,
    KNOWN_SENDERS,
    TRANSACTIONAL_KEYWORDS,
)

HEADER = """package app.frugal.sms

// GENERATED FILE -- DO NOT EDIT.
//
// Emitted from backend/app/modules/sms/parser/senders.py by
// backend/scripts/generate_sender_filter.py. Regenerate with:
//
//     cd backend && python scripts/generate_sender_filter.py > \\
//       ../mobile/android-overlay/app/src/smsreader/java/app/frugal/sms/SenderFilter.kt
//
// `test_device_allowlist_matches_kotlin` regenerates this and compares, so a
// change to the Python that is not mirrored here fails the build. That test
// exists because this file is the privacy boundary: whatever it rejects never
// reaches disk on the handset and never reaches the server, and two copies of
// that rule drifting apart is the one failure here nobody would notice.

/**
 * The device-side filter, mirroring the server's.
 *
 * Applied in the broadcast receiver *before* a message is buffered, so a text
 * from a friend is never written to disk. Everything downstream -- the
 * templates, the confidence rules, the review queue -- only ever sees traffic
 * that passed here.
 */
object SenderFilter {

    /** `<2-char operator>-<header>`, optionally with a category suffix. */
    private val SENDER = Regex("^(?:[A-Z]{2}-)?([A-Z0-9]{4,11})(?:-[A-Z])?$")
"""

FOOTER = """
    /** The DLT header core, or null when this is not a commercial sender. */
    fun senderCore(sender: String): String? =
        SENDER.find(sender.trim().uppercase())?.groupValues?.get(1)

    /**
     * Whether a body is worth capturing at all.
     *
     * Conservative in the direction that costs a transaction rather than
     * invents one: a disqualifying phrase beats any number of transactional
     * keywords, because a false positive writes money into the ledger that was
     * never spent and a false negative writes nothing.
     */
    fun looksTransactional(body: String): Boolean {
        val lowered = body.lowercase()
        if (DISQUALIFYING.any { lowered.contains(it) }) return false
        val words = Regex("[a-z]+").findAll(lowered).map { it.value }.toSet()
        return words.any { it in KEYWORDS }
    }

    /** The complete filter. Nothing this rejects leaves the handset. */
    fun isCapturable(sender: String, body: String): Boolean =
        senderCore(sender) != null && looksTransactional(body)
}
"""


def _set(name: str, values: list[str]) -> str:
    entries = ",\n".join(f'        "{v}"' for v in values)
    return f"\n    private val {name} = setOf(\n{entries},\n    )\n"


def main() -> None:
    out = [HEADER]
    out.append(_set("KNOWN_SENDERS", sorted(KNOWN_SENDERS)))
    out.append(_set("KEYWORDS", sorted(TRANSACTIONAL_KEYWORDS)))
    out.append(_set("DISQUALIFYING", sorted(DISQUALIFYING_PHRASES)))
    out.append(FOOTER)
    sys.stdout.write("".join(out))


if __name__ == "__main__":
    main()
