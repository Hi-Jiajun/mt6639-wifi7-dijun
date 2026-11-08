package com.hiliang.mt6639ui;

import java.util.Arrays;
import java.util.Collections;

/** Executes the production resolver against reflected Android-shaped fixtures. */
public final class BandResolverTest {
    private static int passed;
    private static final BandResolver.Reader READER = new BandResolver.Reader() {
        @Override
        public Object call(Object receiver, String method) throws Exception {
            return receiver.getClass().getMethod(method).invoke(receiver);
        }
    };

    public static void main(String[] args) {
        check("connected 6 GHz", entry(2, info(null, 6115), scan(1)), true, 8, "6G");
        check("MLO 2.4/5", mlo(link(3, 1), link(3, 2)), true, 3, "2.4G/5G");
        check("MLO 2.4/6", mlo(link(3, 1), link(3, 8)), true, 9, "2.4G/6G");
        check("MLO 5/6", mlo(link(3, 2), link(3, 8)), true, 10, "5G/6G");
        check("MLO triple", mlo(link(3, 8), link(3, 1), link(3, 2)),
                true, 11, "2.4G/5G/6G");
        check("inactive links excluded", mlo(link(2, 1), link(1, 2), link(3, 8)),
                true, 8, "6G");
        check("no active does not use 6 GHz main or scans", mlo(link(2, 2)),
                true, 0, "");
        check("unknown active band does not fallback", mlo(link(3, 4)), true, 0, "");
        check("mixed known unknown active suppresses partial label",
                mlo(link(3, 8), link(3, 4)), true, 0, "");
        check("failed active band suppresses partial label",
                mlo(link(3, 8), new BrokenBandLink()), true, 0, "");
        check("failed link state suppresses label",
                mlo(link(3, 8), new BrokenStateLink()), true, 0, "");
        check("unknown link state suppresses label", mlo(link(3, 8), link(99, 1)),
                true, 0, "");
        check("invalid link state suppresses label", mlo(link(3, 8), link(0, 1)),
                true, 0, "");
        check("inactive failed band is irrelevant", mlo(new IdleBrokenBandLink(), link(3, 8)),
                true, 8, "6G");
        check("missing WifiInfo never uses scans", entry(2, null, scan(8)), true, 0, "");
        check("failed WifiInfo never uses scans", new BrokenInfoEntry(), true, 0, "");
        check("empty MLO fallback 5 GHz", entry(2, info(Collections.emptyList(), 5180), scan(8)),
                true, 2, "5G");
        check("null MLO fallback 2.4 GHz", entry(2, info(null, 2412), scan(8)),
                true, 1, "2.4G");
        check("6 GHz channel two", entry(2, info(null, 5935), scan(1)), true, 8, "6G");
        check("6 GHz top channel", entry(2, info(null, 7115), scan(1)), true, 8, "6G");
        check("unknown main frequency never uses scans", entry(2, info(null, 0), scan(8)),
                true, 0, "");
        check("failed MLO read never falls back", entry(2, new BrokenMloInfo(), scan(8)),
                true, 0, "");
        check("non iterable MLO never falls back", entry(2, info("bad", 6115), scan(8)),
                true, 0, "");
        check("failed frequency never uses scans", entry(2, new BrokenFrequencyInfo(), scan(8)),
                true, 0, "");
        check("connected never calls scan results", new ConnectedNoScanEntry(),
                true, 8, "6G");
        check("disconnected uses only scans", entry(0, info(null, 6115), scan(2)),
                false, 2, "5G");
        check("disconnected scans union", entry(0, info(null, 2412), scan(8), scan(2), scan(1)),
                false, 11, "2.4G/5G/6G");
        check("disconnected never calls WifiInfo", new DisconnectedNoInfoEntry(),
                false, 8, "6G");
        check("disconnected no scans ignores stale WifiInfo", entry(0, info(null, 6115)),
                false, 0, "");
        check("failed connected state does not scan", new BrokenConnectedStateEntry(),
                false, 0, "");
        check("unknown connected state does not scan", entry(99, null, scan(8)),
                false, 0, "");
        check("connecting state does not scan", entry(1, null, scan(8)), false, 0, "");
        check("null entry", null, false, 0, "");
        check("scan frequency fallback when getBand absent",
                entry(0, null, new LegacyScan(6115)), false, 8, "6G");
        check("scan getBand wins over frequency", entry(0, null, new Scan(2, 6115)),
                false, 2, "5G");
        check("unsupported scan band skipped", entry(0, null, scan(4), scan(8)),
                false, 8, "6G");
        check("throwing scan band does not use frequency",
                entry(0, null, new BrokenScan(), scan(2)), false, 2, "5G");
        check("failed scan results read is unknown", new BrokenScansEntry(), false, 0, "");
        check("null scan skipped", entry(0, null, null, scan(8)), false, 8, "6G");
        check("null MLO link suppresses partial label", mlo(link(3, 8), null), true, 0, "");
        check("duplicate bands collapse", mlo(link(3, 8), link(3, 8)), true, 8, "6G");
        check("gap below 6 GHz is unknown", entry(2, info(null, 5945)), true, 0, "");
        System.out.println("RESULT=PASS cases=" + passed);
    }

    private static void check(String name, Object entry, boolean connected, int mask, String label) {
        BandResolver.Result result = BandResolver.resolve(entry, READER);
        if (result.connected != connected || result.mask != mask
                || !result.label().equals(label) || result.has6() != ((mask & 8) != 0)) {
            throw new AssertionError(name + ": expected connected=" + connected + " mask=" + mask
                    + " label=" + label + "; got connected=" + result.connected
                    + " mask=" + result.mask + " label=" + result.label());
        }
        passed++;
    }

    private static Entry entry(int state, Object info, Object... scans) {
        return new Entry(state, info, Arrays.asList(scans));
    }

    private static Entry mlo(Object... links) {
        return entry(2, info(Arrays.asList(links), 6115), scan(8));
    }

    private static Info info(Object links, int frequency) { return new Info(links, frequency); }
    private static Link link(int state, int band) { return new Link(state, band); }
    private static Scan scan(int band) { return new Scan(band, 6115); }

    public static class Entry {
        final int state;
        final Object info;
        final Object scans;
        Entry(int state, Object info, Object scans) {
            this.state = state; this.info = info; this.scans = scans;
        }
        public int getConnectedState() { return state; }
        public Object getWifiInfo() { return info; }
        public Object getScanResults() { return scans; }
    }

    public static class Info {
        final Object links;
        final int frequency;
        Info(Object links, int frequency) { this.links = links; this.frequency = frequency; }
        public Object getAffiliatedMloLinks() { return links; }
        public int getFrequency() { return frequency; }
    }

    public static class Link {
        final int state;
        final int band;
        Link(int state, int band) { this.state = state; this.band = band; }
        public int getState() { return state; }
        public int getBand() { return band; }
    }

    public static class LegacyScan {
        public int frequency;
        LegacyScan(int frequency) { this.frequency = frequency; }
    }

    public static class Scan extends LegacyScan {
        final int band;
        Scan(int band, int frequency) { super(frequency); this.band = band; }
        public int getBand() { return band; }
    }

    public static final class BrokenBandLink extends Link {
        BrokenBandLink() { super(3, 8); }
        @Override public int getBand() { throw new IllegalStateException("band unavailable"); }
    }
    public static final class IdleBrokenBandLink extends Link {
        IdleBrokenBandLink() { super(2, 8); }
        @Override public int getBand() { throw new IllegalStateException("inactive band unavailable"); }
    }
    public static final class BrokenStateLink extends Link {
        BrokenStateLink() { super(3, 8); }
        @Override public int getState() { throw new IllegalStateException("state unavailable"); }
    }
    public static final class BrokenInfoEntry extends Entry {
        BrokenInfoEntry() { super(2, null, Arrays.asList(scan(8))); }
        @Override public Object getWifiInfo() { throw new IllegalStateException("info unavailable"); }
    }
    public static final class BrokenMloInfo extends Info {
        BrokenMloInfo() { super(null, 6115); }
        @Override public Object getAffiliatedMloLinks() { throw new IllegalStateException("MLO unavailable"); }
    }
    public static final class BrokenFrequencyInfo extends Info {
        BrokenFrequencyInfo() { super(null, 6115); }
        @Override public int getFrequency() { throw new IllegalStateException("frequency unavailable"); }
    }
    public static final class ConnectedNoScanEntry extends Entry {
        ConnectedNoScanEntry() { super(2, info(null, 6115), null); }
        @Override public Object getScanResults() { throw new AssertionError("connected read scans"); }
    }
    public static final class DisconnectedNoInfoEntry extends Entry {
        DisconnectedNoInfoEntry() { super(0, null, Arrays.asList(scan(8))); }
        @Override public Object getWifiInfo() { throw new AssertionError("disconnected read WifiInfo"); }
    }
    public static final class BrokenConnectedStateEntry extends Entry {
        BrokenConnectedStateEntry() { super(0, null, Arrays.asList(scan(8))); }
        @Override public int getConnectedState() { throw new IllegalStateException("state unavailable"); }
    }
    public static final class BrokenScan extends Scan {
        BrokenScan() { super(8, 6115); }
        @Override public int getBand() { throw new IllegalStateException("band unavailable"); }
    }
    public static final class BrokenScansEntry extends Entry {
        BrokenScansEntry() { super(0, null, null); }
        @Override public Object getScanResults() { throw new IllegalStateException("scans unavailable"); }
    }
}
