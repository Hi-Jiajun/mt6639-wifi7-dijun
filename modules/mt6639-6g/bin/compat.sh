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
    profile_row=$(awk -v release="$1" '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        NF != 4 { invalid=1; next }
        $1 !~ /^[a-z0-9][a-z0-9-]*$/ || $2 !~ /^[a-zA-Z0-9._+-]+$/ { invalid=1; next }
        length($3) != 64 || $3 ~ /[^0-9a-f]/ ||
        length($4) != 64 || $4 ~ /[^0-9a-f]/ { invalid=1; next }
        seen_kernel[$2]++ || seen_id[$1]++ { invalid=1; next }
        $2 == release { row=$0; found++ }
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
