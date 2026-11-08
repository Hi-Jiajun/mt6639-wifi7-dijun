package com.hiliang.mt6639ui;

final class BandResolver {
    static final int BAND_24 = 1;
    static final int BAND_5 = 2;
    static final int BAND_6 = 8;
    static final int ACTIVE = 3;
    static final int CONNECTED = 2;

    interface Reader {
        Object call(Object receiver, String method) throws Exception;
    }

    static final class Result {
        final boolean connected;
        final int mask;

        Result(boolean connected, int mask) {
            this.connected = connected;
            this.mask = mask;
        }

        String label() {
            StringBuilder label = new StringBuilder();
            if ((mask & BAND_24) != 0) {
                label.append("2.4G");
            }
            if ((mask & BAND_5) != 0) {
                if (label.length() > 0) label.append('/');
                label.append("5G");
            }
            if (has6()) {
                if (label.length() > 0) label.append('/');
                label.append("6G");
            }
            return label.toString();
        }

        boolean has6() { return (mask & BAND_6) != 0; }
    }

    static Result resolve(Object entry, Reader reader) {
        if (entry == null || reader == null) return new Result(false, 0);
        final int state;
        try {
            state = integer(reader.call(entry, "getConnectedState"));
        } catch (Exception | LinkageError e) {
            return new Result(false, 0);
        }
        if (state == CONNECTED) {
            try {
                return new Result(true, connectedMask(reader.call(entry, "getWifiInfo"), reader));
            } catch (Exception | LinkageError e) {
                return new Result(true, 0);
            }
        }
        // Only the documented DISCONNECTED state authorizes scan-based badges.
        if (state != 0) return new Result(false, 0);
        try {
            return new Result(false, scanMask(reader.call(entry, "getScanResults"), reader));
        } catch (Exception | LinkageError e) {
            return new Result(false, 0);
        }
    }

    static Result disconnectedScans(Object scans, Reader reader) {
        try { return new Result(false, scanMask(scans, reader)); }
        catch (Exception | LinkageError error) { return new Result(false, 0); }
    }

    private static int connectedMask(Object info, Reader reader) throws Exception {
        if (info == null) return 0;
        Object links = reader.call(info, "getAffiliatedMloLinks");
        if (links == null) return frequencyBand(integer(reader.call(info, "getFrequency")));
        if (!(links instanceof Iterable)) return 0;
        boolean nonempty = false;
        int mask = 0;
        for (Object link : (Iterable<?>) links) {
            nonempty = true;
            int state = integer(reader.call(link, "getState"));
            if (state <= 0 || state > ACTIVE) return 0;
            if (state == ACTIVE) {
                int band = supportedBand(integer(reader.call(link, "getBand")));
                // A partial union would falsely claim the complete active link set.
                if (band == 0) return 0;
                mask |= band;
            }
        }
        if (nonempty) return mask;
        return frequencyBand(integer(reader.call(info, "getFrequency")));
    }

    private static int scanMask(Object scans, Reader reader) {
        if (!(scans instanceof Iterable)) return 0;
        int mask = 0;
        for (Object scan : (Iterable<?>) scans) {
            if (scan == null) continue;
            try {
                mask |= supportedBand(integer(reader.call(scan, "getBand")));
            } catch (NoSuchMethodException | NoSuchMethodError e) {
                // Older ScanResult APIs expose frequency as a public field.
                try {
                    mask |= frequencyBand(integer(scan.getClass().getField("frequency").get(scan)));
                } catch (Exception | LinkageError ignored) {
                    // Individual unknown scan records do not invalidate known records.
                }
            } catch (Exception | LinkageError ignored) {
                // A failed getBand call is not evidence that the API is absent.
            }
        }
        return mask;
    }

    private static int integer(Object value) {
        return value instanceof Number ? ((Number) value).intValue() : -1;
    }

    private static int supportedBand(int band) {
        return band == BAND_24 || band == BAND_5 || band == BAND_6 ? band : 0;
    }

    private static int frequencyBand(int frequency) {
        if (frequency >= 2400 && frequency < 2500) return BAND_24;
        if (frequency >= 4900 && frequency < 5925) return BAND_5;
        if (frequency == 5935 || (frequency >= 5955 && frequency <= 7115)) return BAND_6;
        return 0;
    }
}
