package app.frugal;

import com.getcapacitor.BridgeActivity;

import app.frugal.share.ShareIntentPlugin;

/**
 * The standard build's plugins.
 *
 * Share sheet only. This build declares no permissions at all, which is the
 * whole reason it exists -- Play Protect blocks a website-sideloaded APK that
 * merely declares an SMS permission, so this is the only variant an ordinary
 * user can install.
 *
 * The class is duplicated per flavour rather than living in src/main, because
 * that is the only way each build can name a different set of plugins: a class
 * cannot exist in both main and a flavour source set.
 */
final class PluginRegistrar {

    private PluginRegistrar() {}

    static void register(BridgeActivity activity) {
        activity.registerPlugin(ShareIntentPlugin.class);
    }
}
