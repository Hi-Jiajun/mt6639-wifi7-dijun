'use strict';

// Read-only UI prototype: append a [6G] marker to Wi-Fi rows whose
// AccessPoint has at least one real 6GHz ScanResult. This never changes
// Wi-Fi configuration, scan results, or connection state.
function is6GHz(freq) {
    return freq === 5935 || (freq >= 5955 && freq <= 7115);
}

Java.perform(function () {
    const AndroidR = Java.use('android.R$id');
    const TextView = Java.use('android.widget.TextView');

    const Iterable = Java.use('java.lang.Iterable');
    const ScanResult = Java.use('android.net.wifi.ScanResult');
    const sixGhzBand = ScanResult.WIFI_BAND_6_GHZ.value;

    function scanResultsHave6GHz(results) {
        if (!results) return false;
        try {
            // WifiEntry.getScanResults() returns a Set, while some older
            // preference paths return a List. Both implement Iterable.
            const iter = Java.cast(results, Iterable).iterator();
            while (iter.hasNext()) {
                const result = Java.cast(iter.next(), ScanResult);
                if (result.getBand() === sixGhzBand) return true;
            }
        } catch (e) {
            console.log('frida-settings-6g: cannot inspect scan results: ' + e);
        }
        return false;
    }

    function has6GHz(ap) {
        try {
            if (scanResultsHave6GHz(ap.getScanResults())) return true;
        } catch (_) {
            // Some saved/connected rows may not retain scanResults.
        }
        try {
            return is6GHz(ap.getFrequency());
        } catch (_) {
            return false;
        }
    }

    function decorate(className, getResults, getWifiInfo) {
        try {
            const Pref = Java.use(className);
            const original = Pref.onBindViewHolder.overload(
                'androidx.preference.PreferenceViewHolder'
            );
            const impl = function (holder) {
                original.call(this, holder);
                try {
                    const results = getResults(this);
                    let has6 = scanResultsHave6GHz(results);
                    if (!has6) {
                        const info = getWifiInfo(this);
                        if (info && is6GHz(info.getFrequency())) has6 = true;
                    }
                    if (!has6) return;

                    const itemView = holder.itemView.value;
                    let title = itemView.findViewById(AndroidR.title.value);
                    if (!title) {
                        title = itemView.findViewById(AndroidR.text1.value);
                    }
                    if (!title) return;
                    title = Java.cast(title, TextView);
                    const current = String(title.getText());
                    if (!current.endsWith(' [6G]')) {
                        const JString = Java.use('java.lang.String');
                        title.setText.overload('java.lang.CharSequence').call(
                            title,
                            JString.$new(current + ' [6G]')
                        );
                    }
                } catch (e) {
                    console.log('frida-settings-6g: ' + className + ': ' + e + '\n' + e.stack);
                }
            };
            Pref.onBindViewHolder.overload(
                'androidx.preference.PreferenceViewHolder'
            ).implementation = impl;
            console.log('frida-settings-6g: hooked ' + className);
        } catch (e) {
            console.log('frida-settings-6g: cannot hook ' + className + ': ' + e);
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
            if (!entry) return null;
            return entry.getScanResults();
        },
        function (self) {
            const entry = self.getWifiEntry();
            if (!entry) return null;
            return entry.getWifiInfo();
        }
    );
});
