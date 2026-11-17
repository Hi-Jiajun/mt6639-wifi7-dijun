#!/system/bin/sh
# Shared, read-only tuple selection. No config evaluation or release wildcards.
# Hash verification remains mandatory at each stock-identity boundary.
select_runtime_profile() {
    COMPAT_PROFILE=unsupported
    COMPAT_ERROR=kernel_mismatch
    KERNEL_GUARD=unsupported
    STOCK_CFG_SHA=unsupported
    STOCK_KO_SHA=unsupported
    [ -f "$2" ] && [ ! -L "$2" ] || {
        COMPAT_ERROR=compatibility_table_invalid
        return 3
    }
    # Additional hashes on the same row are reviewed driver variants for the
    # same kernel/config. Select the observed variant so backup and restoration
    # keep checking that exact file, rather than accepting either at each step.
    profile_row=$(awk -v release="$1" -v observed="${3:-}" '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        NF < 4 { invalid=1; next }
        $1 !~ /^[a-z0-9][a-z0-9-]*$/ || $2 !~ /^[a-zA-Z0-9._+-]+$/ { invalid=1; next }
        length($3) != 64 || $3 ~ /[^0-9a-f]/ { invalid=1; next }
        {
            selected=$4
            for (i=4; i<=NF; i++) {
                if (length($i) != 64 || $i ~ /[^0-9a-f]/ || seen_hash[$1, $i]++) invalid=1
                if ($i == observed) selected=$i
            }
        }
        seen_kernel[$2]++ || seen_id[$1]++ { invalid=1; next }
        $2 == release { row=$1 " " $2 " " $3 " " selected; found++ }
        END {
            if (invalid) exit 4
            if (found != 1) exit 3
            print row
        }
    ' "$2")
    profile_rc=$?
    case "$profile_rc" in
        0) ;;
        3) return 3 ;;
        *) COMPAT_ERROR=compatibility_table_invalid; return 3 ;;
    esac
    read -r COMPAT_PROFILE KERNEL_GUARD STOCK_CFG_SHA STOCK_KO_SHA <<EOF
$profile_row
EOF
    COMPAT_ERROR=
    return 0
}
