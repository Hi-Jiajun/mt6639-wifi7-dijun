package com.hiliang.mt6639ui;

final class UiPolicy {
    static final String KEY = "mt6639_ui_badges_enabled";
    static boolean enabled(String value) { return value == null || "1".equals(value); }
    private UiPolicy() { }
}
