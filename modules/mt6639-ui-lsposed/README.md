# MT6639 Wi-Fi band badges and hotspot controls

Settings-only LSPosed module for Xiaomi dijun on
`OS4.0.0.19.XODCNXM`.

The module:

- runs only in the main `com.android.settings` process;
- refuses to install hooks on any other SDK/fingerprint;
- restores module-owned fields before every OEM bind and attempts immediate
  restoration of the OEM row if applying a badge throws;
- reads ACTIVE MLO links for connected networks;
- uses current scan results for disconnected networks;
- renders `6G`, `2.4G/6G`, `5G/6G` or `2.4G/5G/6G` using the original
  HyperOS badge VectorDrawable and MiSansLatin path outlines.
- adds a status row and separate experimental hotspot controls to the native
  Wi-Fi hotspot page. It reads the root module's current-boot snapshot from
  `/data/misc/mt6639-hotspot/status` and submits fixed requests to the module's
  Settings-private request directory.
- offers 6GHz 20/40/80/160/320MHz and 5GHz 20/40/80MHz profiles when their
  readiness gates pass, plus stop and a bounded C02 validation action.
  C02 is a one-shot 2.4GHz HE validation, not a persistent hotspot profile.

For a connected MLO entry with an existing link list, only ACTIVE links are
counted. No ACTIVE links, unknown states or unreadable ACTIVE bands leave the
OEM display intact. Main-frequency fallback is used only for null/empty MLO
lists. Disconnected rows use their current scans; connecting/unknown states do
not use stale scan data to claim a connected band combination.

Missing, malformed, older-boot or unreadable
snapshots are shown as unavailable/incomplete rather than inferred from the
OEM hotspot switch. The 5GHz choices require the module to report its verified
2.4GHz upstream as ready. Requests contain a fixed action, boot, timestamp and
nonce; the KSU worker validates them before calling the existing controller.
The normal OEM hotspot switch is not repurposed. There is no generic shell
command input, arbitrary channel entry, or protocol-selection switch.

Version 0.2.6 is an uninstalled candidate. The current device was last observed
with UI 0.1.4; building and signing an APK does not prove Settings injection,
worker operation, Enforcing start/stop, or an external client's connection.
While a hotspot page is resumed, its controls read the existing snapshot every
two seconds on the main thread. Pause, view destruction and fragment destruction
cancel the refresh; repeated resume does not create another loop. Polling itself
submits no wireless request. A submitted request is not displayed as successful
until the backend's snapshot reports the new state. A successfully queued request
keeps the relevant controls disabled across page navigation while the old snapshot
remains unchanged. The wait uses a monotonic 60-second deadline; expiry displays
that the module has not confirmed the request and does not resend it. A submission
failure can be retried immediately.
See the [candidate installation and rollback guide](../../docs/交付-MT6639候选安装与回滚-20261026.md)
for the current hotspot backend and deployment prerequisites.

## Required carrier

The supplied LSPosed ZIP declares:

```text
LSPosed v2.2.1 (7912), Android 9 ~ 17
Requires Zygisk enabled
```

The APK therefore must be installed under a compatible Zygisk provider and
scoped to `com.android.settings` only. KernelSU, ZygiskNext, LSPosed enabling,
scope changes, reboot behavior and rollback are carrier-level tests; building
this APK does not prove those tests passed.

## Build

```sh
./build.sh
```

The sibling dijun-oneclick Android 35 API jar is used by default. Override with
`ANDROID_JAR=/path/to/android.jar`.

The build is Gradle-free: compile-only Xposed API stubs, `javac`, `jar`, `d8`,
`aapt2`, `aapt`, `zipalign` and `apksigner`. The stubs are not packaged into
the APK. `tests/test_mt6639_ui_module.sh` inspects the DEX class set with Linux `dexdump`
or, when available, `droidasc`.

## Install and verify

1. Install the module APK as root.
2. Enable it in LSPosed Manager.
3. Scope it to `com.android.settings` only.
4. Start a new Settings process.
5. Confirm `MT6639UI injected` and hook installation in the LSPosed file log.
   Platform logcat is an additional channel and may be empty on this device.

Platform log:

```sh
adb logcat -s MT6639UI
```

LSPosed file log:

```sh
adb shell su -c 'tail -n 200 /data/adb/lspd/log/modules_*.log'
```

The module logs the exact build fingerprint on skip, hook installation status,
and at most the first 20 render decisions. It does not log SSIDs, BSSIDs, MACs,
IPs or credentials.

## Independent badge switch

```sh
adb shell settings put system mt6639_ui_badges_enabled 0  # OEM display
adb shell settings put system mt6639_ui_badges_enabled 1  # badges enabled
adb shell settings delete system mt6639_ui_badges_enabled # default (enabled)
adb shell am force-stop com.android.settings
```

Run `settings` as root if shell writes are denied. The setting is read on each
row bind; reopening Settings gives deterministic immediate application to all
visible rows. Absent means enabled for compatibility with version 0.1.0; only
the exact value `1` otherwise enables badges. Malformed values and read errors
skip the overlay. This switch changes no Wi-Fi/scan/regulatory state.

`tests/test_mt6639_ui_bands.sh` executes the production band resolver on the
host. `sh tests/test_mt6639_ui_views.sh --device` builds a separate test-only
APK and exercises production row restoration on real Android Views; it does
not inject into Settings. APK structure checks alone do not prove behavior.

## Rollback

Disable the module in LSPosed Manager, then force-stop `com.android.settings`.
If the Settings UI is unstable, remove or disable LSPosed through KernelSU and
reboot. No original APK or Wi-Fi configuration is modified by this module.
