package app.frugal;

import com.getcapacitor.BridgeActivity;

import app.frugal.share.ShareIntentPlugin;
import app.frugal.sms.SmsReceiverPlugin;

/**
 * The smsreader build's plugins.
 *
 * The share sheet, plus the bridge that hands buffered bank messages to the
 * WebView. The receiver itself cannot upload anything -- native code holds no
 * credentials -- so SmsReceiverPlugin is how captured messages ever reach the
 * server: JavaScript drains the buffer on foreground, when it does have the
 * access token.
 */
final class PluginRegistrar {

    private PluginRegistrar() {}

    static void register(BridgeActivity activity) {
        activity.registerPlugin(ShareIntentPlugin.class);
        activity.registerPlugin(SmsReceiverPlugin.class);
    }
}
