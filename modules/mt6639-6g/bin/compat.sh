#!/system/bin/sh
# Shared, read-only tuple selection keyed by (device, kernel-release).
# No config evaluation or release wildcards; hash verification remains mandatory.
select_runtime_profile() {
    COMPAT_PROFILE=unsupported
    COMPAT_ERROR=kernel_mismatch
    KERNEL_GUARD=unsupported
    STOCK_CFG_SHA=unsupported
    STOCK_KO_SHA=unsupported
    device=$1
    release=$2
    table=$3
    observed=${4:-}
    [ -f "$table" ] && [ ! -L "$table" ] || {
        COMPAT_ERROR=compatibility_table_invalid
        return 3
    }
    # Additional hashes on the same row are reviewed driver variants for the
    # same (device, kernel, config) tuple. Select the observed variant so
    # backup and restoration keep checking that exact file.
    profile_row=$(awk -v device="$device" -v release="$release" -v observed="$observed" '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        NF < 5 { invalid=1; next }
        $1 !~ /^[a-z0-9][a-z0-9-]*$/ || $3 !~ /^[a-zA-Z0-9._+-]+$/ { invalid=1; next }
        length($4) != 64 || $4 ~ /[^0-9a-f]/ { invalid=1; next }
        {
            n_devs = split($2, devs, ",")
            if (n_devs < 1) { invalid=1; next }
            delete row_devs
            dev_match = 0
            for (d = 1; d <= n_devs; d++) {
                if (devs[d] !~ /^(dijun|violin|jinghu)$/ || row_devs[devs[d]]++) invalid=1
                if (seen_dev_kernel[devs[d], $3]++) invalid=1
                if (devs[d] == device) dev_match = 1
            }
            selected=$5
            for (i=5; i<=NF; i++) {
                if (length($i) != 64 || $i ~ /[^0-9a-f]/ || seen_hash[$1, $i]++) invalid=1
                if ($i == observed) selected=$i
            }
        }
        seen_id[$1]++ { invalid=1; next }
        dev_match && $3 == release { row=$1 " " $3 " " $4 " " selected; found++ }
        END {
            if (invalid) exit 4
            if (device !~ /^(dijun|violin|jinghu)$/) exit 5
            if (found != 1) exit 3
            print row
        }
    ' "$table")
    profile_rc=$?
    case "$profile_rc" in
        0) ;;
        3) return 3 ;;
        5) COMPAT_ERROR=wrong_device; return 3 ;;
        *) COMPAT_ERROR=compatibility_table_invalid; return 3 ;;
    esac
    read -r COMPAT_PROFILE KERNEL_GUARD STOCK_CFG_SHA STOCK_KO_SHA <<EOF
$profile_row
EOF
    COMPAT_ERROR=
    return 0
}
