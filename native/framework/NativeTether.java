import android.os.Binder;
import android.os.Parcel;
import android.os.RemoteException;
import java.io.File;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Android-owned IP serving for a separately owned native BSS, not WifiManager SoftAP. */
public final class NativeTether {
    // Verified against this device's framework-tethering.jar IIntResultListener.Stub.
    private static final String RESULT_DESCRIPTOR = "android.net.IIntResultListener";
    private static final class Result extends Binder {
        final CountDownLatch ready = new CountDownLatch(1);
        volatile int result = -1;
        @Override public boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            if (code == 1598968902) { // IBinder.INTERFACE_TRANSACTION
                if (reply != null) reply.writeString(RESULT_DESCRIPTOR);
                return true;
            }
            if (code == 1) { // onResult(int); no raw connector transaction numbers.
                data.enforceInterface(RESULT_DESCRIPTOR);
                int value = data.readInt();
                data.enforceNoDataAvail();
                result = value;
                ready.countDown();
                return true;
            }
            return super.onTransact(code, data, reply, flags);
        }
    }

    private static Object call(Object target, String name, Class<?>[] types, Object... args)
            throws Exception {
        Method method = target.getClass().getMethod(name, types);
        method.setAccessible(true);
        return method.invoke(target, args);
    }

    public static void main(String[] args) {
        int rc = 1;
        try {
            if (args.length != 1 && args.length != 2)
                throw new IllegalArgumentException("inspect | start/stop <private-task-directory>");
            String op = args[0];
            if (!op.equals("inspect") && !op.equals("start") && !op.equals("stop"))
                throw new IllegalArgumentException("unknown_operation");
            if ((op.equals("inspect") ? 1 : 2) != args.length)
                throw new IllegalArgumentException("wrong_argument_count");
            if ((Integer) Class.forName("android.os.Process").getMethod("myUid").invoke(null) != 0)
                throw new SecurityException("requires_kernelsu_root");
            Object binder = Class.forName("android.os.ServiceManager")
                    .getMethod("checkService", String.class).invoke(null, "tethering");
            if (binder == null) throw new IllegalStateException("tethering_not_ready");
            Object connector = Class.forName("android.net.ITetheringConnector$Stub")
                    .getMethod("asInterface", Class.forName("android.os.IBinder")).invoke(null, binder);
            Class<?> listenerType = Class.forName(RESULT_DESCRIPTOR);
            Class<?> manager = Class.forName("android.net.TetheringManager");
            int virtualType = manager.getField("TETHERING_VIRTUAL").getInt(null);
            int globalScope = manager.getField("CONNECTIVITY_SCOPE_GLOBAL").getInt(null);
            if (virtualType != 7 || globalScope != 1)
                throw new IllegalStateException("unexpected_tethering_api");
            Class<?> builderType = Class.forName("android.net.TetheringManager$TetheringRequest$Builder");
            Object builder = builderType.getConstructor(int.class).newInstance(virtualType);
            call(builder, "setInterfaceName", new Class<?>[]{String.class}, "mtap0");
            call(builder, "setConnectivityScope", new Class<?>[]{int.class}, globalScope);
            Object request = call(builder, "build", new Class<?>[0]);
            Object parcel = call(request, "getParcel", new Class<?>[0]);
            connector.getClass().getMethod("startTethering", parcel.getClass(), String.class,
                    String.class, listenerType);
            connector.getClass().getMethod("stopTetheringRequest", request.getClass(), String.class,
                    String.class, listenerType);
            System.out.println("downstream_type=VIRTUAL interface=mtap0 scope=GLOBAL");
            if (op.equals("inspect")) {
                System.out.println("api_shape_verified=1 no_request_sent=1");
            } else {
                File dir = new File(args[1]).getCanonicalFile();
                if (!dir.isDirectory() || !dir.getPath().startsWith("/data/local/tmp/mt6639-native-ap-"))
                    throw new IllegalArgumentException("private_task_directory_required");
                String boot = new String(Files.readAllBytes(new File(dir, "boot.before").toPath())).trim();
                String liveBoot = new String(Files.readAllBytes(new File("/proc/sys/kernel/random/boot_id").toPath())).trim();
                if (!boot.equals(liveBoot)) throw new IllegalStateException("boot_changed");
                String index = new String(Files.readAllBytes(new File(dir, "interface.index").toPath())).trim();
                String liveIndex = new String(Files.readAllBytes(new File("/sys/class/net/mtap0/ifindex").toPath())).trim();
                if (!index.equals(liveIndex)) throw new IllegalStateException("interface_owner_changed");
                if (!new File(dir, "tether.intent").isFile())
                    throw new IllegalStateException("missing_tether_intent");
                Result result = new Result();
                Object listener = Proxy.newProxyInstance(listenerType.getClassLoader(),
                        new Class<?>[]{listenerType}, (proxy, method, params) -> {
                            if (method.getName().equals("asBinder")) return result;
                            throw new UnsupportedOperationException(method.getName());
                        });
                if (op.equals("start")) {
                    call(connector, "startTethering", new Class<?>[]{parcel.getClass(), String.class,
                            String.class, listenerType}, parcel, "android", null, listener);
                } else {
                    // Stop only this exact request. The type-only API is global and can
                    // tear down a different TETHERING_VIRTUAL client that started later.
                    call(connector, "stopTetheringRequest", new Class<?>[]{request.getClass(),
                            String.class, String.class, listenerType}, request, "android", null, listener);
                }
                if (!result.ready.await(8, TimeUnit.SECONDS))
                    throw new IllegalStateException("callback_timeout_state_requires_recovery");
                System.out.println("callback_result=" + result.result);
                if (result.result != 0) throw new IllegalStateException("tethering_rejected_" + result.result);
                System.out.println("request_accepted=1 async_state_requires_inspection=1");
            }
            System.out.println("RESULT=ok"); rc = 0;
        } catch (Throwable error) {
            while (error instanceof InvocationTargetException && error.getCause() != null)
                error = error.getCause();
            System.err.println("RESULT=failed " + error.getClass().getSimpleName() + ": " + error.getMessage());
        }
        System.out.flush(); System.err.flush(); System.exit(rc);
    }
}
