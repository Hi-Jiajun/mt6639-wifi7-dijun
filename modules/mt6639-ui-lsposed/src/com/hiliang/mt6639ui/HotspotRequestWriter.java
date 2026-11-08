package com.hiliang.mt6639ui;

import android.content.Context;
import android.system.Os;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;

/** Writes only a fixed hotspot request into Settings' app-private queue. */
final class HotspotRequestWriter {
    private static final String REQUEST_SUBDIR = "mt6639-hotspot";
    private static final SecureRandom RANDOM = new SecureRandom();

    private HotspotRequestWriter() {
    }

    static final class Result {
        final boolean submitted;
        final String message;

        Result(boolean submitted, String message) {
            this.submitted = submitted;
            this.message = message;
        }

        static Result failed(String message) { return new Result(false, message); }
        static Result submitted(String message) { return new Result(true, message); }
    }

    static Result submit(Context context, String action) {
        HotspotRequestPayload.Profile profile;
        try {
            profile = HotspotRequestPayload.profile(action);
        } catch (IllegalArgumentException error) {
            return Result.failed("请求不可用：操作类型无效");
        }
        String boot = readBootId();
        if (boot == null) return Result.failed("请求不可用：无法确认当前启动代际");
        File directory;
        try {
            File files = context.getFilesDir();
            if (files == null || !files.isDirectory()) {
                return Result.failed("请求不可用：Settings 私有目录不可用");
            }
            directory = new File(files, REQUEST_SUBDIR);
            if (!directory.isDirectory() && !directory.mkdirs()) {
                return Result.failed("请求不可用：无法建立控制队列");
            }
            String canonicalFiles = files.getCanonicalPath();
            String canonicalDirectory = directory.getCanonicalPath();
            if (!directory.isDirectory() ||
                    !canonicalDirectory.startsWith(canonicalFiles + "/")) {
                return Result.failed("请求不可用：控制队列路径无效");
            }
            Os.chmod(directory.getAbsolutePath(), 0700);
        } catch (IOException error) {
            return Result.failed("请求不可用：无法确认控制队列");
        } catch (Exception error) {
            return Result.failed("请求不可用：控制队列权限无效");
        }

        String nonce = nextNonce();
        String payload;
        try {
            payload = HotspotRequestPayload.build(action, boot, nonce);
        } catch (IllegalArgumentException error) {
            return Result.failed("请求不可用：请求字段无效");
        }
        File temporary = new File(directory, "." + nonce + ".tmp");
        File request = new File(directory, "request." + nonce + ".req");
        try {
            try (FileOutputStream output = new FileOutputStream(temporary, false)) {
                output.write(payload.getBytes(StandardCharsets.US_ASCII));
                output.flush();
                output.getFD().sync();
            }
            Os.chmod(temporary.getAbsolutePath(), 0600);
            if (!temporary.renameTo(request)) {
                throw new IOException("rename failed");
            }
        } catch (Exception error) {
            temporary.delete();
            return Result.failed("请求提交失败：控制队列不可写");
        }
        if ("start".equals(profile.operation)) {
            String bandLabel = "5g".equals(profile.band) ? "5 GHz"
                    : ("2g".equals(profile.band) ? "2.4 GHz" : "6 GHz");
            return Result.submitted("启动请求已提交：" + bandLabel
                    + " / " + profile.width
                    + " MHz，等待模块确认");
        }
        if ("c02-24g".equals(profile.operation)) {
            return Result.submitted("C02 验收请求已提交：LabNet-6G STA + 2.4GHz 11ax AP");
        }
        return Result.submitted("停止请求已提交，等待模块恢复");
    }

    private static String readBootId() {
        File file = new File("/proc/sys/kernel/random/boot_id");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] value = new byte[64];
            int count = input.read(value);
            if (count <= 0 || count == value.length) return null;
            String boot = new String(value, 0, count, StandardCharsets.US_ASCII).trim();
            HotspotRequestPayload.build("stop", boot, "00000000000000000000000000000000");
            return boot;
        } catch (IOException | IllegalArgumentException error) {
            return null;
        }
    }

    private static String nextNonce() {
        byte[] value = new byte[16];
        RANDOM.nextBytes(value);
        StringBuilder text = new StringBuilder(32);
        for (byte item : value) {
            text.append(String.format("%02x", item & 0xff));
        }
        return text.toString();
    }
}
