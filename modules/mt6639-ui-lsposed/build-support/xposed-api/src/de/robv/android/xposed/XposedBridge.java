package de.robv.android.xposed;

import java.lang.reflect.Member;

public final class XposedBridge {
    private XposedBridge() {
    }

    public static void log(String message) {
    }

    public static void log(Throwable throwable) {
    }

    public static XC_MethodHook.Unhook hookMethod(
            Member hookMethod,
            XC_MethodHook callback
    ) {
        throw new UnsupportedOperationException("compile-only stub");
    }
}
