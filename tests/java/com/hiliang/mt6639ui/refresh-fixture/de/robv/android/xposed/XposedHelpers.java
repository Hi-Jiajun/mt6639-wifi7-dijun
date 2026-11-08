package de.robv.android.xposed;

import com.hiliang.mt6639ui.HotspotRefreshTest;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;
import java.util.ArrayList;
import java.util.List;

/** Only the Xposed reflection/hook boundary is simulated. */
public final class XposedHelpers {
    private static final Map<String, List<XC_MethodHook>> HOOKS = new HashMap<>();
    public static Class<?> findClass(String name, ClassLoader loader) {
        if (name.equals("androidx.preference.Preference$OnPreferenceClickListener")) {
            return HotspotRefreshTest.ClickListener.class;
        }
        if (name.equals("com.android.settings.MiuiTetherSettings")) {
            return HotspotRefreshTest.MiuiFragment.class;
        }
        if (name.equals("com.android.settings.AllInOneTetherSettings")) {
            return HotspotRefreshTest.AllInOneFragment.class;
        }
        throw new AssertionError("unexpected class " + name);
    }
    public static XC_MethodHook.Unhook findAndHookMethod(
            Class<?> clazz, String method, Object... args) {
        String key = clazz.getName() + ":" + method;
        HOOKS.computeIfAbsent(key, ignored -> new ArrayList<>())
                .add((XC_MethodHook) args[args.length - 1]);
        return null;
    }
    public static Object callMethod(Object target, String name, Object... args) {
        try {
            for (Method method : target.getClass().getMethods()) {
                if (method.getName().equals(name) && method.getParameterCount() == args.length) {
                    return method.invoke(target, args);
                }
            }
            throw new NoSuchMethodException(name);
        } catch (ReflectiveOperationException error) {
            throw new AssertionError("reflection " + name, error);
        }
    }
    public static void fireAfter(Object fragment, String method) throws Throwable {
        XC_MethodHook.MethodHookParam param = new XC_MethodHook.MethodHookParam();
        param.thisObject = fragment;
        param.args = new Object[0];
        for (Class<?> type = fragment.getClass(); type != null; type = type.getSuperclass()) {
            List<XC_MethodHook> hooks = HOOKS.get(type.getName() + ":" + method);
            // An absent hook behaves as the unmodified fragment lifecycle.
            if (hooks == null) continue;
            for (XC_MethodHook hook : hooks) hook.beforeHookedMethod(param);
            for (XC_MethodHook hook : hooks) hook.afterHookedMethod(param);
        }
    }
}
