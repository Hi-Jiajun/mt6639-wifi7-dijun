package com.hiliang.mt6639ui;

import java.util.regex.Pattern;

/** Pure request payload builder shared by the Settings bridge and host tests. */
final class HotspotRequestPayload {
    private static final Pattern BOOT = Pattern.compile(
            "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
    private static final Pattern NONCE = Pattern.compile("^[0-9a-f]{32}$");

    private HotspotRequestPayload() {
    }

    static final class Profile {
        final String operation;
        final String band;
        final String width;

        Profile(String operation, String band, String width) {
            this.operation = operation;
            this.band = band;
            this.width = width;
        }
    }

    static Profile profile(String action) {
        if (action == null) throw new IllegalArgumentException("missing action");
        if ("stop".equals(action)) return new Profile("stop", "6g", "20");
        if ("c02-24g".equals(action)) return new Profile("c02-24g", "6g", "20");
        for (String width : new String[] {"20", "40", "80", "160", "320"}) {
            if (("start-6g-" + width).equals(action)) {
                return new Profile("start", "6g", width);
            }
        }
        for (String width : new String[] {"20", "40", "80"}) {
            if (("start-5g-" + width).equals(action)) {
                return new Profile("start", "5g", width);
            }
        }
        if ("start-2g-20".equals(action)) {
            return new Profile("start", "2g", "20");
        }
        throw new IllegalArgumentException("unsupported action");
    }

    static String build(String action, String bootId, String nonce) {
        Profile profile = profile(action);
        if (bootId == null || !BOOT.matcher(bootId).matches()) {
            throw new IllegalArgumentException("invalid boot id");
        }
        if (nonce == null || !NONCE.matcher(nonce).matches()) {
            throw new IllegalArgumentException("invalid nonce");
        }
        return "schema=mt6639-hotspot-request-v1\n" +
                "op=" + profile.operation + "\n" +
                "band=" + profile.band + "\n" +
                "width=" + profile.width + "\n" +
                "boot=" + bootId + "\n" +
                "nonce=" + nonce + "\n";
    }
}
