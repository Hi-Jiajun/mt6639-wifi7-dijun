import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/** Root-shell framework capability and multi-internet access, without raw transaction IDs. */
public final class WifiStaMode {
    private static Object invoke(Object receiver, String name, Class<?>[] types, Object... args)
            throws Exception {
        Method method = receiver.getClass().getMethod(name, types);
        method.setAccessible(true);
        return method.invoke(receiver, args);
    }

    private static int mode(Object wifi) throws Exception {
        int value = (Integer) invoke(wifi, "getStaConcurrencyForMultiInternetMode", new Class<?>[0]);
        if (value < 0 || value > 2) throw new IllegalStateException("unknown_framework_mode");
        return value;
    }

    private static boolean supported(Object wifi, String field) throws Exception {
        int feature = Class.forName("android.net.wifi.WifiManager").getField(field).getInt(null);
        return (Boolean) invoke(wifi, "isFeatureSupported", new Class<?>[]{int.class}, feature);
    }

    private static int printCapabilities(Object wifi) {
        String[] fields = {
            "WIFI_FEATURE_CONTROL_ROAMING", "WIFI_FEATURE_MBO", "WIFI_FEATURE_OCE",
            "WIFI_FEATURE_WPA3_SAE", "WIFI_FEATURE_WPA3_SUITE_B",
            "WIFI_FEATURE_SAE_H2E", "WIFI_FEATURE_SAE_PK", "WIFI_FEATURE_OWE",
            "WIFI_FEATURE_DPP", "WIFI_FEATURE_DPP_ENROLLEE_RESPONDER", "WIFI_FEATURE_DPP_AKM"
        };
        int errors = 0;
        for (String field : fields) {
            try {
                System.out.println(field + "=" + supported(wifi, field));
            } catch (Exception | LinkageError error) {
                Throwable cause = error;
                while (cause instanceof InvocationTargetException && cause.getCause() != null)
                    cause = cause.getCause();
                System.out.println(field + "=error");
                // Exception messages may include service details; report only the error type.
                System.out.println(field + "_error=" + cause.getClass().getSimpleName());
                errors++;
            }
        }
        return errors;
    }

    public static void main(String[] args) {
        int exit = 1;
        try {
            boolean capabilities = args.length == 1 && args[0].equals("capabilities");
            boolean selection = (args.length == 1 && args[0].equals("selection-inspect"))
                    || (args.length == 2 && (args[0].equals("selection-apply")
                    || args[0].equals("selection-restore")));
            if (!(args.length == 1 && args[0].equals("inspect")) && !capabilities && !selection
                    && !(args.length == 3 && args[0].equals("compare-set"))) {
                throw new IllegalArgumentException("usage: inspect | capabilities | compare-set <expected 0..2> <target 0..2> | selection-inspect | selection-apply/selection-restore <task-dir>");
            }
            int expected = -1, target = -1;
            if (args.length == 3) {
                expected = Integer.parseInt(args[1]);
                target = Integer.parseInt(args[2]);
                if (expected < 0 || expected > 2 || target < 0 || target > 2)
                    throw new IllegalArgumentException("mode_out_of_range");
            }
            int uid = (Integer) Class.forName("android.os.Process").getMethod("myUid").invoke(null);
            if (uid != 0) throw new SecurityException("requires_ksu_root");
            Class<?> binderType = Class.forName("android.os.IBinder");
            Object binder = Class.forName("android.os.ServiceManager")
                    .getMethod("checkService", String.class).invoke(null, "wifi");
            if (binder == null) throw new IllegalStateException("wifi_service_not_ready");
            Object wifi = Class.forName("android.net.wifi.IWifiManager$Stub")
                    .getMethod("asInterface", binderType).invoke(null, binder);
            if (capabilities) {
                int errors = printCapabilities(wifi);
                System.out.println("RESULT=" + (errors == 0 ? "ok" : "partial") + " errors=" + errors);
                System.out.flush();
                System.err.flush();
                System.exit(errors == 0 ? 0 : 1);
                return;
            }
            if (selection) {
                WifiSelectionControl.run(wifi, args);
                System.out.println("RESULT=ok");
                System.out.flush();
                System.exit(0);
            }
            boolean multi = supported(wifi, "WIFI_FEATURE_ADDITIONAL_STA_MULTI_INTERNET");
            boolean dbs = supported(wifi, "WIFI_FEATURE_DUAL_BAND_SIMULTANEOUS");
            int before = mode(wifi);
            System.out.println("multi_internet_supported=" + multi);
            System.out.println("dbs_supported=" + dbs);
            System.out.println("before_mode=" + before);
            if (args.length == 3) {
                // This is a guarded read/set/read, not an atomic Binder CAS. The caller owns
                // serialization and must refuse restoration after an unrelated mode change.
                if (before != expected) throw new IllegalStateException("mode_changed_by_other_owner");
                if (target != 0 && (!multi || (target == 1 && !dbs)))
                    throw new IllegalStateException("framework_capability_missing");
                if (target != before) {
                    boolean accepted = (Boolean) invoke(wifi,
                            "setStaConcurrencyForMultiInternetMode", new Class<?>[]{int.class}, target);
                    System.out.println("setter_accepted=" + accepted);
                    if (!accepted) throw new IllegalStateException("framework_rejected_mode");
                }
                int after = mode(wifi);
                System.out.println("after_mode=" + after);
                if (after != target) throw new IllegalStateException("framework_readback_mismatch");
            }
            System.out.println("RESULT=ok");
            exit = 0;
        } catch (Throwable error) {
            while (error instanceof InvocationTargetException && error.getCause() != null)
                error = error.getCause();
            System.err.println("RESULT=failed " + error.getClass().getSimpleName() + ": " + error.getMessage());
        }
        System.out.flush();
        System.err.flush();
        System.exit(exit);
    }
}
