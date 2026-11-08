# MT6639 bounded Wi-Fi Aware service candidate

Experimental LSPosed module for Dijun only. It targets the `android` package and
does not include a default scope.

The device exposes `WIFI_FEATURE_AWARE`, but currently has neither
`android.hardware.wifi.aware` nor the `wifiaware` Binder. This module hooks
`SystemServiceManager.sealStartedServices()` before the list becomes immutable:

1. checks whether `wifiaware` already exists;
2. reflectively creates the real `com.android.server.wifi.aware.WifiAwareService`;
3. calls the public `SystemServiceManager.startService(SystemService)`;
4. lets the normal phase 500/1000 lifecycle initialize it.

Assembly is inside `try/catch`, and exceptions from `WifiAwareService.onBootPhase`
are isolated to logs. It does not call `aware-attach`, publish/subscribe, request
a data path, create a NAN interface, or alter the current Wi-Fi connection.

After the `wifiaware` Binder exists and phase 500 completes without exception,
the module also makes the exact
`android.hardware.wifi.aware` PackageManager feature visible to normal apps. If
either condition fails, single-feature queries remain at the OEM result. The
`getSystemAvailableFeatures()` enumeration is not modified and can still omit
the feature.

This candidate proves service assembly and feature visibility only. It does not
prove discovery or data-path support.
