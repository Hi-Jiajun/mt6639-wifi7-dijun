package com.hiliang.mt6639ui;

import android.graphics.drawable.Drawable;
import android.text.TextUtils;
import android.view.View;
import android.widget.ImageView;
import android.widget.TextView;
import java.lang.ref.WeakReference;

/** State boundary for one synchronous OEM bind. */
final class RowState {
    private final WeakReference<ImageView> image;
    private final WeakReference<TextView> title;
    private final Drawable drawable;
    private final int visibility;
    private final CharSequence description;
    private final int start, top, end, bottom;
    private final boolean relativePadding;
    private final Drawable[] compound;

    private RowState(ImageView image, TextView title) {
        this.image = new WeakReference<>(image);
        this.title = new WeakReference<>(title);
        drawable = image.getDrawable();
        visibility = image.getVisibility();
        description = image.getContentDescription();
        relativePadding = title != null && title.isPaddingRelative();
        start = title == null ? 0 : title.getPaddingStart();
        end = title == null ? 0 : title.getPaddingEnd();
        top = title == null ? 0 : title.getPaddingTop();
        bottom = title == null ? 0 : title.getPaddingBottom();
        compound = title == null ? null : title.getCompoundDrawablesRelative().clone();
    }

    static RowState capture(ImageView image, TextView title) { return new RowState(image, title); }
    void restore() { restoreOwned(null); }

    /** Null means immediate rollback; otherwise restore only fields we still own. */
    void restoreOwned(RowState written) {
        ImageView target = image.get();
        TextView text = title.get();
        RuntimeException[] failure = new RuntimeException[1];
        if (target != null) {
            attempt(failure, () -> {
                if (written == null || target.getDrawable() == written.drawable)
                    target.setImageDrawable(drawable);
            });
            attempt(failure, () -> {
                if (written == null || target.getVisibility() == written.visibility)
                    target.setVisibility(visibility);
            });
            attempt(failure, () -> {
                if (written == null || TextUtils.equals(target.getContentDescription(), written.description))
                    target.setContentDescription(description);
            });
        }
        if (text != null) {
            attempt(failure, () -> {
                int restoredStart = written == null || text.getPaddingStart() == written.start
                        ? start : text.getPaddingStart();
                int restoredTop = written == null || text.getPaddingTop() == written.top
                        ? top : text.getPaddingTop();
                int restoredEnd = written == null || text.getPaddingEnd() == written.end
                        ? end : text.getPaddingEnd();
                int restoredBottom = written == null || text.getPaddingBottom() == written.bottom
                        ? bottom : text.getPaddingBottom();
                boolean mode = written == null || text.isPaddingRelative() == written.relativePadding
                        ? relativePadding : text.isPaddingRelative();
                if (mode) {
                    text.setPaddingRelative(restoredStart, restoredTop, restoredEnd, restoredBottom);
                } else {
                    boolean rtl = text.getLayoutDirection() == View.LAYOUT_DIRECTION_RTL;
                    text.setPadding(rtl ? restoredEnd : restoredStart, restoredTop,
                            rtl ? restoredStart : restoredEnd, restoredBottom);
                }
            });
            attempt(failure, () -> {
                Drawable[] current = text.getCompoundDrawablesRelative();
                boolean changed = false;
                for (int i = 0; i < current.length; i++) {
                    if ((written == null || current[i] == written.compound[i])
                            && current[i] != compound[i]) {
                        current[i] = compound[i];
                        changed = true;
                    }
                }
                if (changed) text.setCompoundDrawablesRelative(current[0], current[1], current[2], current[3]);
            });
        }
        if (failure[0] != null) throw failure[0];
    }

    private static void attempt(RuntimeException[] failure, Runnable operation) {
        try { operation.run(); } catch (RuntimeException error) {
            if (failure[0] == null) failure[0] = error;
            else failure[0].addSuppressed(error);
        }
    }
}
