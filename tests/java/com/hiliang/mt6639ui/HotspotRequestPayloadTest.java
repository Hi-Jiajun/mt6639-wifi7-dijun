package com.hiliang.mt6639ui;

public final class HotspotRequestPayloadTest {
    private static final String BOOT = "11111111-2222-3333-4444-555555555555";
    private static final String NONCE = "0123456789abcdef0123456789abcdef";

    public static void main(String[] args) {
        String start20 = HotspotRequestPayload.build("start-6g-20", BOOT, NONCE);
        expect(start20,
                "schema=mt6639-hotspot-request-v1\n" +
                "op=start\n" +
                "band=6g\n" +
                "width=20\n" +
                "boot=" + BOOT + "\n" +
                "nonce=" + NONCE + "\n");
        for (String width : new String[] {"40", "80", "160", "320"}) {
            String payload = HotspotRequestPayload.build("start-6g-" + width, BOOT, NONCE);
            if (!payload.contains("\nwidth=" + width + "\n")) {
                throw new AssertionError(payload);
            }
        }
        for (String width : new String[] {"20", "40", "80"}) {
            String payload = HotspotRequestPayload.build("start-5g-" + width, BOOT, NONCE);
            if (!payload.contains("\nband=5g\n") ||
                    !payload.contains("\nwidth=" + width + "\n")) {
                throw new AssertionError(payload);
            }
        }
        String twoG = HotspotRequestPayload.build("start-2g-20", BOOT, NONCE);
        if (!twoG.contains("\nop=start\n") || !twoG.contains("\nband=2g\n") ||
                !twoG.contains("\nwidth=20\n")) {
            throw new AssertionError(twoG);
        }
        String local6g = HotspotRequestPayload.build("start-local-6g-320", BOOT, NONCE);
        if (!local6g.contains("\nop=start-local\n") || !local6g.contains("\nband=6g\n") ||
                !local6g.contains("\nwidth=320\n")) {
            throw new AssertionError(local6g);
        }
        String local5g = HotspotRequestPayload.build("start-local-5g-80", BOOT, NONCE);
        if (!local5g.contains("\nop=start-local\n") || !local5g.contains("\nband=5g\n") ||
                !local5g.contains("\nwidth=80\n")) {
            throw new AssertionError(local5g);
        }
        String local2g = HotspotRequestPayload.build("start-local-2g-20", BOOT, NONCE);
        if (!local2g.contains("\nop=start-local\n") || !local2g.contains("\nband=2g\n") ||
                !local2g.contains("\nwidth=20\n")) {
            throw new AssertionError(local2g);
        }
        String stop = HotspotRequestPayload.build("stop", BOOT, NONCE);
        if (!stop.contains("\nop=stop\n")) throw new AssertionError(stop);
        String c02 = HotspotRequestPayload.build("c02-24g", BOOT, NONCE);
        if (!c02.contains("\nop=c02-24g\n")) throw new AssertionError(c02);
        expectRejected("toggle", BOOT, NONCE);
        expectRejected("start-6g-6", BOOT, NONCE);
        expectRejected("start-5g-160", BOOT, NONCE);
        expectRejected("start-2g-40", BOOT, NONCE);
        expectRejected("start-local-5g-160", BOOT, NONCE);
        expectRejected("start-local-2g-40", BOOT, NONCE);
        expectRejected("c02-5g", BOOT, NONCE);
        expectRejected("start-6g-20", "bad", NONCE);
        expectRejected("start-6g-20", BOOT, "ABCDEF");
        System.out.println("RESULT=PASS");
    }

    private static void expect(String actual, String expected) {
        if (!expected.equals(actual)) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }

    private static void expectRejected(String operation, String boot, String nonce) {
        try {
            HotspotRequestPayload.build(operation, boot, nonce);
            throw new AssertionError("request unexpectedly accepted");
        } catch (IllegalArgumentException expected) {
            // Expected fail-closed behavior.
        }
    }
}
