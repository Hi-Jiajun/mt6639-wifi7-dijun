package com.hiliang.mt6639ui;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.os.Bundle;
import android.view.View;
import android.widget.ImageView;
import android.widget.TextView;

/** Exercises production restoration on real Android Views, without hooking Settings. */
public final class RowStateInstrumentation extends Instrumentation {
    private int passed;

    @Override public void onCreate(Bundle args) {
        super.onCreate(args);
        start();
    }

    @Override public void onStart() {
        final Bundle result = new Bundle();
        try {
            final Throwable[] error = new Throwable[1];
            runOnMainSync(new Runnable() {
                @Override public void run() {
                    try { exercise(); } catch (Throwable failure) { error[0] = failure; }
                }
            });
            if (error[0] != null) throw new AssertionError(error[0]);
            result.putString("stream", "RESULT=PASS android_view_cases=" + passed + "\n");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable failure) {
            result.putString("stream", "RESULT=FAIL " + failure + "\n");
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private void check(boolean ok, String name) {
        if (!ok) throw new AssertionError(name);
        passed++;
    }

    private void exercise() {
        Context context = getTargetContext();
        ImageView image = new ImageView(context);
        TextView title = new TextView(context);
        Drawable original = new ColorDrawable(Color.BLUE);
        Drawable overlay = new ColorDrawable(Color.RED);
        Drawable lock = new ColorDrawable(Color.GREEN);
        image.setImageDrawable(original);
        image.setVisibility(View.INVISIBLE);
        image.setContentDescription("OEM description");
        title.setPaddingRelative(7, 11, 13, 17);
        title.setCompoundDrawablesRelative(null, null, lock, null);
        RowState before = RowState.capture(image, title);

        image.setImageDrawable(overlay);
        image.setVisibility(View.VISIBLE);
        image.setContentDescription("6G");
        title.setPaddingRelative(7, 11, 90, 17);
        title.setCompoundDrawablesRelative(null, null, null, null);
        before.restore();
        check(image.getDrawable() == original, "rollback drawable");
        check(image.getVisibility() == View.INVISIBLE, "rollback visibility");
        check("OEM description".contentEquals(image.getContentDescription()), "rollback a11y");
        check(title.getPaddingStart() == 7 && title.getPaddingTop() == 11
                && title.getPaddingEnd() == 13 && title.getPaddingBottom() == 17,
                "rollback all OEM padding");
        check(title.getCompoundDrawablesRelative()[2] == lock, "rollback compound");

        image.setImageDrawable(overlay);
        image.setVisibility(View.VISIBLE);
        image.setContentDescription("6G");
        title.setPaddingRelative(7, 11, 90, 17);
        title.setCompoundDrawablesRelative(null, null, null, null);
        RowState written = RowState.capture(image, title);
        Drawable external = new ColorDrawable(Color.YELLOW);
        image.setImageDrawable(external);
        image.setContentDescription("new OEM description");
        title.setPaddingRelative(20, 21, 22, 23);
        before.restoreOwned(written);
        check(image.getDrawable() == external, "preserve external drawable");
        check("new OEM description".contentEquals(image.getContentDescription()), "preserve external a11y");
        check(title.getPaddingStart() == 20 && title.getPaddingEnd() == 22,
                "preserve external padding");
        check(image.getVisibility() == View.INVISIBLE, "restore still owned visibility");
        check(title.getCompoundDrawablesRelative()[2] == lock, "restore still owned compound");

        title.setPaddingRelative(7, 11, 13, 17);
        RowState partialBefore = RowState.capture(image, title);
        title.setPaddingRelative(7, 11, 90, 17);
        RowState partialWritten = RowState.capture(image, title);
        title.setPaddingRelative(7, 21, 90, 17);
        partialBefore.restoreOwned(partialWritten);
        check(title.getPaddingTop() == 21 && title.getPaddingEnd() == 13,
                "external top change must not strand module end padding");

        title.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        title.setPaddingRelative(3, 5, 19, 7);
        RowState rtl = RowState.capture(image, title);
        title.setPaddingRelative(3, 5, 100, 7);
        rtl.restore();
        check(title.getPaddingStart() == 3 && title.getPaddingEnd() == 19,
                "RTL keeps logical start/end");
        check(title.getPaddingTop() == 5 && title.getPaddingBottom() == 7,
                "RTL keeps vertical padding");
        RowState.capture(image, null).restore();
        check(image.getDrawable() == external, "missing title supported");

        check(UiPolicy.enabled(null), "absent switch keeps installed compatibility");
        check(UiPolicy.enabled("1"), "enable switch");
        check(!UiPolicy.enabled("0"), "disable switch");
        check(!UiPolicy.enabled("garbage"), "malformed switch disables");
        check(!UiPolicy.enabled("2"), "unsupported switch disables");
    }
}
