package com.hiliang.mt6639ui;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** Strict, read-only parser for the root hotspot module's public status snapshot. */
final class HotspotStatus {
    static final String SNAPSHOT_PATH = "/data/misc/mt6639-hotspot/status";
    private static final String SCHEMA_V1 = "mt6639-hotspot-status-v1";
    private static final String SCHEMA_V2 = "mt6639-hotspot-status-v2";
    private static final String SCHEMA_V3 = "mt6639-hotspot-status-v3";
    private static final long MAX_SNAPSHOT_BYTES = 1024;
    private static final Set<String> STATES = immutableSet(
            "idle", "starting", "active", "stopping", "unresolved", "disabled");
    private static final Set<String> BANDS = immutableSet("none", "2g", "5g", "6g");
    private static final Set<String> WIDTHS = immutableSet("none", "20", "40", "80", "160", "320");
    private static final Set<String> KEYS_V1 =
            immutableSet("schema", "state", "band", "width", "boot");
    private static final Set<String> KEYS_V2 =
            immutableSet(
                    "schema", "state", "band", "width", "boot",
                    "ap_key", "reconnect_6g", "reconnect_2g", "enforcing"
            );
    private static final Set<String> KEYS_V3 =
            immutableSet(
                    "schema", "state", "band", "width", "boot",
                    "ap_key", "reconnect_6g", "reconnect_2g", "enforcing",
                    "fiveg_upstream"
            );

    private HotspotStatus() {
    }

    static final class Snapshot {
        final String state;
        final String summary;
        final boolean available;
        final boolean start6gReady;
        final boolean start5gReady;
        final boolean start2gReady;
        final String start6gBlockReason;
        final String start5gBlockReason;
        final String start2gBlockReason;

        Snapshot(
                String state,
                String summary,
                boolean available,
                boolean start6gReady,
                boolean start5gReady,
                boolean start2gReady,
                String start6gBlockReason,
                String start5gBlockReason,
                String start2gBlockReason
        ) {
            this.state = state;
            this.summary = summary;
            this.available = available;
            this.start6gReady = start6gReady;
            this.start5gReady = start5gReady;
            this.start2gReady = start2gReady;
            this.start6gBlockReason = start6gBlockReason;
            this.start5gBlockReason = start5gBlockReason;
            this.start2gBlockReason = start2gBlockReason;
        }

        boolean canStart() {
            return canStart6g() || canStart5g() || canStart2g();
        }

        boolean canStart6g() {
            return available && isIdle() && start6gReady;
        }

        boolean canStart5g() {
            return available && isIdle() && start5gReady;
        }

        boolean canStart2g() {
            return available && isIdle() && start2gReady;
        }

        boolean canStop() {
            return available && (
                    "active".equals(state) ||
                    "starting".equals(state) ||
                    "unresolved".equals(state)
            );
        }

        boolean isIdle() {
            return available && "idle".equals(state);
        }

        String startHint() {
            if (!isIdle()) return summary;
            if (start6gReady && start5gReady && start2gReady) {
                return "选择 6 GHz 20/40/80/160/320MHz、5 GHz 20/40/80MHz 或 2.4 GHz 20MHz";
            }
            if (start6gReady) return "选择 6 GHz 带宽后启动固定参数热点";
            if (start5gReady) return "选择 5 GHz 带宽后启动固定参数热点";
            if (start2gReady) return "选择 2.4 GHz 带宽后启动固定参数热点";
            return "不可启动：6G[" + start6gBlockReason
                    + "]；5G[" + start5gBlockReason
                    + "]；2.4G[" + start2gBlockReason + "]";
        }

        static Snapshot unavailable(String summary) {
            return new Snapshot(
                    "unavailable", summary, false,
                    false, false, false,
                    summary, summary, summary);
        }
    }

    static String readSummary() {
        return readSnapshot().summary;
    }

    static Snapshot readSnapshot() {
        File currentBootFile = new File("/proc/sys/kernel/random/boot_id");
        String currentBoot;
        try {
            currentBoot = new String(readBounded(currentBootFile, 256), StandardCharsets.US_ASCII).trim();
        } catch (IOException error) {
            return Snapshot.unavailable("状态不可用：无法确认当前启动代际");
        }
        File snapshot = new File(SNAPSHOT_PATH);
        if (!snapshot.exists()) {
            return Snapshot.unavailable("状态不可用：实验热点模块尚未提供快照");
        }
        try {
            String canonical = snapshot.getCanonicalPath();
            if (!SNAPSHOT_PATH.equals(canonical) || !snapshot.isFile()) {
                return Snapshot.unavailable("状态不可用：快照路径不安全");
            }
            String text = new String(readBounded(snapshot, MAX_SNAPSHOT_BYTES), StandardCharsets.US_ASCII);
            return parseSnapshot(text, currentBoot);
        } catch (IOException error) {
            return Snapshot.unavailable("状态不可用：无法读取快照");
        }
    }

    static String parseSummary(String text, String currentBoot) {
        return parseSnapshot(text, currentBoot).summary;
    }

    static Snapshot parseSnapshot(String text, String currentBoot) {
        Map<String, String> fields = new HashMap<>();
        String[] lines = text.split("\\n", -1);
        // A well-formed snapshot ends in exactly one newline, which split retains as an empty tail.
        if (lines.length < 2 || !lines[lines.length - 1].isEmpty()) {
            return Snapshot.unavailable("状态不可用：快照字段数量不符");
        }
        int fieldCount = lines.length - 1;
        if (fieldCount != 5 && fieldCount != 9 && fieldCount != 10) {
            return Snapshot.unavailable("状态不可用：快照字段数量不符");
        }
        for (int index = 0; index < fieldCount; index++) {
            String line = lines[index];
            int separator = line.indexOf('=');
            if (separator <= 0 || separator != line.lastIndexOf('=') ||
                    separator == line.length() - 1) {
                return Snapshot.unavailable("状态不可用：快照格式无效");
            }
            String key = line.substring(0, separator);
            String value = line.substring(separator + 1);
            if (fields.put(key, value) != null) {
                return Snapshot.unavailable("状态不可用：快照字段重复");
            }
        }
        String schema = fields.get("schema");
        boolean version2 = SCHEMA_V2.equals(schema);
        boolean version3 = SCHEMA_V3.equals(schema);
        Set<String> expectedKeys = version3 ? KEYS_V3 :
                (version2 ? KEYS_V2 : KEYS_V1);
        if (!(SCHEMA_V1.equals(schema) || version2 || version3) ||
                !expectedKeys.equals(fields.keySet())) {
            return Snapshot.unavailable("状态不可用：快照字段不完整");
        }

        String state = fields.get("state");
        String band = fields.get("band");
        String width = fields.get("width");
        if (!STATES.contains(state) ||
                !BANDS.contains(band) || !WIDTHS.contains(width)) {
            return Snapshot.unavailable("状态不可用：快照取值无效");
        }
        if (!currentBoot.equals(fields.get("boot"))) {
            return Snapshot.unavailable("状态快照已过期：来自上一次启动");
        }
        if (version2 || version3) {
            for (String key : new String[] {
                    "ap_key", "reconnect_6g", "reconnect_2g", "enforcing"
            }) {
                String value = fields.get(key);
                if (!"0".equals(value) && !"1".equals(value)) {
                    return Snapshot.unavailable("状态不可用：快照取值无效");
                }
            }
        }
        if (version3) {
            String value = fields.get("fiveg_upstream");
            if (!"0".equals(value) && !"1".equals(value)) {
                return Snapshot.unavailable("状态不可用：快照取值无效");
            }
        }
        if ("active".equals(state)) {
            if ("none".equals(band) || "none".equals(width)) {
                return new Snapshot(
                        "unresolved", "状态未决：活动快照缺少频段或带宽",
                        true, true, false, false,
                        "", "当前并非 idle", "当前并非 idle");
            }
            String bandLabel = "2g".equals(band) ? "2.4 GHz" :
                    ("6g".equals(band) ? "6 GHz" : "5 GHz");
            return new Snapshot(
                    "active",
                    "实验热点 active · " + bandLabel + " · " + width + " MHz",
                    true,
                    true,
                    false,
                    false,
                    "",
                    "当前并非 idle",
                    "当前并非 idle"
            );
        }
        boolean start6gReady = true;
        boolean start5gReady = false;
        boolean start2gReady = false;
        StringBuilder missing6g = new StringBuilder();
        StringBuilder missing5g = new StringBuilder("旧版快照未提供 5G readiness");
        StringBuilder missing2g = new StringBuilder("旧版快照未提供 2.4G readiness");
        if (version2 || version3) {
            boolean apKey = "1".equals(fields.get("ap_key"));
            boolean reconnect6g = "1".equals(fields.get("reconnect_6g"));
            boolean reconnect2g = "1".equals(fields.get("reconnect_2g"));
            boolean enforcing = "1".equals(fields.get("enforcing"));
            // 5GHz additionally needs the STA on LabNet-2.4G; 2.4GHz only needs
            // the upstream key staged, because it runs on wifi-any or cellular.
            boolean upstream5g = version3 && "1".equals(fields.get("fiveg_upstream"));

            missing5g.setLength(0);
            missing2g.setLength(0);
            if (!apKey) {
                appendReason(missing6g, "缺少热点口令 ap.key");
                appendReason(missing5g, "缺少热点口令 ap.key");
                appendReason(missing2g, "缺少热点口令 ap.key");
            }
            if (!reconnect6g) {
                appendReason(missing6g, "缺少 LabNet-6G 上游口令");
            }
            if (!reconnect2g) {
                appendReason(missing5g, "缺少 LabNet-2.4G 上游口令");
                appendReason(missing2g, "缺少 LabNet-2.4G 上游口令");
            }
            if (!enforcing) {
                appendReason(missing6g, "SELinux 未处于 Enforcing");
                appendReason(missing5g, "SELinux 未处于 Enforcing");
                appendReason(missing2g, "SELinux 未处于 Enforcing");
            }
            if (!upstream5g) {
                appendReason(missing5g, "当前未连接 LabNet-2.4G/2412MHz");
            }
            start6gReady = missing6g.length() == 0;
            start5gReady = missing5g.length() == 0;
            start2gReady = missing2g.length() == 0;
        }
        if ("idle".equals(state)) {
            return new Snapshot(
                    "idle", "实验热点未启动", true,
                    start6gReady, start5gReady, start2gReady,
                    missing6g.toString(), missing5g.toString(), missing2g.toString());
        }
        if ("starting".equals(state)) {
            return new Snapshot(
                    "starting", "实验热点正在启动", true,
                    start6gReady, start5gReady, start2gReady,
                    missing6g.toString(), missing5g.toString(), missing2g.toString());
        }
        if ("stopping".equals(state)) {
            return new Snapshot(
                    "stopping", "实验热点正在停止", true,
                    start6gReady, start5gReady, start2gReady,
                    missing6g.toString(), missing5g.toString(), missing2g.toString());
        }
        if ("disabled".equals(state)) {
            return new Snapshot(
                    "disabled", "实验热点模块已禁用", true,
                    false, false, false,
                    "实验热点模块已禁用", "实验热点模块已禁用", "实验热点模块已禁用");
        }
        return new Snapshot(
                "unresolved", "实验热点状态未决，需要检查恢复",
                true, start6gReady, start5gReady, start2gReady,
                missing6g.toString(), missing5g.toString(), missing2g.toString());
    }

    private static void appendReason(StringBuilder target, String reason) {
        if (target.length() > 0) target.append("、");
        target.append(reason);
    }

    private static Set<String> immutableSet(String... values) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(values)));
    }

    private static byte[] readBounded(File file, long limit) throws IOException {
        if (file.isDirectory() || file.length() > limit) {
            throw new IOException("unsafe size");
        }
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[256];
            long total = 0;
            int count;
            while ((count = input.read(buffer)) != -1) {
                total += count;
                if (total > limit) throw new IOException("oversized snapshot");
                output.write(buffer, 0, count);
            }
            return output.toByteArray();
        }
    }
}
