package com.hiliang.mt6639ui;

/** Pure device + SDK gate shared by Xposed entrypoints and host tests. */
final class DeviceGate {
    private DeviceGate() {
    }

    static boolean isSupported(String device, int sdkInt) {
        if (sdkInt != 35 && sdkInt != 36 && sdkInt != 37) {
            return false;
        }
        return "dijun".equals(device)
                || "violin".equals(device)
                || "jinghu".equals(device);
    }
}
