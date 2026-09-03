package app.frugal.sms

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import org.json.JSONArray
import org.json.JSONObject

/**
 * Buffers bank alerts. Does nothing else, and cannot.
 *
 * The access token lives in a JavaScript module closure and the refresh cookie
 * is httpOnly and scoped to `/api/v1/auth`, so **native code holds no
 * credentials and has no way to obtain any**. A receiver that tried to upload a
 * captured message would have nothing to authenticate with.
 *
 * So the contract is deliberately small:
 *
 *  1. Apply the sender allowlist and the transactional gate. A message that
 *     fails either is discarded here and never written to disk.
 *  2. Append what passes to a capped local buffer.
 *  3. Stop.
 *
 * `SmsReceiverPlugin.drainPending()` hands the buffer to the WebView when the
 * app next comes to the foreground, at which point JavaScript has the token and
 * posts to `/api/v1/sms/messages/batch`. Live capture is therefore "captured
 * now, uploaded when you next open the app" -- which is fine, but it is what it
 * is, and the download page says so.
 */
class SmsBroadcastReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return

        // Multipart messages arrive as several PDUs that must be concatenated,
        // or a long bank alert is truncated mid-amount.
        val bySender = mutableMapOf<String, StringBuilder>()
        val timestamps = mutableMapOf<String, Long>()

        for (message in Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return) {
            val sender = message.originatingAddress ?: continue
            bySender.getOrPut(sender) { StringBuilder() }.append(message.messageBody ?: "")
            timestamps.putIfAbsent(sender, message.timestampMillis)
        }

        for ((sender, builder) in bySender) {
            val body = builder.toString()
            // The privacy boundary. Everything this rejects never reaches disk
            // on the handset and never reaches the server.
            if (!SenderFilter.isCapturable(sender, body)) continue
            SmsBuffer.append(context, sender, body, timestamps[sender] ?: System.currentTimeMillis())
        }
    }
}

/**
 * A bounded FIFO of captured messages, in private app storage.
 *
 * Capped because this fills without supervision: a handset that does not open
 * the app for a month would otherwise accumulate every alert in that month, and
 * an unbounded store of bank messages is exactly the liability this feature
 * spends so much effort avoiding. Oldest is dropped first — a missed
 * transaction is recoverable from the SMS backup import; unbounded growth is
 * not recoverable at all.
 */
object SmsBuffer {
    private const val PREFS = "frugal_sms_buffer"
    private const val KEY = "pending"
    private const val MAX_ENTRIES = 500

    @Synchronized
    fun append(context: Context, sender: String, body: String, receivedAt: Long) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val existing = JSONArray(prefs.getString(KEY, "[]"))

        val entry = JSONObject().apply {
            put("sender", sender)
            put("body", body)
            put("receivedAt", receivedAt)
        }

        val trimmed = JSONArray()
        val start = maxOf(0, existing.length() - (MAX_ENTRIES - 1))
        for (i in start until existing.length()) trimmed.put(existing.get(i))
        trimmed.put(entry)

        prefs.edit().putString(KEY, trimmed.toString()).apply()
    }

    @Synchronized
    fun peek(context: Context): JSONArray =
        JSONArray(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, "[]"))

    /**
     * Cleared only after the server has accepted the batch.
     *
     * Clearing on read would lose every buffered message to one failed upload —
     * a flaky connection on the train would silently drop a week of
     * transactions, and the user would have no way to know it happened.
     */
    @Synchronized
    fun clear(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY).apply()
    }
}
