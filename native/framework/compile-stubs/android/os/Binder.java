package android.os;
/** Compile-time ABI only; never include in the delivered DEX. */
public class Binder {
    protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
            throws RemoteException { throw new UnsupportedOperationException(); }
}
