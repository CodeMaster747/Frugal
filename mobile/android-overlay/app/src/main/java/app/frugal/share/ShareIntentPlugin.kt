package app.frugal.share

import android.content.Intent
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin

/**
 * Receiving one message from the Android share sheet.
 *
 * In `src/main`, so it is in **both** builds, and it needs no permission at all
 * -- `ACTION_SEND` is the OS handing us text the user explicitly chose to hand
 * over. That makes it the only live SMS path available in the standard build,
 * and the only one that works for the 100% of users who can install it.
 *
 * The flow is: long-press a bank message in the Messages app, Share, pick
 * Frugal. One message at a time, which is laborious for a month of history and
 * exactly right for the transaction someone wants recorded now. The bulk case
 * is the SMS backup import.
 */
@CapacitorPlugin(name = "ShareIntent")
class ShareIntentPlugin : Plugin() {

    companion object {
        const val NATIVE_VERSION = 1
    }

    private var pendingText: String? = null

    override fun load() {
        // The launching intent, for the case where the app was not already
        // running. `handleOnNewIntent` covers the case where it was, and both
        // are needed -- a share into a cold app and a share into a warm one
        // arrive through different callbacks.
        capture(activity?.intent)
    }

    override fun handleOnNewIntent(intent: Intent) {
        super.handleOnNewIntent(intent)
        capture(intent)
        pendingText?.let {
            notifyListeners("sharedText", JSObject().put("text", it))
        }
    }

    private fun capture(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        if (intent.type != "text/plain") return
        intent.getStringExtra(Intent.EXTRA_TEXT)?.let { pendingText = it }
    }

    @PluginMethod
    fun nativeVersion(call: PluginCall) {
        call.resolve(JSObject().put("version", NATIVE_VERSION))
    }

    /**
     * The shared text, if the app was opened by a share.
     *
     * Consumed on read: the same share must not be ingested twice because the
     * user navigated away and back.
     */
    @PluginMethod
    fun consumeSharedText(call: PluginCall) {
        val text = pendingText
        pendingText = null
        call.resolve(JSObject().put("text", text))
    }
}
