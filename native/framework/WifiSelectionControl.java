import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

/** Copies the complete platform selection policy and changes only sufficiency checks. */
final class WifiSelectionControl {
    private static Object call(Object target, String method, Class<?>[] types, Object... args)
            throws Exception {
        Method m = target.getClass().getMethod(method, types);
        m.setAccessible(true);
        return m.invoke(target, args);
    }

    private static Object manager(Object wifi) throws Exception {
        Class<?> looper = Class.forName("android.os.Looper");
        if (looper.getMethod("myLooper").invoke(null) == null)
            looper.getMethod("prepareMainLooper").invoke(null);
        Class<?> at = Class.forName("android.app.ActivityThread");
        Object thread = at.getMethod("systemMain").invoke(null);
        Object context = call(thread, "getSystemContext", new Class<?>[0]);
        Class<?> wm = Class.forName("android.net.wifi.WifiManager");
        return wm.getConstructor(Class.forName("android.content.Context"),
                Class.forName("android.net.wifi.IWifiManager")).newInstance(context, wifi);
    }

    private static Object get(Object wm) throws Exception {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<Object> value = new AtomicReference<>();
        Executor direct = Runnable::run;
        Consumer<Object> callback = config -> { value.set(config); latch.countDown(); };
        call(wm, "getNetworkSelectionConfig", new Class<?>[]{Executor.class, Consumer.class},
                direct, callback);
        if (!latch.await(5, TimeUnit.SECONDS) || value.get() == null)
            throw new IllegalStateException("selection_getter_timeout");
        return value.get();
    }

    private static byte[] encode(Object value) throws Exception {
        Class<?> parcelType = Class.forName("android.os.Parcel");
        Object parcel = parcelType.getMethod("obtain").invoke(null);
        try {
            call(value, "writeToParcel", new Class<?>[]{parcelType, int.class}, parcel, 0);
            return (byte[]) call(parcel, "marshall", new Class<?>[0]);
        } finally { call(parcel, "recycle", new Class<?>[0]); }
    }

    private static Object decode(File file) throws Exception {
        byte[] bytes = Files.readAllBytes(file.toPath());
        if (bytes.length == 0 || bytes.length > 65536)
            throw new IllegalStateException("invalid_selection_backup");
        Class<?> parcelType = Class.forName("android.os.Parcel");
        Object parcel = parcelType.getMethod("obtain").invoke(null);
        try {
            call(parcel, "unmarshall", new Class<?>[]{byte[].class, int.class, int.class},
                    bytes, 0, bytes.length);
            call(parcel, "setDataPosition", new Class<?>[]{int.class}, 0);
            Object creator = Class.forName("android.net.wifi.WifiNetworkSelectionConfig")
                    .getField("CREATOR").get(null);
            return call(creator, "createFromParcel", new Class<?>[]{parcelType}, parcel);
        } finally { call(parcel, "recycle", new Class<?>[0]); }
    }

    private static void save(File file, Object config) throws Exception {
        if (!file.createNewFile()) throw new IllegalStateException("selection_backup_exists");
        try (FileOutputStream stream = new FileOutputStream(file)) {
            stream.write(encode(config));
            stream.getFD().sync();
        }
        if (!decode(file).equals(config)) throw new IllegalStateException("selection_backup_mismatch");
    }

    static void run(Object wifi, String[] args) throws Exception {
        Object wm = manager(wifi);
        Object current = get(wm);
        System.out.println("selection_before=" + current);
        if (args[0].equals("selection-inspect")) return;
        File dir = new File(args[1]).getCanonicalFile();
        if (!dir.isDirectory() || !dir.getPath().startsWith("/data/local/tmp/mt6639-sta-"))
            throw new IllegalArgumentException("private_task_directory_required");
        File before = new File(dir, "selection.before.parcel");
        File applied = new File(dir, "selection.applied.parcel");
        Object target;
        if (args[0].equals("selection-apply")) {
            Class<?> configType = Class.forName("android.net.wifi.WifiNetworkSelectionConfig");
            Class<?> builderType = Class.forName("android.net.wifi.WifiNetworkSelectionConfig$Builder");
            // The platform Builder(config) aliases its argument. Persist before
            // constructing it, then give it a deserialized independent copy.
            save(before, current);
            Object builder = builderType.getConstructor(configType).newInstance(decode(before));
            call(builder, "setSufficiencyCheckEnabledWhenScreenOff", new Class<?>[]{boolean.class}, false);
            call(builder, "setSufficiencyCheckEnabledWhenScreenOn", new Class<?>[]{boolean.class}, false);
            target = call(builder, "build", new Class<?>[0]);
            if (!decode(before).equals(current))
                throw new IllegalStateException("selection_original_was_mutated");
            save(applied, target);
        } else {
            if (!before.isFile()) throw new IllegalStateException("selection_backup_missing");
            target = decode(before);
            // A failure before the setter can leave only the original backup; no
            // change is needed in that case. Never overwrite an unrelated policy.
            if (current.equals(target)) {
                System.out.println("selection_restore=already_original");
                return;
            }
            if (!applied.isFile() || !current.equals(decode(applied)))
                throw new IllegalStateException("selection_changed_by_other_owner");
        }
        call(wm, "setNetworkSelectionConfig", new Class<?>[]{target.getClass()}, target);
        if (!get(wm).equals(target)) throw new IllegalStateException("selection_readback_mismatch");
        System.out.println("selection_after=" + target);
    }
}
