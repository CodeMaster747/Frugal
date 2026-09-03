package app.frugal;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Which plugins exist depends on the flavour, so the list lives in a
        // flavour-specific PluginRegistrar rather than here. This class is in
        // src/main and therefore compiles into BOTH builds -- naming
        // SmsReceiverPlugin directly would break the standard build, which does
        // not contain it.
        //
        // Registration must precede super.onCreate: the bridge is built there,
        // and a plugin registered afterwards is invisible to it.
        PluginRegistrar.register(this);
        super.onCreate(savedInstanceState);
    }
}
