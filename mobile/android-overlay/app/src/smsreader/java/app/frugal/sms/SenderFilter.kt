package app.frugal.sms

// GENERATED FILE -- DO NOT EDIT.
//
// Emitted from backend/app/modules/sms/parser/senders.py by
// backend/scripts/generate_sender_filter.py. Regenerate with:
//
//     cd backend && python scripts/generate_sender_filter.py > \
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

    private val KNOWN_SENDERS = setOf(
        "AIRTLB",
        "ATMSBI",
        "AUBANK",
        "AXISBK",
        "AXISBL",
        "BOBIBK",
        "BOBSMS",
        "BOBTXN",
        "CANBNK",
        "CBSSBI",
        "CENTBK",
        "FEDBNK",
        "HDFCBK",
        "HDFCBN",
        "ICCICI",
        "ICICIB",
        "ICICIT",
        "IDBIBK",
        "IDFCFB",
        "INDBNK",
        "INDUSB",
        "KOTAKB",
        "PNBSMS",
        "PUNBNK",
        "PYTMPB",
        "RBLBNK",
        "SBICRD",
        "SBIINB",
        "SBIPSG",
        "SBIUPI",
        "UNIONB",
        "YESBNK",
    )

    private val KEYWORDS = setOf(
        "credit",
        "credited",
        "debit",
        "debited",
        "paid",
        "purchase",
        "received",
        "sent",
        "spent",
        "transaction",
        "transferred",
        "txn",
        "withdrawn",
    )

    private val DISQUALIFYING = setOf(
        "apply now",
        "click here",
        "collect request",
        "do not share",
        "eligible for",
        "has requested",
        "is requesting",
        "never share",
        "offer",
        "one time password",
        "one-time password",
        "otp",
        "pre-approved",
        "preapproved",
        "request for",
        "will be credited",
        "will be debited",
    )

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
