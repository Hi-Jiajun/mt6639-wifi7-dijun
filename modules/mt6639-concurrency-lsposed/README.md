# MT6639 bounded dual-STA gate

Experimental LSPosed module for Dijun only. It hooks
`com.android.server.wifi.WifiOptimizationStub.isNativeModeActive()` and returns
true only while a root-written short lease exists.

The lease is valid only when all of these match:

- exact Dijun build fingerprint;
- current kernel boot ID;
- expiry no more than 120 seconds away and still in the future;
- strict schema and 16-hex-character lease.

Without a valid lease the OEM method result is unchanged. The module does not
write system properties, switch multi-internet mode, create interfaces or
forge the `android.net.wifi.cts` package name.

This is a diagnostic hook for a bounded dual-STA experiment. It is not part of
the default release until C01 obtains two real ClientModeManagers, two
independent network paths and clean recovery.
