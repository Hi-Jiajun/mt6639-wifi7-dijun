package android.os;

/** Uses the same monotonic virtual time as the event scheduler. */
public final class SystemClock {
    public static long elapsedRealtime() { return Handler.elapsedRealtime(); }
}
