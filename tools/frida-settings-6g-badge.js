'use strict';

// Prototype badge renderer for HyperOS Settings.
//
// The stock list renders a 22x14dp vector in R.id.wifi_band for 2.4GHz and
// 5GHz. This hook uses that same slot for a 6G badge on rows backed by real
// 6GHz scan or connection data.

(function waitForJava() {
    if (typeof Java === 'undefined' || typeof Java.perform !== 'function') {
        setTimeout(waitForJava, 10);
        return;
    }

Java.perform(function () {
    const SettingsId = Java.use('com.android.settings.R$id');
    const SettingsDrawable = Java.use('com.android.settings.R$drawable');
    const AndroidR = Java.use('android.R$id');
    const SettingsColor = Java.use('com.android.settings.R$color');
    const ScanResult = Java.use('android.net.wifi.ScanResult');
    const MloLink = Java.use('android.net.wifi.MloLink');
    const Iterable = Java.use('java.lang.Iterable');
    const ImageView = Java.use('android.widget.ImageView');
    const TextView = Java.use('android.widget.TextView');
    const Bitmap = Java.use('android.graphics.Bitmap');
    const BitmapConfig = Java.use('android.graphics.Bitmap$Config');
    const BitmapDrawable = Java.use('android.graphics.drawable.BitmapDrawable');
    const Canvas = Java.use('android.graphics.Canvas');
    const Color = Java.use('android.graphics.Color');
    const Paint = Java.use('android.graphics.Paint');
    const PaintStyle = Java.use('android.graphics.Paint$Style');
    const Path = Java.use('android.graphics.Path');
    const PathDirection = Java.use('android.graphics.Path$Direction');
    const PorterDuffMode = Java.use('android.graphics.PorterDuff$Mode');
    const RectF = Java.use('android.graphics.RectF');
    const Typeface = Java.use('android.graphics.Typeface');
    const TypefaceBuilder = Java.use('android.graphics.Typeface$Builder');
    const JString = Java.use('java.lang.String');

    const BAND_24 = ScanResult.WIFI_BAND_24_GHZ.value;
    const BAND_5 = ScanResult.WIFI_BAND_5_GHZ.value;
    const BAND_6 = ScanResult.WIFI_BAND_6_GHZ.value;
    const CONNECTED = 2;
    const BADGE_COLOR_ID = SettingsColor.wifi_signal_icon_color.value;
    const CONNECTED_BADGE_COLOR_ID =
        SettingsColor.wifi_signal_icon_connected_color.value;

    function is6GHz(freq) {
        return freq === 5935 || (freq >= 5955 && freq <= 7115);
    }

    function is5GHz(freq) {
        return freq >= 4900 && freq < 5925;
    }

    function is24GHz(freq) {
        return freq >= 2400 && freq < 2500;
    }

    function collectBands(results, bands) {
        if (!results) return;
        try {
            const iter = Java.cast(results, Iterable).iterator();
            while (iter.hasNext()) {
                const result = Java.cast(iter.next(), ScanResult);
                const band = result.getBand();
                if (band === BAND_24) bands.has24 = true;
                if (band === BAND_5) bands.has5 = true;
                if (band === BAND_6) bands.has6 = true;
            }
        } catch (e) {
            console.log('frida-settings-6g-badge: scan result read failed: ' + e);
        }
    }

    function addFrequency(bands, info) {
        if (!info) return;
        try {
            const freq = info.getFrequency();
            if (is24GHz(freq)) bands.has24 = true;
            if (is5GHz(freq)) bands.has5 = true;
            if (is6GHz(freq)) bands.has6 = true;
        } catch (_) {
            // Saved rows do not always retain a WifiInfo.
        }
    }

    function addBand(bands, band) {
        if (band === BAND_24) bands.has24 = true;
        if (band === BAND_5) bands.has5 = true;
        if (band === BAND_6) bands.has6 = true;
    }

    function collectInfoBands(info, bands) {
        if (!info) return false;
        let found = false;
        try {
            const links = info.getAffiliatedMloLinks();
            const iter = Java.cast(links, Iterable).iterator();
            while (iter.hasNext()) {
                const link = Java.cast(iter.next(), MloLink);
                if (link.getState() === MloLink.MLO_LINK_STATE_ACTIVE.value) {
                    addBand(bands, link.getBand());
                    found = true;
                }
            }
        } catch (_) {
            // MLO accessors are unavailable on older framework paths.
        }
        if (found) return true;
        try {
            const before = bands.has24 || bands.has5 || bands.has6;
            addFrequency(bands, info);
            return before || bands.has24 || bands.has5 || bands.has6;
        } catch (_) {
            return false;
        }
    }

    function labelFor(bands) {
        if (!bands.has6) return null;
        const parts = [];
        if (bands.has24) parts.push('2.4G');
        if (bands.has5) parts.push('5G');
        parts.push('6G');
        return parts.join('/');
    }

    function makeBadge(context, connected, label) {
        const resources = context.getResources();
        const metrics = resources.getDisplayMetrics();
        const density = metrics.density.value;
        const densityDpi = metrics.densityDpi.value;
        const multi = label.length > 2;
        const color = context.getColor(
            connected ? CONNECTED_BADGE_COLOR_ID : BADGE_COLOR_ID
        );
        const base = context.getDrawable(
            connected
                ? (multi
                    ? SettingsDrawable.band_wifi_24g_connected.value
                    : SettingsDrawable.band_wifi_5g_connected.value)
                : (multi
                    ? SettingsDrawable.band_wifi_24g.value
                    : SettingsDrawable.band_wifi_5g.value)
        );
        const width = base.getIntrinsicWidth();
        const height = base.getIntrinsicHeight();
        const bitmap = Bitmap.createBitmap(
            width,
            height,
            BitmapConfig.ARGB_8888.value
        );
        bitmap.setDensity(densityDpi);
        const canvas = Canvas.$new(bitmap);

        base.setBounds(0, 0, width, height);
        base.draw(canvas);

        const interior = Path.$new();
        const inset = 2 * density;
        interior.addRoundRect(
            RectF.$new(inset, inset, width - inset, height - inset),
            2.5 * density,
            2.5 * density,
            PathDirection.CW.value
        );
        canvas.save();
        canvas.clipPath(interior);
        canvas.drawColor(0, PorterDuffMode.CLEAR.value);
        canvas.restore();

        const text = Paint.$new(Paint.ANTI_ALIAS_FLAG.value);
        text.setStyle(PaintStyle.FILL.value);
        text.setColor(color);
        text.setAlpha(102);
        let typeface;
        try {
            const builder = TypefaceBuilder.$new(
                JString.$new('/system/fonts/MiSansLatinVF.ttf')
            );
            builder.setWeight(425);
            builder.setFontVariationSettings(JString.$new("'wght' 425"));
            typeface = builder.build();
        } catch (_) {
            try {
                typeface = Typeface.create(
                    Typeface.createFromFile(
                        JString.$new('/system/fonts/MiSansLatinVF.ttf')
                    ),
                    425,
                    false
                );
            } catch (_) {
                typeface = Typeface.create('sans-serif', 0);
            }
        }
        text.setTypeface(typeface);
        const viewWidth = multi ? 48 : 22;
        const viewHeight = multi ? 15 : 14;
        const scaleX = width / viewWidth;
        const scaleY = height / viewHeight;
        text.setTextSize(10.17 * ((scaleX + scaleY) / 2));
        const glyphPath = Path.$new();
        text.getTextPath(
            JString.$new(label),
            0,
            label.length,
            0,
            11.05 * scaleY,
            glyphPath
        );
        const bounds = RectF.$new();
        glyphPath.computeBounds(bounds, true);
        glyphPath.offset(
            (width / 2) - ((bounds.left.value + bounds.right.value) / 2),
            0
        );
        canvas.drawPath(glyphPath, text);

        bitmap.setDensity(densityDpi);
        return BitmapDrawable.$new(resources, bitmap);
    }

    function decorate(className, getResults, getWifiInfo) {
        try {
            const Pref = Java.use(className);
            const original = Pref.onBindViewHolder.overload(
                'androidx.preference.PreferenceViewHolder'
            );
            function applyToHolder(self, holder, verbose) {
                try {
                    const bands = { has24: false, has5: false, has6: false };
                    let connected = false;
                    try {
                        connected = self.getWifiEntry().getConnectedState() === CONNECTED;
                    } catch (_) {}
                    if (!connected) {
                        try {
                            connected = self.isConnected() || self.isSlaveConnected();
                        } catch (_) {}
                    }
                    const info = getWifiInfo ? getWifiInfo(self) : null;
                    if (!connected || !collectInfoBands(info, bands)) {
                        collectBands(getResults(self), bands);
                    }

                    const itemView = holder.itemView.value;
                    let bandView = itemView.findViewById(SettingsId.wifi_band.value);
                    if (!bandView) return false;
                    bandView = Java.cast(bandView, ImageView);
                    if (!bands.has6) {
                        bandView.setContentDescription(null);
                        return false;
                    }

                    const label = labelFor(bands);
                    if (!label) return false;
                    bandView.setImageDrawable(
                        makeBadge(self.getContext(), connected, label)
                    );
                    bandView.setVisibility(0);
                    bandView.setContentDescription(JString.$new(label));
                    bandView.invalidate();

                    let titleView = itemView.findViewById(AndroidR.title.value);
                    if (!titleView) {
                        titleView = itemView.findViewById(AndroidR.text1.value);
                    }
                    if (titleView) {
                        titleView = Java.cast(titleView, TextView);
                        const density = self.getContext().getResources()
                            .getDisplayMetrics().density.value;
                        const endPadding =
                            bandView.getDrawable().getIntrinsicWidth() +
                            Math.round(5.5 * density);
                        titleView.setPadding(0, 0, endPadding, 0);
                        titleView.requestLayout();
                    }
                    itemView.requestLayout();
                    if (verbose) {
                        console.log(
                            'frida-settings-6g-badge: ' + label + ' ' +
                            self.getClass().getName() + ' ' + self.getTitle()
                        );
                    }
                    return true;
                } catch (e) {
                    console.log(
                        'frida-settings-6g-badge: ' + className + ': ' +
                        e + '\n' + e.stack
                    );
                    return false;
                }
            }
            original.implementation = function (holder) {
                original.call(this, holder);
                applyToHolder(this, holder, true);
            };
            console.log('frida-settings-6g-badge: hooked ' + className);
        } catch (e) {
            console.log('frida-settings-6g-badge: cannot hook ' + className + ': ' + e);
        }
    }

    decorate(
        'com.android.settings.wifi.MiuiAccessPointPreference',
        function (self) { return self.getAccessPoint().getScanResults(); },
        null
    );
    decorate(
        'com.android.settings.wifi.MiuiWifiEntryPreference',
        function (self) {
            const entry = self.getWifiEntry();
            return entry ? entry.getScanResults() : null;
        },
        function (self) {
            const entry = self.getWifiEntry();
            return entry ? entry.getWifiInfo() : null;
        }
    );
    decorate(
        'com.android.settings.wifi.LongPressWifiEntryPreference',
        function (self) {
            const entry = self.getWifiEntry();
            return entry ? entry.getScanResults() : null;
        },
        function (self) {
            const entry = self.getWifiEntry();
            return entry ? entry.getWifiInfo() : null;
        }
    );
});
})();
