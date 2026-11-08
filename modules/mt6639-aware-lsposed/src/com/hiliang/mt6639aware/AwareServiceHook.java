package com.hiliang.mt6639aware;

import android.os.Build;

import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicBoolean;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

/**
 * Best-effort, exception-isolated registration of the missing wifiaware service.
 *
 * The candidate injects before the service list is sealed, while still isolating
 * Aware phase failures from system_server. It never attaches Aware, starts
 * discovery, changes Wi-Fi state, or propagates an Aware failure into boot.
 */
public final class AwareServiceHook implements IXposedHookLoadPackage {
    private static final String TAG = "MT6639Aware";
    private static final String EXPECTED_FINGERPRINT =
            "Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys";
    private static final String AWARE_FEATURE = "android.hardware.wifi.aware";
    private static final AtomicBoolean ATTEMPTED = new AtomicBoolean(false);
    private static volatile boolean phase500Ok;
    private static volatile boolean phase1000Ok;

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpparam) {
        if (!"android".equals(lpparam.packageName) ||
                !"android".equals(lpparam.processName) ||
                Build.VERSION.SDK_INT != 37 ||
                !EXPECTED_FINGERPRINT.equals(Build.FINGERPRINT)) {
            return;
        }
        try {
            Class<?> managerClass = XposedHelpers.findClass(
                    "com.android.server.SystemServiceManager", lpparam.classLoader);
            for (Method method : managerClass.getDeclaredMethods()) {
                if (!"sealStartedServices".equals(method.getName()) ||
                        method.getParameterTypes().length != 0) {
                    continue;
                }
                XposedBridge.hookMethod(method, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
                        registerAware(param.thisObject, lpparam.classLoader);
                    }
                });
            }
            Class<?> awareClass = XposedHelpers.findClass(
                    "com.android.server.wifi.aware.WifiAwareService",
                    lpparam.classLoader);
            XposedHelpers.findAndHookMethod(awareClass, "onBootPhase", int.class,
                    new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            int phase = (Integer) param.args[0];
                            if (!param.hasThrowable()) {
                                if (phase == 500) phase500Ok = true;
                                if (phase == 1000) phase1000Ok = true;
                                log("phase " + phase + " ok");
                                return;
                            }
                            log("phase " + phase + " failure isolated: "
                                    + param.getThrowable());
                            param.setResult(null);
                        }
                    });
            Class<?> packageManagerClass = XposedHelpers.findClass(
                    "com.android.server.pm.PackageManagerService",
                    lpparam.classLoader);
            XposedHelpers.findAndHookMethod(packageManagerClass,
                    "hasSystemFeature", String.class, int.class,
                    new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            if (!AWARE_FEATURE.equals(param.args[0]) ||
                                    Boolean.TRUE.equals(param.getResult())) {
                                return;
                            }
                            try {
                                if (phase500Ok && checkService("wifiaware") != null) {
                                    param.setResult(true);
                                }
                            } catch (Throwable error) {
                                log("feature visibility SKIP " + error);
                            }
                        }
                    });
            log("hook installed");
        } catch (Throwable error) {
            log("hook install SKIP " + error);
        }
    }

    private static void registerAware(Object systemServiceManager, ClassLoader loader) {
        if (!ATTEMPTED.compareAndSet(false, true)) return;
        try {
            Object existing = checkService("wifiaware");
            if (existing != null) {
                log("wifiaware already registered");
                return;
            }
            Object context = XposedHelpers.getObjectField(
                    systemServiceManager, "mContext");
            Class<?> serviceClass = XposedHelpers.findClass(
                    "com.android.server.wifi.aware.WifiAwareService", loader);
            Object service = XposedHelpers.newInstance(serviceClass, context);
            XposedHelpers.callMethod(systemServiceManager, "startService", service);
            log("wifiaware registered before service-list seal");
        } catch (Throwable error) {
            ATTEMPTED.set(false);
            log("wifiaware registration FAILED " + error);
        }
    }

    private static Object checkService(String name) throws Exception {
        Class<?> serviceManager = Class.forName("android.os.ServiceManager");
        Method check = serviceManager.getMethod("checkService", String.class);
        return check.invoke(null, name);
    }

    private static void log(String message) {
        try {
            XposedBridge.log(TAG + " " + message);
        } catch (Throwable ignored) {
        }
    }
}
