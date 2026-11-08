/* Uses real mount executor and runner. Only external Android Binder operations
 * are replaced by a test executable, confined to fresh fixture files. */
#define MT_MOUNT_HELPERS_ONLY
#include "mount_txn_integration.c"
static int copy_backend(const char *src, const char *dst) {
    char b[4096]; int in = open(src, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    int out = open(dst, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0700);
    ssize_t n;
    if (in < 0 || out < 0) return 0;
    while ((n = read(in, b, sizeof b)) > 0) if (write(out, b, (size_t)n) != n) return 0;
    close(in); fsync(out); close(out); return n == 0;
}
static int text_file(const char *dir, const char *name, const char *text) {
    char path[400]; int fd, ok;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return 0;
    ok = write(fd, text, strlen(text)) == (ssize_t)strlen(text) && !fsync(fd);
    close(fd); return ok;
}
static int content_is(const char *dir, const char *name, const char *expected) {
    char path[400], bytes[4096]; int fd; ssize_t n;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    n = read(fd, bytes, sizeof bytes); close(fd);
    return n == (ssize_t)strlen(expected) && !memcmp(bytes, expected, (size_t)n);
}
static int case_wlan(const char *base, const char *backend, int which) {
    char dir[300], target[340], helper[340], out[4096], logpath[340];
    int pid, status = 0, rc, i;
    const char *actions = which == 2 ?
        "get-scan\nget-wifi\nget-override\nget-country\nscan-off\ncountry-us\nwifi-off\nwifi-on\nscan-off\ncountry-cn\nwifi-off\nwifi-on\ncountry-auto\nscan-off\nscan-now\nget-scan\nget-wifi\nget-link\nget-ipv4\n" :
        (which == 1 ?
        "get-scan\nget-wifi\nget-override\nget-country\nscan-off\ncountry-us\nwifi-off\nscan-off\ncountry-cn\nwifi-off\nwifi-on\ncountry-auto\nscan-on\nscan-now\nget-scan\nget-wifi\nget-link\nget-ipv4\n" :
        "get-scan\nget-wifi\nget-override\nget-country\nscan-off\ncountry-us\nwifi-off\nwifi-on\nscan-off\ncountry-cn\nwifi-off\nwifi-on\ncountry-auto\nscan-on\nscan-now\nget-scan\nget-wifi\nget-link\nget-ipv4\n");
    snprintf(dir, sizeof dir, "%s-case%d", base, which);
    snprintf(target, sizeof target, "%s/target", dir);
    snprintf(helper, sizeof helper, "%s/wlan-helper", dir);
    snprintf(logpath, sizeof logpath, "%s/wlan-actions", dir);
    if (!check(mkdir(dir, 0700) == 0 && fixture_file(target) && copy_backend(backend, helper) &&
               text_file(dir, "wlan-scan", which == 2 ? "0\n" : "1\n") &&
               text_file(dir, "wlan-wifi", "1\n") &&
               text_file(dir, "wlan-override", "null\n") &&
               text_file(dir, "wlan-country", "CN\n") &&
               (which != 3 || text_file(dir, "wlan-fail", "get-scan")) &&
               (which != 4 || text_file(dir, "wlan-fail", "wifi-on")) &&
               (which != 5 || text_file(dir, "wlan-fail", "nul")) &&
               (which == 6 || (text_file(dir, "wlan-link", "1\n") &&
                               text_file(dir, "wlan-ipv4", "1\n"))), "wlan-fixture")) return 0;
    rc = command("run-wlan", dir, which == 1 ? "5000" : "1000",
                 which == 1 ? "wlan-off" : NULL, out, sizeof out);
    if (which == 3 || which == 5) return check(rc != 0 && exact_config(target, 0) &&
            content_is(dir, "wlan-scan", "1\n") && content_is(dir, "wlan-wifi", "1\n") &&
            content_is(dir, "wlan-actions", "get-scan\n"),
            which == 3 ? "failed-baseline-read-rejects-before-mutation" :
                         "embedded-NUL-read-rejects-before-mutation");
    if (!check(rc == 0 && (pid = guardian_pid(out)) > 1 &&
               waitpid(pid, &status, WNOHANG) == 0, "wlan-independent-guardian-starts")) return 0;
    if (which == 4) {
        return check(reap(pid, &status) && WIFEXITED(status) && WEXITSTATUS(status) != 0 &&
            exact_config(target, 0) && content_is(dir, "wlan-scan", "1\n") &&
            content_is(dir, "wlan-wifi", "0\n") &&
            command("recover", dir, NULL, NULL, out, sizeof out) == 6 &&
            strstr(out, "settings_failed") != NULL,
            "failed-radio-recovery-reports-failure-despite-restored-config");
    }
    if (which == 6) {
        return check(reap(pid, &status) && WIFEXITED(status) && WEXITSTATUS(status) == 7 &&
            exact_config(target, 0) && content_is(dir, "wlan-scan", "1\n") &&
            content_is(dir, "wlan-wifi", "1\n") &&
            content_is(dir, "wlan-restored", "restored\n") &&
            command("recover", dir, NULL, NULL, out, sizeof out) == 7 &&
            strstr(out, "network_not_ready") != NULL,
            "association-timeout-is-explicit-and-settings-still-restored");
    }
    if (which == 1) {
        struct timespec pause = {0, 10000000};
        for (i = 0; i < 300; i++) {
            if (content_is(dir, "wlan-wifi", "0\n")) break;
            nanosleep(&pause, NULL);
        }
        if (!check(i < 300 && content_is(dir, "wlan-scan", "0\n") &&
                   kill(pid, SIGKILL) == 0 && reap(pid, &status) && (status & 127) == SIGKILL,
                   "kill-guardian-after-radio-off")) return 0;
        if (!check(command("recover", dir, NULL, NULL, out, sizeof out) == 0,
                   "fresh-process-restores-mount-and-settings")) return 0;
    } else if (!check(reap(pid, &status) && WIFEXITED(status) && WEXITSTATUS(status) == 0,
                      "deadline-restores-mount-and-settings")) return 0;
    if (!check(exact_config(target, 0) && content_is(dir, "wlan-wifi", "1\n") &&
               content_is(dir, "wlan-scan", which == 2 ? "0\n" : "1\n"),
               "exact-config-wifi-and-original-scan-restored")) return 0;
    /* Assert ordered actual external actions; restoration must not enable scan
     * before radio rebuilding. Wrong order/omitted mutation fails this literal. */
    if (!check(content_is(dir, "wlan-actions", actions),
        "external-actions-have-safe-order-and-strict-readback")) return 0;
    return check(command("recover", dir, NULL, NULL, out, sizeof out) == 0 &&
                 content_is(dir, "wlan-actions", actions),
                 "wlan-repeat-recovery-idempotent");
}
int main(int argc, char **argv) {
    int ok;
    if (argc != 4 || strncmp(argv[1], "/data/local/tmp/mt6639-", strlen("/data/local/tmp/mt6639-")) ||
        strncmp(argv[2], "/data/local/tmp/mt6639-", strlen("/data/local/tmp/mt6639-")) ||
        strncmp(argv[3], "/data/local/tmp/mt6639-native-", strlen("/data/local/tmp/mt6639-native-"))) return 64;
    binary = argv[1];
    if (!check(syscall(NR_PRCTL, PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0 &&
        syscall(NR_UNSHARE, CLONE_NEWNS) == 0 &&
        syscall(NR_MOUNT, (char *)0, "/", (char *)0, MS_REC | MS_PRIVATE, (void *)0) == 0,
        "wlan-test-isolated-and-subreaper")) return 1;
    ok = case_wlan(argv[3], argv[2], 0) && case_wlan(argv[3], argv[2], 1) &&
         case_wlan(argv[3], argv[2], 2) && case_wlan(argv[3], argv[2], 3) &&
         case_wlan(argv[3], argv[2], 4) && case_wlan(argv[3], argv[2], 5) &&
         case_wlan(argv[3], argv[2], 6);
    stop_remaining_children();
    if (!ok || failures) return 1;
    printf("ALL-WLAN-TRANSACTION-CASES-PASS failures=0\n"); return 0;
}
