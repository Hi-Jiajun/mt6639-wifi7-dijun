package com.hiliang.mt6639ui;

import android.content.Context;
import android.content.res.Configuration;
import android.content.res.Resources;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PorterDuff;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.Drawable;
import android.provider.Settings;
import android.view.View;
import android.widget.ImageView;
import android.widget.TextView;

import java.lang.reflect.Field;
import java.util.HashMap;
import java.util.Map;
import java.util.WeakHashMap;

final class BadgeRenderer {
    private static final Map<String, Bitmap> CACHE = new HashMap<>();
    private static final Map<View, AppliedRow> ROWS = new WeakHashMap<>();
    private static final BandResolver.Reader READER = (receiver, method) ->
            de.robv.android.xposed.XposedHelpers.callMethod(receiver, method);

    private static ClassLoader settingsLoader;
    private static int idWifiBand = -1;
    private static int drawable5g = -1;
    private static int drawable5gConnected = -1;
    private static int drawable24g = -1;
    private static int drawable24gConnected = -1;
    private static int colorNormal = -1;
    private static int colorConnected = -1;
    private static int renderCount;
    private static Boolean lastEnabled;
    private static volatile boolean ready;

    private BadgeRenderer() {
    }

    private static final class AppliedRow {
        final RowState original;
        final RowState written;
        AppliedRow(RowState original, RowState written) {
            this.original = original;
            this.written = written;
        }
    }

    private static View itemView(Object holder) {
        if (holder == null) return null;
        Object view = de.robv.android.xposed.XposedHelpers.getObjectField(holder, "itemView");
        return view instanceof View ? (View) view : null;
    }

    static void beforeBind(Object holder) {
        View view = itemView(holder);
        AppliedRow previous = ROWS.get(view);
        if (previous != null) {
            previous.original.restoreOwned(previous.written);
            ROWS.remove(view);
        }
    }

    static void init(ClassLoader loader) {
        ready = false;
        settingsLoader = loader;
        try {
            idWifiBand = resourceId("com.android.settings.R$id", "wifi_band");
            drawable5g = resourceId(
                    "com.android.settings.R$drawable",
                    "band_wifi_5g"
            );
            drawable5gConnected = resourceId(
                    "com.android.settings.R$drawable",
                    "band_wifi_5g_connected"
            );
            drawable24g = resourceId(
                    "com.android.settings.R$drawable",
                    "band_wifi_24g"
            );
            drawable24gConnected = resourceId(
                    "com.android.settings.R$drawable",
                    "band_wifi_24g_connected"
            );
            colorNormal = resourceId(
                    "com.android.settings.R$color",
                    "wifi_signal_icon_color"
            );
            colorConnected = resourceId(
                    "com.android.settings.R$color",
                    "wifi_signal_icon_connected_color"
            );
            if (idWifiBand < 0 || drawable5g < 0 || drawable5gConnected < 0
                    || drawable24g < 0 || drawable24gConnected < 0
                    || colorNormal < 0 || colorConnected < 0) {
                throw new IllegalStateException("missing resource id");
            }
            ready = true;
            MainHook.log("resource ids loaded");
        } catch (Throwable t) {
            MainHook.log("resource lookup FAIL: " + t);
        }
    }

    static void apply(Object preference, Object holder, boolean accessPointPath) {
        if (!ready || holder == null || preference == null || settingsLoader == null) {
            return;
        }

        View itemView = itemView(holder);
        if (itemView == null) return;
        // A failed before-bind cleanup must not become the next OEM baseline.
        if (ROWS.containsKey(itemView)) return;
        View bandView = itemView.findViewById(idWifiBand);
        if (!(bandView instanceof ImageView)) {
            return;
        }
        ImageView image = (ImageView) bandView;

        Context context = (Context) de.robv.android.xposed.XposedHelpers.callMethod(
                preference,
                "getContext"
        );
        boolean enabled = UiPolicy.enabled(Settings.System.getString(context.getContentResolver(), UiPolicy.KEY));
        if (lastEnabled == null || lastEnabled != enabled) {
            MainHook.log("badges enabled=" + enabled);
            lastEnabled = enabled;
        }
        if (!enabled) return;
        BandResolver.Result bands = collectBands(preference, accessPointPath);
        if (!bands.has6()) return;

        String label = bands.label();
        boolean connected = bands.connected;
        Drawable badge = badge(context, connected, label);
        View title = itemView.findViewById(android.R.id.title);
        if (title == null) {
            title = itemView.findViewById(android.R.id.text1);
        }
        TextView textTitle = title instanceof TextView ? (TextView) title : null;
        RowState original = RowState.capture(image, textTitle);
        try {
            image.setImageDrawable(badge);
            image.setVisibility(View.VISIBLE);
            image.setContentDescription(label);
            if (textTitle != null) {
                clearOemBandCompound(context, textTitle);
                float density = context.getResources().getDisplayMetrics().density;
                int endPadding = badge.getIntrinsicWidth() + Math.round(5.5f * density);
                textTitle.setPaddingRelative(textTitle.getPaddingStart(), textTitle.getPaddingTop(),
                        Math.max(textTitle.getPaddingEnd(), endPadding), textTitle.getPaddingBottom());
            }
            ROWS.put(itemView, new AppliedRow(original, RowState.capture(image, textTitle)));
        } catch (Throwable failure) {
            try { original.restore(); }
            catch (Throwable rollbackFailure) { failure.addSuppressed(rollbackFailure); }
            throw failure;
        }

        if (renderCount < 20) {
            renderCount++;
            MainHook.log(
                    "render " + label + " "
                            + preference.getClass().getSimpleName()
                            + " connected=" + connected
            );
        }
    }

    private static BandResolver.Result collectBands(Object preference, boolean accessPointPath) {
        if (accessPointPath) {
            // Legacy connected AccessPoint lacks a trustworthy ACTIVE link set.
            if (!Boolean.FALSE.equals(call(preference, "isConnected"))
                    || !Boolean.FALSE.equals(call(preference, "isSlaveConnected")))
                return new BandResolver.Result(false, 0);
            Object ap = call(preference, "getAccessPoint");
            return BandResolver.disconnectedScans(call(ap, "getScanResults"), READER);
        }
        return BandResolver.resolve(call(preference, "getWifiEntry"), READER);
    }

    private static Drawable badge(Context context, boolean connected, String label) {
        Resources resources = context.getResources();
        int nightMode = resources.getConfiguration().uiMode
                & Configuration.UI_MODE_NIGHT_MASK;
        String key = label + "|" + connected + "|" + nightMode + "|"
                + resources.getDisplayMetrics().densityDpi;
        Bitmap cached = CACHE.get(key);
        if (cached != null) {
            return new BitmapDrawable(resources, cached);
        }

        int baseId = label.length() > 2
                ? (connected ? drawable24gConnected : drawable24g)
                : (connected ? drawable5gConnected : drawable5g);
        Drawable base = context.getDrawable(baseId);
        if (base == null) {
            throw new IllegalStateException("missing OEM badge drawable");
        }
        int width = base.getIntrinsicWidth();
        int height = base.getIntrinsicHeight();
        Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        bitmap.setDensity(resources.getDisplayMetrics().densityDpi);
        Canvas canvas = new Canvas(bitmap);
        base.setBounds(0, 0, width, height);
        base.draw(canvas);

        float density = resources.getDisplayMetrics().density;
        Path interior = new Path();
        float inset = 2f * density;
        interior.addRoundRect(
                new RectF(inset, inset, width - inset, height - inset),
                2.5f * density,
                2.5f * density,
                Path.Direction.CW
        );
        canvas.save();
        canvas.clipPath(interior);
        canvas.drawColor(Color.TRANSPARENT, PorterDuff.Mode.CLEAR);
        canvas.restore();

        boolean multi = label.length() > 2;
        int viewWidth = multi ? 48 : 22;
        int viewHeight = multi ? 15 : 14;
        float scaleX = (float) width / viewWidth;
        float scaleY = (float) height / viewHeight;
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(context.getColor(connected ? colorConnected : colorNormal));
        paint.setAlpha(102);
        paint.setTypeface(miSansTypeface());
        paint.setTextSize(10.17f * ((scaleX + scaleY) / 2f));

        Path glyphs = new Path();
        paint.getTextPath(label, 0, label.length(), 0, 11.05f * scaleY, glyphs);
        RectF bounds = new RectF();
        glyphs.computeBounds(bounds, true);
        glyphs.offset(width / 2f - (bounds.left + bounds.right) / 2f, 0);
        canvas.drawPath(glyphs, paint);

        CACHE.put(key, bitmap);
        return new BitmapDrawable(resources, bitmap);
    }

    private static void clearOemBandCompound(Context context, TextView title) {
        Drawable[] drawables = title.getCompoundDrawablesRelative();
        boolean changed = false;
        for (int i = 0; i < drawables.length; i++) {
            if (isOemBandDrawable(context, drawables[i])) {
                drawables[i] = null;
                changed = true;
            }
        }
        if (changed) {
            title.setCompoundDrawablesRelative(
                    drawables[0],
                    drawables[1],
                    drawables[2],
                    drawables[3]
            );
        }
    }

    private static boolean isOemBandDrawable(Context context, Drawable candidate) {
        if (candidate == null) {
            return false;
        }
        Drawable.ConstantState state = candidate.getConstantState();
        if (state == null) {
            return false;
        }
        int[] ids = new int[] {
                drawable5g,
                drawable5gConnected,
                drawable24g,
                drawable24gConnected
        };
        for (int id : ids) {
            Drawable reference = context.getDrawable(id);
            if (reference != null && state.equals(reference.getConstantState())) {
                return true;
            }
        }
        return false;
    }

    private static Typeface miSansTypeface() {
        try {
            return new Typeface.Builder("/system/fonts/MiSansLatinVF.ttf")
                    .setWeight(425)
                    .setFontVariationSettings("'wght' 425")
                    .build();
        } catch (Throwable t) {
            return Typeface.create("sans-serif", Typeface.NORMAL);
        }
    }

    private static int resourceId(String className, String field) throws Exception {
        Class<?> cls = Class.forName(className, false, settingsLoader);
        Field f = cls.getDeclaredField(field);
        return f.getInt(null);
    }

    private static Object call(Object receiver, String method) {
        if (receiver == null) {
            return null;
        }
        try {
            return de.robv.android.xposed.XposedHelpers.callMethod(receiver, method);
        } catch (Throwable t) {
            return null;
        }
    }

}
