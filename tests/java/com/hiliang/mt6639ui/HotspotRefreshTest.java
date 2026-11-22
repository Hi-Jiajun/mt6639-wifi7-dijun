package com.hiliang.mt6639ui;

import android.app.AlertDialog;
import android.content.Context;
import android.os.Handler;
import de.robv.android.xposed.XposedHelpers;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/** Runs the real MainHook lifecycle callbacks and status parser against host fixtures. */
public final class HotspotRefreshTest {
    public interface ClickListener { boolean onPreferenceClick(Object preference); }
    private static Path snapshotPath;
    private static String boot;
    private static final String STATUS = "mt6639_experimental_hotspot_status";
    private static final String START = "mt6639_experimental_hotspot_start";
    private static final String STOP = "mt6639_experimental_hotspot_stop";
    private static final String C02 = "mt6639_experimental_hotspot_c02";

    public static class Preference {
        public String summary;
        public boolean visible;
        public boolean enabled;
        public boolean selectable;
        public int updates;
        public ClickListener listener;
        public void setOnPreferenceClickListener(ClickListener value) { listener = value; }
        public void setSummary(CharSequence value) { summary = value.toString(); updates++; }
        public void setVisible(boolean value) { visible = value; }
        public void setEnabled(boolean value) { enabled = value; }
        public void setSelectable(boolean value) { selectable = value; }
    }
    public static class Group {
        final Map<String, Preference> preferences = new HashMap<>();
        Group() {
            for (String key : new String[] { STATUS, START, STOP, C02 }) {
                preferences.put(key, new Preference());
            }
        }
        public Preference findPreference(String key) { return preferences.get(key); }
    }
    public static class Fragment {
        final Group group = new Group();
        final String groupKey;
        Fragment(String key) { groupKey = key; }
        public void onPause() { }
        public void onDestroyView() { }
        public void onDestroy() { }
        public Group findPreference(String key) {
            if (!groupKey.equals(key)) throw new AssertionError("wrong group " + key);
            return group;
        }
        Preference preference(String key) { return group.preferences.get(key); }
    }
    public static final class MiuiFragment extends Fragment {
        public MiuiFragment() { super("miui_tether_common_category"); }
    }
    public static final class AllInOneFragment extends Fragment {
        public AllInOneFragment() { super("wifi_tether_settings_group"); }
    }

    public static void main(String[] args) throws Throwable {
        snapshotPath = Path.of(args[0]);
        boot = Files.readString(Path.of("/proc/sys/kernel/random/boot_id")).trim();
        hook("com.android.settings.MiuiTetherSettings", "miui_tether_common_category");
        hook("com.android.settings.AllInOneTetherSettings", "wifi_tether_settings_group");
        exercise(new MiuiFragment());
        exercise(new AllInOneFragment());
        independentPages();
        pendingSubmission();
        pendingStartAndC02();
        pickerOffersBandsByReadiness();
        v4PureTabletLocalHotspot();
        System.out.println("RESULT=PASS hotspot_refresh states=lifecycle+idle-starting-active-stopping-idle+unavailable pages=2");
    }

    private static void v4PureTabletLocalHotspot() throws Throwable {
        Fragment fragment = new MiuiFragment();
        Files.writeString(snapshotPath, "schema=mt6639-hotspot-status-v4\nstate=idle"
                + "\nband=none\nwidth=none\nboot=" + boot
                + "\nap_key=1\nreconnect_6g=0\nreconnect_2g=0\nenforcing=1\n"
                + "ready_6g=1\nready_5g=1\nready_2g=1\nlocal_ready=1\n", StandardCharsets.US_ASCII);
        XposedHelpers.fireAfter(fragment, "onResume");
        check(!fragment.preference(C02).visible, "v4 without reconnect_6g hides lab C02 preference");
        Preference start = fragment.preference(START);
        check(start.enabled, "v4 pure tablet start is enabled");
        Method install = MainHook.class.getDeclaredMethod("installHotspotStartPicker",
                Object.class, ClassLoader.class, Context.class);
        install.setAccessible(true);
        install.invoke(null, start, HotspotRefreshTest.class.getClassLoader(), null);
        HotspotRequestWriter.accept = true;
        start.listener.onPreferenceClick(start);
        CharSequence[] items = AlertDialog.items();
        int localIdx = -1;
        for (int index = 0; index < items.length; index++) {
            if (items[index].toString().startsWith("本地直连 6 GHz")) {
                localIdx = index;
                break;
            }
        }
        check(localIdx >= 0, "v4 picker offers local hotspot actions when local_ready=1");
        AlertDialog.confirm(localIdx);
        check("start-local-6g-20".equals(HotspotRequestWriter.lastOperation),
                "picker submits start-local-6g-20");
        writeState("starting");
        Handler.advanceBy(2000);
        HotspotRequestWriter.accept = false;
        XposedHelpers.fireAfter(fragment, "onDestroy");
        System.out.println("PASS v4 pure tablet picker hides C02 and offers start-local-*");
    }

    private static void pickerOffersBandsByReadiness() throws Throwable {
        Fragment fragment = new MiuiFragment();
        writeStateV3("idle", "0");
        XposedHelpers.fireAfter(fragment, "onResume");
        Preference start = fragment.preference(START);
        Method install = MainHook.class.getDeclaredMethod("installHotspotStartPicker",
                Object.class, ClassLoader.class, Context.class);
        install.setAccessible(true);
        install.invoke(null, start, HotspotRefreshTest.class.getClassLoader(), null);
        HotspotRequestWriter.accept = true;

        start.listener.onPreferenceClick(start);
        CharSequence[] items = AlertDialog.items();
        int twoG = -1;
        int fiveG = 0;
        for (int index = 0; index < items.length; index++) {
            String label = items[index].toString();
            if (label.startsWith("2.4 GHz · 20 MHz")) twoG = index;
            if (label.startsWith("5 GHz")) fiveG++;
        }
        check(twoG >= 0, "2.4GHz is offered when only the upstream key is staged");
        check(fiveG == 0, "5GHz stays withheld while the STA is not on LabNet-2.4G");
        int requests = HotspotRequestWriter.submissions;
        AlertDialog.confirm(twoG);
        check("start-2g-20".equals(HotspotRequestWriter.lastOperation),
                "picker submits the 2.4GHz action");
        check(HotspotRequestWriter.submissions == requests + 1, "exactly one 2.4GHz request");

        // A state change releases the pending guard without faking success.
        writeStateV3("starting", "0");
        Handler.advanceBy(2000);
        writeStateV3("idle", "1");
        Handler.advanceBy(2000);
        install.invoke(null, start, HotspotRefreshTest.class.getClassLoader(), null);
        start.listener.onPreferenceClick(start);
        int fives = 0;
        int twos = 0;
        for (CharSequence item : AlertDialog.items()) {
            if (item.toString().startsWith("5 GHz")) fives++;
            if (item.toString().startsWith("2.4 GHz")) twos++;
        }
        check(fives == 3 && twos == 1, "5GHz appears only with its STA upstream, 2.4GHz stays");
        AlertDialog.confirm(0);
        check("start-6g-20".equals(HotspotRequestWriter.lastOperation),
                "6GHz remains the first/default pick");
        writeStateV3("starting", "1");
        Handler.advanceBy(2000);
        HotspotRequestWriter.accept = false;
        XposedHelpers.fireAfter(fragment, "onDestroy");
        System.out.println("PASS picker readiness: 2.4GHz gated by key only, 5GHz by STA band");
    }

    private static void pendingSubmission() throws Throwable {
        Fragment fragment = new MiuiFragment();
        writeState("active");
        XposedHelpers.fireAfter(fragment, "onResume");
        Preference stop = fragment.preference(STOP);
        installAction(stop, "stop");
        HotspotRequestWriter.accept = true;
        stop.listener.onPreferenceClick(stop);
        check(!stop.enabled, "successful submission disables stop immediately");
        Handler.advanceBy(2000);
        check(!stop.enabled, "unchanged active snapshot must not re-enable pending stop");
        check(HotspotRequestWriter.submissions == 1, "polling must never resubmit requests");
        stop.listener.onPreferenceClick(stop);
        check(HotspotRequestWriter.submissions == 1, "stale click must not duplicate stop");
        XposedHelpers.fireAfter(fragment, "onPause");
        Fragment recreated = new AllInOneFragment();
        XposedHelpers.fireAfter(recreated, "onResume");
        check(!recreated.preference(STOP).enabled, "pending survives page navigation");
        Handler.advanceBy(56000);
        check(!recreated.preference(STOP).enabled, "pending remains locked before 60s");
        Handler.advanceBy(2000);
        check(recreated.preference(STOP).enabled, "60s timeout permits eligible retry");
        check(recreated.preference(STATUS).summary.contains("超时")
                && recreated.preference(STATUS).summary.contains("尚未确认"),
                "timeout explicitly says unconfirmed");
        check(HotspotRequestWriter.submissions == 1, "timeout must not automatically replay");
        stop = recreated.preference(STOP);
        installAction(stop, "stop");
        HotspotRequestWriter.succeed = false;
        stop.listener.onPreferenceClick(stop);
        check(stop.enabled && stop.summary.contains("提交失败"), "failed submission is retryable immediately");
        Handler.advanceBy(2000);
        check(stop.enabled && recreated.preference(STATUS).summary.contains("提交失败"),
                "failure explanation survives polling");
        HotspotRequestWriter.succeed = true;
        stop.listener.onPreferenceClick(stop);
        check(!stop.enabled && HotspotRequestWriter.submissions == 3,
                "retry submits exactly one new request");
        writeState("stopping");
        Handler.advanceBy(2000);
        check("实验热点正在停止".equals(recreated.preference(STATUS).summary),
                "observed progress replaces waiting notice without claiming success");
        writeState("idle");
        Handler.advanceBy(2000);
        check(recreated.preference(START).enabled, "completed stop restores idle eligibility");
        HotspotRequestWriter.accept = false;
        XposedHelpers.fireAfter(recreated, "onDestroy");
        System.out.println("PASS pending stop + duplicate guard + navigation + monotonic 60s timeout + retry");
    }

    private static void pendingStartAndC02() throws Throwable {
        Fragment fragment = new MiuiFragment();
        writeState("idle");
        XposedHelpers.fireAfter(fragment, "onResume");
        Preference start = fragment.preference(START);
        Preference c02 = fragment.preference(C02);
        Method install = MainHook.class.getDeclaredMethod("installHotspotStartPicker",
                Object.class, ClassLoader.class, Context.class);
        install.setAccessible(true);
        install.invoke(null, start, HotspotRefreshTest.class.getClassLoader(), null);
        installAction(c02, "c02-24g");
        HotspotRequestWriter.accept = true;
        int requests = HotspotRequestWriter.submissions;
        start.listener.onPreferenceClick(start);
        AlertDialog.confirm();
        check("start-6g-20".equals(HotspotRequestWriter.lastOperation), "picker submits selected action");
        // A different action can still have a stale enabled row before the next tick.
        c02.listener.onPreferenceClick(c02);
        Handler.advanceBy(2000);
        check(!start.enabled && !c02.enabled, "unchanged idle cannot restore start or competing C02");
        check(HotspotRequestWriter.submissions == requests + 1, "pending blocks competing request");
        Files.writeString(snapshotPath, "malformed\n", StandardCharsets.US_ASCII);
        Handler.advanceBy(2000);
        check(!start.enabled && fragment.preference(STATUS).summary.contains("等待模块"),
                "unavailable snapshot is not confirmation");
        writeState("starting");
        Handler.advanceBy(2000);
        check(fragment.preference(STOP).enabled, "observed starting releases guard for stop");
        writeState("idle");
        Handler.advanceBy(2000);
        c02.listener.onPreferenceClick(c02);
        check("c02-24g".equals(HotspotRequestWriter.lastOperation), "C02 action submitted");
        Handler.advanceBy(2000);
        check(!c02.enabled && !start.enabled, "pending C02 cannot repeat on unchanged idle");
        c02.listener.onPreferenceClick(c02);
        check(HotspotRequestWriter.submissions == requests + 2, "C02 duplicate guarded");
        writeState("starting");
        Handler.advanceBy(2000);
        HotspotRequestWriter.accept = false;
        XposedHelpers.fireAfter(fragment, "onDestroy");
        System.out.println("PASS pending start/C02 + competing click + unavailable is unconfirmed + observed progress");
    }

    private static void installAction(Preference preference, String operation) throws Exception {
        Method install = MainHook.class.getDeclaredMethod("installHotspotAction",
                Object.class, ClassLoader.class, Object.class, String.class);
        install.setAccessible(true);
        install.invoke(null, preference, HotspotRefreshTest.class.getClassLoader(), null, operation);
    }

    private static void hook(String name, String group) throws Exception {
        Method hook = MainHook.class.getDeclaredMethod(
                "hookHotspotSettings", ClassLoader.class, String.class, String.class);
        hook.setAccessible(true);
        hook.invoke(null, HotspotRefreshTest.class.getClassLoader(), name, group);
    }

    private static void exercise(Fragment fragment) throws Throwable {
        writeState("idle");
        XposedHelpers.fireAfter(fragment, "onResume");
        check(fragment.preference(START).enabled, "idle start enabled");
        check(!fragment.preference(STOP).visible, "idle stop hidden");
        writeState("starting");
        Handler.advanceBy(2000);
        check(fragment.preference(STOP).visible && fragment.preference(STOP).enabled,
                "starting stop must become visible/enabled without leaving page");
        check(!fragment.preference(START).visible, "starting start hidden");
        writeState("active");
        Handler.advanceBy(2000);
        check("实验热点 active · 6 GHz · 80 MHz".equals(fragment.preference(STATUS).summary),
                "active summary refreshes on current page");
        check(fragment.preference(STOP).enabled, "active stop enabled");
        writeState("stopping");
        Handler.advanceBy(2000);
        check("实验热点正在停止".equals(fragment.preference(STATUS).summary), "stopping summary");
        check(!fragment.preference(STOP).enabled && !fragment.preference(START).enabled,
                "stopping disables actions");
        writeState("idle");
        Handler.advanceBy(2000);
        check(fragment.preference(START).enabled && !fragment.preference(STOP).visible,
                "idle restores start and hides stop");
        Files.writeString(snapshotPath, "malformed\n", StandardCharsets.US_ASCII);
        Handler.advanceBy(2000);
        check(fragment.preference(STATUS).summary.startsWith("状态不可用"), "unavailable summary");
        check(!fragment.preference(STOP).enabled && !fragment.preference(START).enabled,
                "unavailable snapshot never presumes action success");

        XposedHelpers.fireAfter(fragment, "onResume");
        XposedHelpers.fireAfter(fragment, "onResume");
        check(Handler.pendingCount() == 1, "repeated resume keeps exactly one loop");
        int reads = fragment.preference(STATUS).updates;
        Handler.advanceBy(2000);
        check(fragment.preference(STATUS).updates == reads + 1, "one read per interval");
        Runnable stale = Handler.nextTask();
        XposedHelpers.fireAfter(fragment, "onPause");
        assertStopped(fragment, stale, "pause");
        writeState("active");
        XposedHelpers.fireAfter(fragment, "onResume");
        check(fragment.preference(STOP).enabled, "resume reads latest snapshot immediately");
        // Even a late callback from the previous visible session must stay cancelled.
        int resumedReads = fragment.preference(STATUS).updates;
        stale.run();
        check(fragment.preference(STATUS).updates == resumedReads && Handler.pendingCount() == 1,
                "late callback cannot join resumed loop");
        stale = Handler.nextTask();
        XposedHelpers.fireAfter(fragment, "onDestroyView");
        assertStopped(fragment, stale, "destroyView");
        XposedHelpers.fireAfter(fragment, "onResume");
        stale = Handler.nextTask();
        XposedHelpers.fireAfter(fragment, "onDestroy");
        assertStopped(fragment, stale, "destroy");
        System.out.println("PASS " + fragment.getClass().getSimpleName() + " transitions + cancellation");
    }

    private static void assertStopped(Fragment fragment, Runnable stale, String lifecycle) {
        int reads = fragment.preference(STATUS).updates;
        check(Handler.pendingCount() == 0, lifecycle + " removes queued callback");
        stale.run();
        Handler.advanceBy(10000);
        check(fragment.preference(STATUS).updates == reads && Handler.pendingCount() == 0,
                lifecycle + " stops reads and stale callbacks");
    }

    private static void independentPages() throws Throwable {
        Fragment first = new MiuiFragment();
        Fragment second = new AllInOneFragment();
        XposedHelpers.fireAfter(first, "onResume");
        XposedHelpers.fireAfter(second, "onResume");
        check(Handler.pendingCount() == 2, "one loop per visible page");
        XposedHelpers.fireAfter(first, "onPause");
        int firstReads = first.preference(STATUS).updates;
        int secondReads = second.preference(STATUS).updates;
        Handler.advanceBy(2000);
        check(first.preference(STATUS).updates == firstReads
                && second.preference(STATUS).updates == secondReads + 1,
                "pausing one page does not stop the other");
        XposedHelpers.fireAfter(second, "onDestroy");
        check(Handler.pendingCount() == 0, "all pages stopped");
    }

    private static void writeState(String state) throws Exception {
        Files.writeString(snapshotPath, "schema=mt6639-hotspot-status-v1\nstate=" + state
                + "\nband=6g\nwidth=80\nboot=" + boot + "\n", StandardCharsets.US_ASCII);
    }

    private static void writeStateV3(String state, String fivegUpstream) throws Exception {
        Files.writeString(snapshotPath, "schema=mt6639-hotspot-status-v3\nstate=" + state
                + "\nband=none\nwidth=none\nboot=" + boot
                + "\nap_key=1\nreconnect_6g=1\nreconnect_2g=1\nenforcing=1\n"
                + "fiveg_upstream=" + fivegUpstream + "\n", StandardCharsets.US_ASCII);
    }
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
