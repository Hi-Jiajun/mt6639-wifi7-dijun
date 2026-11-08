package android.os;

import java.util.ArrayList;

/** Deterministic host event queue; never sleeps or starts a thread. */
public final class Handler {
    private static final ArrayList<Event> EVENTS = new ArrayList<>();
    private static long now;
    private static final class Event {
        final Runnable task;
        final long due;
        Event(Runnable task, long due) { this.task = task; this.due = due; }
    }
    public Handler(Looper looper) {
        if (looper != Looper.getMainLooper()) throw new AssertionError("not main looper");
    }
    public boolean postDelayed(Runnable task, long delay) {
        if (delay <= 0) throw new AssertionError("refresh must have bounded frequency");
        EVENTS.add(new Event(task, now + delay));
        return true;
    }
    public void removeCallbacks(Runnable task) {
        EVENTS.removeIf(event -> event.task == task);
    }
    public static int pendingCount() { return EVENTS.size(); }
    public static long elapsedRealtime() { return now; }
    public static Runnable nextTask() { return EVENTS.get(0).task; }
    public static void advanceBy(long elapsed) {
        long target = now + elapsed;
        while (true) {
            Event next = null;
            for (Event event : EVENTS) {
                if (event.due <= target && (next == null || event.due < next.due)) next = event;
            }
            if (next == null) break;
            EVENTS.remove(next);
            now = next.due;
            next.task.run();
        }
        now = target;
    }
}
