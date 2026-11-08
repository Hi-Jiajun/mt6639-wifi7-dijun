package com.hiliang.mt6639concurrency;

import android.os.Build;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

/** Forces only the vendor native-mode predicate, only while a valid short lease exists. */
public final class DualStaGateHook implements IXposedHookLoadPackage {
    private static final String TAG = "MT6639Concurrency";
    private static final String PACKAGE = "android";
    private static final String FINGERPRINT =
            "Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys";

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam param) {
        if (!PACKAGE.equals(param.packageName)) return;
        if (!FINGERPRINT.equals(Build.FINGERPRINT)) {
            log("SKIP unsupported fingerprint=" + Build.FINGERPRINT);
            return;
        }
        try {
            Class<?> stub = XposedHelpers.findClass(
                    "com.android.server.wifi.WifiOptimizationStub", param.classLoader);
            XposedHelpers.findAndHookMethod(stub, "isNativeModeActive", new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam method) {
                    if (!method.hasThrowable() && DualStaGate.active()) {
                        method.setResult(Boolean.TRUE);
                    }
                }
            });
            log("hooked bounded dual-STA native-mode predicate");
        } catch (Throwable error) {
            log("hook SKIP: " + error);
        }
    }

    private static void log(String message) {
        try {
            XposedBridge.log(TAG + " " + message);
        } catch (Throwable ignored) {
        }
    }
}
