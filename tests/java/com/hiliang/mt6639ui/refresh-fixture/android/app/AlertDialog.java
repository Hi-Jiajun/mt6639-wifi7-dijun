package android.app;

import android.content.Context;
import android.content.DialogInterface;

/** Host picker boundary; tests explicitly press the positive button. */
public final class AlertDialog {
    private static DialogInterface.OnClickListener positive;
    private static DialogInterface.OnClickListener choice;
    private static CharSequence[] lastItems;

    public static CharSequence[] items() {
        if (lastItems == null) throw new AssertionError("picker is not open");
        return lastItems;
    }

    public static void confirm() {
        confirm(0);
    }

    public static void confirm(int index) {
        if (positive == null || choice == null) throw new AssertionError("picker is not open");
        DialogInterface.OnClickListener listener = positive;
        DialogInterface.OnClickListener selector = choice;
        positive = null;
        choice = null;
        lastItems = null;
        selector.onClick(null, index);
        listener.onClick(null, 0);
    }
    public static final class Builder {
        public Builder(Context context) { }
        public Builder setTitle(CharSequence title) { return this; }
        public Builder setSingleChoiceItems(CharSequence[] items, int selected,
                DialogInterface.OnClickListener listener) {
            lastItems = items;
            choice = listener;
            return this;
        }
        public Builder setNegativeButton(CharSequence label,
                DialogInterface.OnClickListener listener) { return this; }
        public Builder setPositiveButton(CharSequence label,
                DialogInterface.OnClickListener listener) {
            positive = listener;
            return this;
        }
        public AlertDialog show() { return new AlertDialog(); }
    }
}
