package app.frugal.sms

import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.getcapacitor.annotation.Permission
import org.json.JSONArray

/**
 * The bridge between the buffered messages and the WebView.
 *
 * Exists because native code cannot upload anything itself: the access token is
 * a JavaScript module closure and the refresh cookie is httpOnly and
 * path-scoped, so the receiver has no credentials. JavaScript drains the buffer
 * on foreground, when it does hold the token.
 *
 * Only compiled into the `smsreader` flavour. The web app must therefore guard
 * every call with `Capacitor.isPluginAvailable("SmsReceiver")`, so the standard
 * build and every browser see a clean absence rather than a rejected promise.
 */
@CapacitorPlugin(
    name = "SmsReceiver",
    permissions = [
        Permission(
            alias = "sms",
            strings = [
                android.Manifest.permission.READ_SMS,
                android.Manifest.permission.RECEIVE_SMS,
            ],
        ),
    ],
)
class SmsReceiverPlugin : Plugin() {

    /**
     * Bumped whenever this plugin's surface changes.
     *
     * A sideloaded APK has no update channel, so a deployed frontend can be
     * newer than every installed build. The app compares this against
     * `min_native_version` from `GET /system/providers` at boot and blocks
     * below it -- otherwise a frontend calling a method this build does not
     * have fails on every device at once, silently.
     */
    companion object {
        const val NATIVE_VERSION = 1
    }

    @PluginMethod
    fun nativeVersion(call: PluginCall) {
        call.resolve(JSObject().put("version", NATIVE_VERSION))
    }

    @PluginMethod
    fun hasPermission(call: PluginCall) {
        call.resolve(JSObject().put("granted", getPermissionState("sms")?.toString() == "granted"))
    }

    @PluginMethod
    fun requestPermission(call: PluginCall) {
        requestPermissionForAlias("sms", call, "permissionCallback")
    }

    @PluginMethod
    fun permissionCallback(call: PluginCall) {
        call.resolve(JSObject().put("granted", getPermissionState("sms")?.toString() == "granted"))
    }

    /**
     * Everything captured since the last successful upload.
     *
     * Read-only: the buffer is cleared by [acknowledge] once the server has
     * accepted the batch, not here. Clearing on read would lose a week of
     * transactions to one flaky connection, and the user would have no way to
     * know it happened.
     */
    @PluginMethod
    fun drainPending(call: PluginCall) {
        val pending: JSONArray = SmsBuffer.peek(context)
        call.resolve(JSObject().put("messages", pending).put("count", pending.length()))
    }

    @PluginMethod
    fun acknowledge(call: PluginCall) {
        SmsBuffer.clear(context)
        call.resolve()
    }
}
