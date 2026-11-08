package com.hiliang.mt6639concurrency;

import android.os.SystemClock;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Pattern;

/** Strict reader for a root-owned, short-lived dual-STA test lease. */
final class DualStaGate {
    static final String FILE = "/data/misc/mt6639-concurrency/gate";
    private static final String SCHEMA = "mt6639-dualsta-gate-v1";
    private static final long MAX_FILE_BYTES = 1024;
    private static final long MAX_REMAINING_MS = 120_000;
    private static final Pattern LEASE = Pattern.compile("[0-9a-f]{16}");

    private DualStaGate() {
    }

    static boolean active() {
        try {
            String boot = readText(new File("/proc/sys/kernel/random/boot_id"), 256).trim();
            File gate = new File(FILE);
            if (Files.isSymbolicLink(gate.toPath()) || !gate.isFile()
                    || !FILE.equals(gate.getCanonicalPath())) return false;
            String marker = readText(gate, MAX_FILE_BYTES);
            return parse(marker, SystemClock.elapsedRealtime(), boot);
        } catch (Throwable ignored) {
            return false;
        }
    }

    static boolean parse(String marker, long nowMs, String currentBoot) {
        Map<String, String> fields = new HashMap<>();
        String[] lines = marker.split("\\n", -1);
        if (lines.length != 5 || !lines[4].isEmpty()) return false;
        for (int i = 0; i < 4; i++) {
            int split = lines[i].indexOf('=');
            if (split <= 0 || split != lines[i].lastIndexOf('=') || split == lines[i].length() - 1)
                return false;
            if (fields.put(lines[i].substring(0, split), lines[i].substring(split + 1)) != null)
                return false;
        }
        if (fields.size() != 4 || !SCHEMA.equals(fields.get("schema"))
                || !currentBoot.equals(fields.get("boot"))
                || !LEASE.matcher(fields.getOrDefault("lease", "")).matches())
            return false;
        long expires;
        try {
            expires = Long.parseLong(fields.get("expires_ms"));
        } catch (NumberFormatException error) {
            return false;
        }
        long remaining = expires - nowMs;
        return remaining > 0 && remaining <= MAX_REMAINING_MS;
    }

    private static String readText(File file, long limit) throws IOException {
        if (file.length() > limit) throw new IOException("oversized");
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[256];
            long total = 0;
            int count;
            while ((count = input.read(buffer)) != -1) {
                total += count;
                if (total > limit) throw new IOException("oversized");
                output.write(buffer, 0, count);
            }
            return new String(output.toByteArray(), StandardCharsets.US_ASCII);
        }
    }
}
