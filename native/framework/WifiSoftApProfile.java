import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;

/** Preserve the complete saved configuration; apply recoverable 2.4/5GHz SoftAP profiles. */
public final class WifiSoftApProfile {
    private static Object call(Object target, String name, Class<?>[] types, Object... args)
            throws Exception {
        Method method = target.getClass().getMethod(name, types);
        method.setAccessible(true);
        return method.invoke(target, args);
    }

    private static byte[] encode(Object config) throws Exception {
        Class<?> pt = Class.forName("android.os.Parcel");
        Object p = pt.getMethod("obtain").invoke(null);
        try {
            call(config, "writeToParcel", new Class<?>[]{pt, int.class}, p, 0);
            return (byte[]) call(p, "marshall", new Class<?>[0]);
        } finally { call(p, "recycle", new Class<?>[0]); }
    }

    private static Object decode(File file) throws Exception {
        byte[] data = Files.readAllBytes(file.toPath());
        if (data.length == 0 || data.length > 65536)
            throw new IllegalStateException("invalid_softap_parcel");
        Class<?> pt = Class.forName("android.os.Parcel");
        Object p = pt.getMethod("obtain").invoke(null);
        try {
            call(p, "unmarshall", new Class<?>[]{byte[].class, int.class, int.class}, data, 0, data.length);
            call(p, "setDataPosition", new Class<?>[]{int.class}, 0);
            Object creator = Class.forName("android.net.wifi.SoftApConfiguration").getField("CREATOR").get(null);
            return call(creator, "createFromParcel", new Class<?>[]{pt}, p);
        } finally { call(p, "recycle", new Class<?>[0]); }
    }

    private static void save(File file, Object value) throws Exception {
        if (!file.createNewFile()) throw new IllegalStateException("backup_exists");
        try (FileOutputStream stream = new FileOutputStream(file)) {
            stream.write(encode(value)); stream.getFD().sync();
        }
        if (!value.equals(decode(file))) throw new IllegalStateException("parcel_roundtrip_mismatch");
    }

    private static void summary(Object wifi, Object config) throws Exception {
        System.out.println("ap_state=" + call(wifi, "getWifiApEnabledState", new Class<?>[0]));
        System.out.println("band=" + call(config, "getBand", new Class<?>[0]));
        System.out.println("channel=" + call(config, "getChannel", new Class<?>[0]));
        System.out.println("security_type=" + call(config, "getSecurityType", new Class<?>[0]));
        System.out.println("he_enabled=" + call(config, "isIeee80211axEnabled", new Class<?>[0]));
        System.out.println("eht_enabled=" + call(config, "isIeee80211beEnabled", new Class<?>[0]));
        // No SSID, passphrase, client address list or full config in output.
    }

    public static void main(String[] args) {
        int rc = 1;
        try {
            if (!(args.length == 1 && args[0].equals("inspect"))
                    && !(args.length == 2 && (args[0].equals("prepare")
                    || args[0].equals("prepare-24g") || args[0].equals("prepare-5g")
                    || args[0].equals("apply")
                    || args[0].equals("restore") || args[0].equals("start") || args[0].equals("stop"))))
                throw new IllegalArgumentException("usage: inspect | prepare/prepare-24g/prepare-5g/apply/restore/start/stop <private-task-dir>");
            if ((Integer) Class.forName("android.os.Process").getMethod("myUid").invoke(null) != 0)
                throw new SecurityException("requires_kernelsu_root");
            Object binder = Class.forName("android.os.ServiceManager").getMethod("checkService", String.class)
                    .invoke(null, "wifi");
            if (binder == null) throw new IllegalStateException("wifi_not_ready");
            Object wifi = Class.forName("android.net.wifi.IWifiManager$Stub")
                    .getMethod("asInterface", Class.forName("android.os.IBinder")).invoke(null, binder);
            Object current = call(wifi, "getSoftApConfiguration", new Class<?>[0]);
            if (current == null) throw new IllegalStateException("saved_config_missing");
            summary(wifi, current);
            if (!args[0].equals("inspect")) {
                if (!args[0].equals("stop") && (Integer) call(wifi, "getWifiApEnabledState", new Class<?>[0]) != 11)
                    throw new IllegalStateException("hotspot_must_be_disabled");
                File dir = new File(args[1]).getCanonicalFile();
                if (!dir.isDirectory() || !dir.getPath().startsWith("/data/local/tmp/mt6639-ap-"))
                    throw new IllegalArgumentException("private_task_directory_required");
                File before = new File(dir, "ap.before.parcel"), applied = new File(dir, "ap.applied.parcel");
                if (args[0].equals("prepare") || args[0].equals("prepare-24g")
                        || args[0].equals("prepare-5g")) {
                    save(before, current);
                    Class<?> ct = Class.forName("android.net.wifi.SoftApConfiguration");
                    Object builder = Class.forName("android.net.wifi.SoftApConfiguration$Builder")
                            .getConstructor(ct).newInstance(decode(before));
                    call(builder, "setIeee80211axEnabled", new Class<?>[]{boolean.class}, true);
                    call(builder, "setIeee80211beEnabled", new Class<?>[]{boolean.class},
                            !args[0].equals("prepare-24g"));
                    if (args[0].equals("prepare-24g")) {
                        call(builder, "setBand", new Class<?>[]{int.class}, 1);
                    }
                    if (args[0].equals("prepare-5g")) {
                        call(builder, "setBand", new Class<?>[]{int.class}, 2);
                        int security = (Integer) call(current, "getSecurityType", new Class<?>[0]);
                        // The on-device ApConfigUtil rejects EHT for open/WPA2-only/OWE-transition.
                        // Retain the existing password; never invent or log a credential.
                        if (security == 1) {
                            String passphrase = (String) call(current, "getPassphrase", new Class<?>[0]);
                            if (passphrase == null || passphrase.length() < 8 || passphrase.length() > 63)
                                throw new IllegalStateException("existing_password_not_sae_compatible");
                            call(builder, "setPassphrase", new Class<?>[]{String.class, int.class}, passphrase, 3);
                        } else if (security == 0 || security == 4) {
                            throw new IllegalStateException("eht_compatible_security_required");
                        }
                    }
                    Object target = call(builder, "build", new Class<?>[0]);
                    if (!current.equals(decode(before))) throw new IllegalStateException("original_mutated");
                    save(applied, target);
                    System.out.println("prepared_only=1");
                } else {
                    Object baseline = decode(before), target = decode(applied);
                    if (args[0].equals("start") || args[0].equals("stop")) {
                        if (!current.equals(target))
                            throw new IllegalStateException("saved_config_changed_by_other_owner");
                        boolean accepted;
                        if (args[0].equals("start")) {
                            accepted = (Boolean) call(wifi, "startTetheredHotspot",
                                    new Class<?>[]{target.getClass(), String.class}, target, "android");
                        } else {
                            accepted = (Boolean) call(wifi, "stopSoftAp", new Class<?>[0]);
                        }
                        if (!accepted) throw new IllegalStateException("framework_rejected_request");
                        System.out.println("request_accepted=1 asynchronous_state_requires_inspection=1");
                        System.out.println("RESULT=ok");
                        System.out.flush(); System.exit(0);
                    }
                    if (args[0].equals("restore")) {
                        if (!current.equals(baseline) && !current.equals(target))
                            throw new IllegalStateException("saved_config_changed_by_other_owner");
                        target = baseline;
                    } else if (!current.equals(baseline) && !current.equals(target)) {
                        throw new IllegalStateException("saved_config_changed_by_other_owner");
                    }
                    if (!current.equals(target)) {
                        boolean accepted = (Boolean) call(wifi, "setSoftApConfiguration",
                                new Class<?>[]{target.getClass(), String.class}, target, "android");
                        if (!accepted) throw new IllegalStateException("framework_rejected_config");
                    }
                    Object after = call(wifi, "getSoftApConfiguration", new Class<?>[0]);
                    if (!target.equals(after)) throw new IllegalStateException("saved_config_readback_mismatch");
                    System.out.println("saved_config_matches_target=1");
                }
            }
            System.out.println("RESULT=ok"); rc = 0;
        } catch (Throwable error) {
            while (error instanceof InvocationTargetException && error.getCause() != null) error = error.getCause();
            System.err.println("RESULT=failed " + error.getClass().getSimpleName() + ": " + error.getMessage());
        }
        System.out.flush(); System.err.flush(); System.exit(rc);
    }
}
