package com.hiliang.mt6639concurrency;

public final class DualStaGateTest {
    private static final String BOOT = "11111111-2222-3333-4444-555555555555";

    public static void main(String[] args) {
        expect(true, marker(BOOT, 130_000, "0123456789abcdef"), 100_000, BOOT);
        expect(false, marker(BOOT, 100_000, "0123456789abcdef"), 100_000, BOOT);
        expect(false, marker(BOOT, 230_001, "0123456789abcdef"), 100_000, BOOT);
        expect(false, marker("other", 130_000, "0123456789abcdef"), 100_000, BOOT);
        expect(false, marker(BOOT, 130_000, "xyz"), 100_000, BOOT);
        expect(false, marker(BOOT, 130_000, "0123456789abcdef") + "\n", 100_000, BOOT);
        System.out.println("RESULT=PASS");
    }

    private static String marker(String boot, long expires, String lease) {
        return "schema=mt6639-dualsta-gate-v1\n" +
                "boot=" + boot + "\n" +
                "expires_ms=" + expires + "\n" +
                "lease=" + lease + "\n";
    }

    private static void expect(boolean expected, String marker, long now, String boot) {
        boolean actual = DualStaGate.parse(marker, now, boot);
        if (actual != expected) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }
}
