#!/system/bin/sh
# KernelSU starts service.sh and action.sh with almost no environment (only
# PATH is present), but every app_process call in this module needs the Android
# runtime environment: without ANDROID_DATA / BOOTCLASSPATH app_process exits
# with no output at all, which used to surface as
# "runner_exited_during_start reason=unreported" with an empty framework.before.
#
# Values are taken from system_server's environment when possible, so an OS
# update that changes BOOTCLASSPATH does not silently break this module. Nothing
# here overrides a value the caller already provided.

android_sysenv() {
    key=$1
    for pid in $(pidof system_server 2>/dev/null); do
        value=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null |
            sed -n "s/^$key=//p" | head -n 1)
        if [ -n "${value:-}" ]; then
            printf '%s\n' "$value"
            return 0
        fi
    done
    return 1
}

android_env_export() {
    name=$1
    fallback=$2
    eval "current=\${$name:-}"
    if [ -z "$current" ]; then
        current=$(android_sysenv "$name" 2>/dev/null) || current=$fallback
        [ -n "$current" ] || return 1
        eval "$name=\$current"
        export "$name"
    fi
    return 0
}

# A soft reboot restarts zygote and system_server while long-lived KernelSU
# services (service.sh and the Settings worker) keep their old environment.
# Reusing that stale BOOTCLASSPATH makes app_process abort inside
# artInitializeStaticStorageFromCode, which used to look like a silent
# "reason=unreported". Refresh the derived values whenever the recorded
# system_server pid is no longer the current one.
android_env_server=$(pidof system_server 2>/dev/null | awk '{print $1}')
if [ -n "${android_env_server:-}" ] &&
    [ "${MT6639_RUNTIME_SERVER:-}" != "$android_env_server" ]; then
    unset BOOTCLASSPATH DEX2OATBOOTCLASSPATH ANDROID_DATA ANDROID_ROOT \
        ANDROID_ART_ROOT ANDROID_I18N_ROOT ANDROID_TZDATA_ROOT EXTERNAL_STORAGE
    MT6639_RUNTIME_SERVER=$android_env_server
    export MT6639_RUNTIME_SERVER
fi

android_env_export ANDROID_DATA /data || true
android_env_export ANDROID_ROOT /system || true
android_env_export ANDROID_ART_ROOT /apex/com.android.art || true
android_env_export ANDROID_I18N_ROOT /apex/com.android.i18n || true
android_env_export ANDROID_TZDATA_ROOT /apex/com.android.tzdata || true
android_env_export EXTERNAL_STORAGE /sdcard || true
android_env_export BOOTCLASSPATH '' || true
android_env_export DEX2OATBOOTCLASSPATH "${BOOTCLASSPATH:-}" || true

# app_process needs a boot classpath; fail loudly instead of letting the caller
# produce empty output.
android_env_ready() {
    [ -n "${BOOTCLASSPATH:-}" ] && [ -n "${ANDROID_DATA:-}" ]
}
