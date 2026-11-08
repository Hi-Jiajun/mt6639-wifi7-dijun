package android.os;
/** Compile-time ABI only; never include in the delivered DEX. */
public class Parcel {
    public void writeString(String value) { throw new UnsupportedOperationException(); }
    public void enforceInterface(String value) { throw new UnsupportedOperationException(); }
    public int readInt() { throw new UnsupportedOperationException(); }
    public void enforceNoDataAvail() { throw new UnsupportedOperationException(); }
}
