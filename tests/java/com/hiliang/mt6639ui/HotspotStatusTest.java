package com.hiliang.mt6639ui;

public final class HotspotStatusTest {
    private static final String BOOT = "11111111-2222-3333-4444-555555555555";

    public static void main(String[] args) {
        expect("实验热点未启动", snapshot("idle", "none", "none", BOOT));
        expect("实验热点 active · 6 GHz · 80 MHz",
                snapshot("active", "6g", "80", BOOT));
        expect("实验热点 active · 5 GHz · 40 MHz",
                snapshot("active", "5g", "40", BOOT));
        expect("实验热点 active · 2.4 GHz · 20 MHz",
                snapshot("active", "2g", "20", BOOT));
        expect("实验热点正在停止", snapshot("stopping", "6g", "20", BOOT));
        expect("状态快照已过期：来自上一次启动",
                snapshot("active", "6g", "20", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"));
        expect("状态未决：活动快照缺少频段或带宽",
                snapshot("active", "none", "20", BOOT));
        expect("状态不可用：快照字段重复",
                "schema=mt6639-hotspot-status-v1\nstate=idle\nstate=idle\nwidth=none\nboot=" + BOOT + "\n");
        expect("状态不可用：快照取值无效",
                snapshot("active", "6g", "NaN", BOOT));
        expect("状态不可用：快照字段数量不符",
                snapshot("idle", "none", "none", BOOT) + "\n");
        HotspotStatus.Snapshot idle = HotspotStatus.parseSnapshot(
                snapshot("idle", "none", "none", BOOT), BOOT);
        if (!idle.canStart() || idle.canStop()) throw new AssertionError("idle actions");
        HotspotStatus.Snapshot active = HotspotStatus.parseSnapshot(
                snapshot("active", "6g", "20", BOOT), BOOT);
        if (active.canStart() || !active.canStop()) throw new AssertionError("active actions");
        HotspotStatus.Snapshot stale = HotspotStatus.parseSnapshot(
                snapshot("idle", "none", "none",
                        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"), BOOT);
        if (stale.canStart() || stale.canStop()) throw new AssertionError("stale actions");
        HotspotStatus.Snapshot ready = HotspotStatus.parseSnapshot(
                snapshotV2("idle", "1", "1", "1", "1"), BOOT);
        if (!ready.canStart() || ready.canStop()) throw new AssertionError("ready actions");
        if (!ready.canStart6g() || ready.canStart5g()) {
            throw new AssertionError("v2 band readiness");
        }
        HotspotStatus.Snapshot missingKey = HotspotStatus.parseSnapshot(
                snapshotV2("idle", "0", "1", "0", "1"), BOOT);
        if (missingKey.canStart() || missingKey.canStop() ||
                !missingKey.startHint().contains("ap.key")) {
            throw new AssertionError("missing key readiness");
        }
        HotspotStatus.Snapshot permissive = HotspotStatus.parseSnapshot(
                snapshotV2("idle", "1", "1", "0", "0"), BOOT);
        if (permissive.canStart() || !permissive.startHint().contains("Enforcing")) {
            throw new AssertionError("permissive readiness");
        }
        expect("状态不可用：快照取值无效",
                snapshotV2("idle", "yes", "1", "0", "1"));
        HotspotStatus.Snapshot dual = HotspotStatus.parseSnapshot(
                snapshotV3("1", "1", "1", "1", "1"), BOOT);
        if (!dual.canStart6g() || !dual.canStart5g() || !dual.canStart2g()) {
            throw new AssertionError("v3 dual readiness");
        }
        HotspotStatus.Snapshot fivegMissing = HotspotStatus.parseSnapshot(
                snapshotV3("1", "1", "1", "1", "0"), BOOT);
        if (!fivegMissing.canStart6g() || fivegMissing.canStart5g() ||
                !fivegMissing.canStart2g()) {
            throw new AssertionError("v3 5g upstream readiness");
        }
        HotspotStatus.Snapshot noTwoGKey = HotspotStatus.parseSnapshot(
                snapshotV3("1", "1", "0", "1", "1"), BOOT);
        if (noTwoGKey.canStart2g() || noTwoGKey.canStart5g() ||
                !noTwoGKey.start2gBlockReason.contains("LabNet-2.4G")) {
            throw new AssertionError("2.4g key readiness");
        }
        HotspotStatus.Snapshot legacy = HotspotStatus.parseSnapshot(
                snapshot("idle", "none", "none", BOOT), BOOT);
        if (legacy.canStart2g() || legacy.canStart5g() || !legacy.canStart6g()) {
            throw new AssertionError("v1 snapshot keeps only 6g readiness");
        }
        expect("状态不可用：快照取值无效",
                snapshotV3("1", "1", "1", "1", "yes"));
        // v4 multi-device + pure Wi-Fi tablet checks
        HotspotStatus.Snapshot tabletLocal = HotspotStatus.parseSnapshot(
                snapshotV4("1", "0", "0", "1", "1", "1", "1", "1"), BOOT);
        if (!tabletLocal.canStart6g() || !tabletLocal.canStart5g() || !tabletLocal.canStart2g()
                || !tabletLocal.canStartLocal() || tabletLocal.canRunC02()) {
            throw new AssertionError("v4 pure tablet readiness without reconnect keys");
        }
        HotspotStatus.Snapshot sta6g = HotspotStatus.parseSnapshot(
                snapshotV4("1", "1", "0", "1", "1", "0", "1", "0"), BOOT);
        if (!sta6g.canStart6g() || sta6g.canStart5g() || !sta6g.canStart2g()
                || sta6g.canStartLocal() || !sta6g.canRunC02()) {
            throw new AssertionError("v4 6g STA concurrency");
        }
        HotspotStatus.Snapshot sta2g = HotspotStatus.parseSnapshot(
                snapshotV4("1", "0", "0", "1", "0", "1", "0", "0"), BOOT);
        if (sta2g.canStart6g() || !sta2g.canStart5g() || sta2g.canStart2g()
                || sta2g.canStartLocal()) {
            throw new AssertionError("v4 2.4g STA concurrency");
        }
        expect("状态不可用：快照取值无效",
                snapshotV4("1", "0", "0", "1", "1", "1", "1", "bad"));
        for (String dev : new String[] {"dijun", "violin", "jinghu"}) {
            for (int sdk : new int[] {35, 36, 37}) {
                if (!DeviceGate.isSupported(dev, sdk)) {
                    throw new AssertionError("expected supported: " + dev + "/" + sdk);
                }
            }
            if (DeviceGate.isSupported(dev, 34) || DeviceGate.isSupported(dev, 38)) {
                throw new AssertionError("unexpected sdk accepted for " + dev);
            }
        }
        if (DeviceGate.isSupported("other", 37) || DeviceGate.isSupported(null, 37)) {
            throw new AssertionError("unknown device accepted");
        }
        System.out.println("RESULT=PASS");
    }

    private static String snapshotV4(
            String apKey, String reconnect6g, String reconnect2g, String enforcing,
            String ready6g, String ready5g, String ready2g, String localReady
    ) {
        return "schema=mt6639-hotspot-status-v4\n" +
                "state=idle\n" +
                "band=none\n" +
                "width=none\n" +
                "boot=" + BOOT + "\n" +
                "ap_key=" + apKey + "\n" +
                "reconnect_6g=" + reconnect6g + "\n" +
                "reconnect_2g=" + reconnect2g + "\n" +
                "enforcing=" + enforcing + "\n" +
                "ready_6g=" + ready6g + "\n" +
                "ready_5g=" + ready5g + "\n" +
                "ready_2g=" + ready2g + "\n" +
                "local_ready=" + localReady + "\n";
    }

    private static String snapshot(String state, String band, String width, String boot) {
        return "schema=mt6639-hotspot-status-v1\n" +
                "state=" + state + "\n" +
                "band=" + band + "\n" +
                "width=" + width + "\n" +
                "boot=" + boot + "\n";
    }

    private static String snapshotV2(
            String state, String apKey, String reconnect6g, String reconnect2g,
            String enforcing
    ) {
        return "schema=mt6639-hotspot-status-v2\n" +
                "state=" + state + "\n" +
                "band=none\n" +
                "width=none\n" +
                "boot=" + BOOT + "\n" +
                "ap_key=" + apKey + "\n" +
                "reconnect_6g=" + reconnect6g + "\n" +
                "reconnect_2g=" + reconnect2g + "\n" +
                "enforcing=" + enforcing + "\n";
    }

    private static String snapshotV3(
            String apKey, String reconnect6g, String reconnect2g,
            String enforcing, String fivegUpstream
    ) {
        return "schema=mt6639-hotspot-status-v3\n" +
                "state=idle\n" +
                "band=none\n" +
                "width=none\n" +
                "boot=" + BOOT + "\n" +
                "ap_key=" + apKey + "\n" +
                "reconnect_6g=" + reconnect6g + "\n" +
                "reconnect_2g=" + reconnect2g + "\n" +
                "enforcing=" + enforcing + "\n" +
                "fiveg_upstream=" + fivegUpstream + "\n";
    }

    private static void expect(String expected, String text) {
        String actual = HotspotStatus.parseSummary(text, BOOT);
        if (!expected.equals(actual)) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }
}
