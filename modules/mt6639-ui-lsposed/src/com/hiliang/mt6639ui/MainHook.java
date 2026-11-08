package com.hiliang.mt6639ui;

import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import java.lang.ref.WeakReference;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.WeakHashMap;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

/**
 * Settings-only Xposed entry point.
 *
 * The module is fail-open: any missing class, changed method signature or
 * drawing error leaves the original HyperOS Wi-Fi row untouched.
 */
public final class MainHook implements IXposedHookLoadPackage {
    private static final String TAG = "MT6639UI";
    private static final String TARGET_PACKAGE = "com.android.settings";
    private static final String TARGET_PROCESS = "com.android.settings";
    private static final String EXPECTED_FINGERPRINT =
            "Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys";

    private static final String[] ENTRY_PREFERENCES = new String[] {
            "com.android.settings.wifi.MiuiWifiEntryPreference"
    };
    private static final String HOTSPOT_STATUS_KEY = "mt6639_experimental_hotspot_status";
    private static final String HOTSPOT_START_KEY = "mt6639_experimental_hotspot_start";
    private static final String HOTSPOT_STOP_KEY = "mt6639_experimental_hotspot_stop";
    private static final String HOTSPOT_C02_KEY = "mt6639_experimental_hotspot_c02";
    private static final long HOTSPOT_REFRESH_INTERVAL_MS = 2000;
    private static final long HOTSPOT_REQUEST_TIMEOUT_MS = 60000;
    // Process-local request guard survives page navigation without retaining UI.
    private static PendingHotspotRequest pendingHotspotRequest;
    private static String hotspotRequestNotice;
    // Accessed only by Settings' main-thread fragment lifecycle / Handler.
    private static final WeakHashMap<Object, HotspotRefresh> HOTSPOT_REFRESHES =
            new WeakHashMap<>();
    private static final String[][] HOTSPOT_START_ACTIONS = new String[][] {
            {"20", "启动 6 GHz 热点（20 MHz）", "兼容优先；EHT 当前实测基线"},
            {"40", "启动 6 GHz 热点（40 MHz）", "实验档；需要驱动和 AP 接受 40 MHz"},
            {"80", "启动 6 GHz 热点（80 MHz）", "实验档；需要驱动和 AP 接受 80 MHz"},
            {"160", "启动 6 GHz 热点（160 MHz）", "实验档；需要驱动和 AP 接受 160 MHz"},
            {"320", "启动 6 GHz 热点（320 MHz）", "最高带宽实验档；必须有可用 320 MHz 信道"}
    };
    private static final String[][] HOTSPOT_START5G_ACTIONS = new String[][] {
            {"20", "启动 5 GHz 热点（20 MHz）", "实验档；需要 2.4GHz STA 上游"},
            {"40", "启动 5 GHz 热点（40 MHz）", "实验档；需要 2.4GHz STA 上游"},
            {"80", "启动 5 GHz 热点（80 MHz）", "实验档；当前 5GHz profile 上限"}
    };
    private static final String[][] HOTSPOT_START2G_ACTIONS = new String[][] {
            {"20", "启动 2.4 GHz 热点（20 MHz）", "HE 11ax 档；可与 5/6GHz 上游并存"}
    };

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpparam) {
        log("injected pkg=" + lpparam.packageName
                + " process=" + lpparam.processName
                + " sdk=" + Build.VERSION.SDK_INT
                + " loader=" + shortLoader(lpparam.classLoader));

        if (!TARGET_PACKAGE.equals(lpparam.packageName)
                || !TARGET_PROCESS.equals(lpparam.processName)) {
            return;
        }
        if (Build.VERSION.SDK_INT != 37
                || !EXPECTED_FINGERPRINT.equals(Build.FINGERPRINT)) {
            log("SKIP unsupported build fingerprint=" + Build.FINGERPRINT);
            return;
        }

        BadgeRenderer.init(lpparam.classLoader);

        for (String className : ENTRY_PREFERENCES) {
            hookPreference(lpparam.classLoader, className, false);
        }
        hookPreference(
                lpparam.classLoader,
                "com.android.settings.wifi.MiuiAccessPointPreference",
                true
        );
        hookHotspotSettings(
                lpparam.classLoader,
                "com.android.settings.MiuiTetherSettings",
                "miui_tether_common_category"
        );
        hookHotspotSettings(
                lpparam.classLoader,
                "com.android.settings.AllInOneTetherSettings",
                "wifi_tether_settings_group"
        );
    }

    private static void hookHotspotSettings(
            final ClassLoader classLoader,
            final String className,
            final String groupKey
    ) {
        try {
            Class<?> fragmentClass = XposedHelpers.findClass(className, classLoader);
            // Install cancellation first: a changed lifecycle must never leave
            // an onResume refresh loop running without its matching stop hook.
            hookHotspotRefreshStop(fragmentClass, "onPause");
            hookHotspotRefreshStop(fragmentClass, "onDestroyView");
            hookHotspotRefreshStop(fragmentClass, "onDestroy");
            XposedHelpers.findAndHookMethod(
                    fragmentClass,
                    "onCreate",
                    Bundle.class,
                    new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            if (param.hasThrowable()) return;
                            try {
                                addHotspotStatusPreference(
                                        param.thisObject, classLoader, groupKey);
                            } catch (Throwable t) {
                                log("hotspot status add SKIP " + className + ": " + t);
                            }
                        }
                    }
            );
            XposedHelpers.findAndHookMethod(
                    fragmentClass,
                    "onResume",
                    new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            if (param.hasThrowable()) return;
                            try {
                                startHotspotStatusRefresh(param.thisObject, groupKey);
                            } catch (Throwable t) {
                                log("hotspot status refresh SKIP " + className + ": " + t);
                            }
                        }
                    }
            );
            log("hotspot status hooked " + className);
        } catch (Throwable t) {
            log("hotspot status hook SKIP " + className + ": " + t);
        }
    }

    private static void hookHotspotRefreshStop(
            final Class<?> fragmentClass, String methodName
    ) throws NoSuchMethodException {
        // Settings may inherit these callbacks. Xposed's findAndHookMethod
        // requires the declaring class, so resolve it and filter its receivers.
        Class<?> declaringClass = fragmentClass;
        while (declaringClass != null) {
            try {
                declaringClass.getDeclaredMethod(methodName);
                break;
            } catch (NoSuchMethodException missing) {
                declaringClass = declaringClass.getSuperclass();
            }
        }
        if (declaringClass == null) throw new NoSuchMethodException(methodName);
        XposedHelpers.findAndHookMethod(declaringClass, methodName, new XC_MethodHook() {
            @Override
            protected void beforeHookedMethod(MethodHookParam param) {
                if (!fragmentClass.isInstance(param.thisObject)) return;
                HotspotRefresh refresh = HOTSPOT_REFRESHES.remove(param.thisObject);
                if (refresh != null) refresh.stop();
            }
        });
    }

    private static void startHotspotStatusRefresh(Object fragment, String groupKey) {
        if (HOTSPOT_REFRESHES.containsKey(fragment)) return;
        HotspotRefresh refresh = new HotspotRefresh(fragment, groupKey);
        HOTSPOT_REFRESHES.put(fragment, refresh);
        refresh.run();
    }

    private static final class HotspotRefresh implements Runnable {
        private final WeakReference<Object> fragment;
        private final String groupKey;
        private final Handler handler = new Handler(Looper.getMainLooper());
        private boolean stopped;

        HotspotRefresh(Object fragment, String groupKey) {
            this.fragment = new WeakReference<>(fragment);
            this.groupKey = groupKey;
        }

        @Override
        public void run() {
            Object current = fragment.get();
            if (stopped || current == null) return;
            try {
                updateHotspotStatusPreference(current, groupKey);
            } catch (Throwable error) {
                log("hotspot status refresh SKIP: " + error);
            }
            if (!stopped) handler.postDelayed(this, HOTSPOT_REFRESH_INTERVAL_MS);
        }

        void stop() {
            stopped = true;
            handler.removeCallbacks(this);
            fragment.clear();
        }
    }

    private static void addHotspotStatusPreference(
            Object fragment,
            ClassLoader classLoader,
            String groupKey
    ) throws Exception {
        Object group = XposedHelpers.callMethod(fragment, "findPreference", groupKey);
        if (group == null) return;
        Object status = ensureHotspotPreference(
                fragment, group, classLoader, HOTSPOT_STATUS_KEY, "MT6639 实验热点");
        Object stop = ensureHotspotPreference(
                fragment, group, classLoader, HOTSPOT_STOP_KEY, "停止 MT6639 实验热点");
        Object start = ensureHotspotPreference(
                fragment, group, classLoader, HOTSPOT_START_KEY, "启动 MT6639 实验热点");
        Object c02 = ensureHotspotPreference(
                fragment, group, classLoader, HOTSPOT_C02_KEY,
                "运行 2.4GHz STA+AP 并发验收");
        Object context = XposedHelpers.callMethod(fragment, "getContext");
        XposedHelpers.callMethod(status, "setSelectable", false);
        XposedHelpers.callMethod(start, "setSummary", "选择 6 GHz 带宽后启动固定参数热点");
        XposedHelpers.callMethod(stop, "setSummary", "停止实验热点并等待原 LabNet-6G 连接恢复");
        XposedHelpers.callMethod(
                c02, "setSummary",
                "一次性 C02：保留 LabNet-6G STA，启动 2.4GHz/20MHz/11ax；结束后自动恢复");
        installHotspotStartPicker(
                start, classLoader, (Context) context);
        installHotspotAction(stop, classLoader, context, "stop");
        installHotspotAction(c02, classLoader, context, "c02-24g");
        updateHotspotStatusPreference(fragment, groupKey);
    }

    private static Object ensureHotspotPreference(
            Object fragment,
            Object group,
            ClassLoader classLoader,
            String key,
            String title
    ) throws Exception {
        Object existing = XposedHelpers.callMethod(group, "findPreference", key);
        if (existing != null) return existing;
        Class<?> contextClass = Class.forName("android.content.Context", false, classLoader);
        Class<?> preferenceClass = XposedHelpers.findClass(
                "androidx.preference.Preference", classLoader);
        Object preference = preferenceClass.getConstructor(contextClass)
                .newInstance(XposedHelpers.callMethod(fragment, "getContext"));
        XposedHelpers.callMethod(preference, "setKey", key);
        XposedHelpers.callMethod(preference, "setTitle", title);
        XposedHelpers.callMethod(preference, "setPersistent", false);
        try {
            XposedHelpers.callMethod(preference, "setIconSpaceReserved", false);
        } catch (Throwable ignored) {
            // OEM versions without this optional Preference method retain normal spacing.
        }
        XposedHelpers.callMethod(group, "addPreference", preference);
        return preference;
    }

    private static void installHotspotAction(
            final Object preference,
            ClassLoader classLoader,
            final Object context,
            final String operation
    ) throws Exception {
        Class<?> listenerClass = XposedHelpers.findClass(
                "androidx.preference.Preference$OnPreferenceClickListener", classLoader);
        Object listener = Proxy.newProxyInstance(
                listenerClass.getClassLoader(),
                new Class<?>[] { listenerClass },
                new InvocationHandler() {
                    @Override
                    public Object invoke(Object proxy, Method method, Object[] args) {
                        if ("onPreferenceClick".equals(method.getName())) {
                            submitHotspotRequest((Context) context, preference, operation);
                            return true;
                        }
                        if ("toString".equals(method.getName())) {
                            return "MT6639HotspotAction(" + operation + ")";
                        }
                        if ("hashCode".equals(method.getName())) {
                            return System.identityHashCode(proxy);
                        }
                        if ("equals".equals(method.getName())) {
                            return proxy == args[0];
                        }
                        throw new UnsupportedOperationException(method.getName());
                    }
                }
        );
        XposedHelpers.callMethod(preference, "setOnPreferenceClickListener", listener);
    }

    private static void installHotspotStartPicker(
            final Object preference,
            ClassLoader classLoader,
            final Context context
    ) throws Exception {
        Class<?> listenerClass = XposedHelpers.findClass(
                "androidx.preference.Preference$OnPreferenceClickListener", classLoader);
        Object listener = Proxy.newProxyInstance(
                listenerClass.getClassLoader(),
                new Class<?>[] { listenerClass },
                new InvocationHandler() {
                    @Override
                    public Object invoke(Object proxy, Method method, Object[] args) {
                        if ("onPreferenceClick".equals(method.getName())) {
                            showHotspotStartPicker(context, preference);
                            return true;
                        }
                        if ("toString".equals(method.getName())) {
                            return "MT6639HotspotStartPicker";
                        }
                        if ("hashCode".equals(method.getName())) {
                            return System.identityHashCode(proxy);
                        }
                        if ("equals".equals(method.getName())) {
                            return proxy == args[0];
                        }
                        throw new UnsupportedOperationException(method.getName());
                    }
                }
        );
        XposedHelpers.callMethod(preference, "setOnPreferenceClickListener", listener);
    }

    private static void showHotspotStartPicker(
            Context context,
            final Object preference
    ) {
        HotspotStatus.Snapshot snapshot = HotspotStatus.readSnapshot();
        ArrayList<String> labels = new ArrayList<>();
        ArrayList<String> actions = new ArrayList<>();
        if (snapshot.canStart6g()) {
            for (String[] action : HOTSPOT_START_ACTIONS) {
                labels.add("6 GHz · " + action[0] + " MHz · " + action[2]);
                actions.add("start-6g-" + action[0]);
            }
        }
        if (snapshot.canStart5g()) {
            for (String[] action : HOTSPOT_START5G_ACTIONS) {
                labels.add("5 GHz · " + action[0] + " MHz · " + action[2]);
                actions.add("start-5g-" + action[0]);
            }
        }
        if (snapshot.canStart2g()) {
            for (String[] action : HOTSPOT_START2G_ACTIONS) {
                labels.add("2.4 GHz · " + action[0] + " MHz · " + action[2]);
                actions.add("start-2g-" + action[0]);
            }
        }
        if (labels.isEmpty()) return;
        final int[] selected = new int[] {0};
        final CharSequence[] items = labels.toArray(new CharSequence[0]);
        final String[] startActions = actions.toArray(new String[0]);
        new AlertDialog.Builder(context)
                .setTitle("选择实验热点频段与带宽")
                .setSingleChoiceItems(items, selected[0], new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        selected[0] = which;
                    }
                })
                .setNegativeButton("取消", null)
                .setPositiveButton("启动", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        submitHotspotRequest(context, preference, startActions[selected[0]]);
                    }
                })
                .show();
    }

    private static final class PendingHotspotRequest {
        final String previousState;
        final long submittedAt = SystemClock.elapsedRealtime();

        PendingHotspotRequest(String previousState) {
            this.previousState = previousState;
        }
    }

    private static void updatePendingHotspotRequest(HotspotStatus.Snapshot snapshot) {
        if (pendingHotspotRequest == null) return;
        if (snapshot.available && !pendingHotspotRequest.previousState.equals(snapshot.state)) {
            // A changed snapshot is observable progress, never a fabricated success.
            pendingHotspotRequest = null;
            hotspotRequestNotice = null;
        } else if (SystemClock.elapsedRealtime() - pendingHotspotRequest.submittedAt
                >= HOTSPOT_REQUEST_TIMEOUT_MS) {
            pendingHotspotRequest = null;
            hotspotRequestNotice = "请求等待已超时（60 秒），模块尚未确认；请核对状态后重试";
        }
    }

    private static void submitHotspotRequest(
            Context context, Object preference, String operation
    ) {
        HotspotStatus.Snapshot snapshot = HotspotStatus.readSnapshot();
        updatePendingHotspotRequest(snapshot);
        // Also guard stale dialogs / callbacks before the next polling tick.
        if (pendingHotspotRequest != null) {
            XposedHelpers.callMethod(preference, "setSummary", hotspotRequestNotice);
            XposedHelpers.callMethod(preference, "setEnabled", false);
            return;
        }
        boolean eligible;
        if ("stop".equals(operation)) {
            eligible = snapshot.canStop();
        } else if ("c02-24g".equals(operation) || operation.startsWith("start-6g-")) {
            eligible = snapshot.canStart6g();
        } else if (operation.startsWith("start-2g-")) {
            eligible = snapshot.canStart2g();
        } else {
            eligible = snapshot.canStart5g();
        }
        if (!eligible) {
            XposedHelpers.callMethod(preference, "setSummary", snapshot.summary);
            XposedHelpers.callMethod(preference, "setEnabled", false);
            return;
        }
        HotspotRequestWriter.Result result = HotspotRequestWriter.submit(context, operation);
        hotspotRequestNotice = result.submitted
                ? result.message + "（等待模块状态变化，最长 60 秒）" : result.message;
        if (result.submitted) pendingHotspotRequest = new PendingHotspotRequest(snapshot.state);
        XposedHelpers.callMethod(preference, "setSummary", hotspotRequestNotice);
        XposedHelpers.callMethod(preference, "setEnabled", !result.submitted);
    }

    private static void updateHotspotStatusPreference(Object fragment, String groupKey) {
        Object group = XposedHelpers.callMethod(fragment, "findPreference", groupKey);
        if (group == null) return;
        HotspotStatus.Snapshot snapshot = HotspotStatus.readSnapshot();
        updatePendingHotspotRequest(snapshot);
        boolean waiting = pendingHotspotRequest != null;
        Object status = XposedHelpers.callMethod(group, "findPreference", HOTSPOT_STATUS_KEY);
        Object start = XposedHelpers.callMethod(group, "findPreference", HOTSPOT_START_KEY);
        Object stop = XposedHelpers.callMethod(group, "findPreference", HOTSPOT_STOP_KEY);
        Object c02 = XposedHelpers.callMethod(group, "findPreference", HOTSPOT_C02_KEY);
        String notice = hotspotRequestNotice == null ? "" : "\n" + hotspotRequestNotice;
        if (status != null) XposedHelpers.callMethod(status, "setSummary", snapshot.summary + notice);
        if (start != null) XposedHelpers.callMethod(start, "setSummary", snapshot.startHint() + notice);
        if (stop != null) XposedHelpers.callMethod(stop, "setSummary",
                "停止实验热点并等待原 LabNet-6G 连接恢复" + notice);
        if (c02 != null) XposedHelpers.callMethod(c02, "setSummary",
                "一次性 C02：保留 LabNet-6G STA，启动 2.4GHz/20MHz/11ax；结束后自动恢复" + notice);
        updateActionPreference(start, snapshot.isIdle(), !waiting && snapshot.canStart());
        updateActionPreference(stop, snapshot.canStop(), !waiting && snapshot.canStop());
        updateActionPreference(c02, snapshot.isIdle(), !waiting && snapshot.canStart6g());
    }

    private static void updateActionPreference(
            Object preference,
            boolean visible,
            boolean enabled
    ) {
        if (preference == null) return;
        XposedHelpers.callMethod(preference, "setVisible", visible);
        XposedHelpers.callMethod(preference, "setSelectable", enabled);
        XposedHelpers.callMethod(preference, "setEnabled", enabled);
    }

    private static void hookPreference(
            final ClassLoader classLoader,
            final String className,
            final boolean accessPointPath
    ) {
        try {
            Class<?> preferenceClass = XposedHelpers.findClass(className, classLoader);
            Class<?> holderClass = XposedHelpers.findClass(
                    "androidx.preference.PreferenceViewHolder",
                    classLoader
            );
            XposedHelpers.findAndHookMethod(
                    preferenceClass,
                    "onBindViewHolder",
                    holderClass,
                    new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
                            try {
                                BadgeRenderer.beforeBind(param.args[0]);
                            } catch (Throwable t) {
                                log("restore FAIL " + className + ": " + t);
                            }
                        }

                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            if (param.hasThrowable()) return;
                            try {
                                BadgeRenderer.apply(
                                        param.thisObject,
                                        param.args[0],
                                        accessPointPath
                                );
                            } catch (Throwable t) {
                                log("render FAIL " + className + ": " + t);
                            }
                        }
                    }
            );
            log("hooked " + className);
        } catch (Throwable t) {
            log("hook SKIP " + className + ": " + t);
        }
    }

    private static String shortLoader(ClassLoader loader) {
        if (loader == null) {
            return "null";
        }
        return loader.getClass().getSimpleName() + "@"
                + Integer.toHexString(System.identityHashCode(loader));
    }

    static void log(String message) {
        try {
            XposedBridge.log(TAG + " " + message);
        } catch (Throwable ignored) {
            // A logging failure must never affect Settings.
        }
    }
}
