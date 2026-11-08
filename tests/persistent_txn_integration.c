/* Runs the real test-profile executor in a private mount namespace. The only
 * lifecycle input is DIR/module-control/modules/mt6639-6g, and every Binder call uses a fixture
 * executable. No production targets, real WLAN commands or device operations. */
#define MT_MOUNT_HELPERS_ONLY
#include "mount_txn_integration.c"
extern int rename(const char *, const char *);
extern int chmod(const char *, mode_t);
static int persistent_text(const char *dir, const char *name, const char *text) {
    char path[400]; int fd, ok;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return 0;
    ok = write(fd, text, strlen(text)) == (ssize_t)strlen(text) && !fsync(fd);
    close(fd); return ok;
}
static int persistent_content(const char *dir, const char *name, const char *expected) {
    char path[400], bytes[4096]; int fd; ssize_t n;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    n = read(fd, bytes, sizeof bytes); close(fd);
    return n == (ssize_t)strlen(expected) && !memcmp(bytes, expected, (size_t)n);
}
static int persistent_copy(const char *src, const char *dst) {
    char bytes[4096]; int in, out, ok = 1; ssize_t n;
    in = open(src, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (in < 0) return 0;
    out = open(dst, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0700);
    if (out < 0) { close(in); return 0; }
    while ((n = read(in, bytes, sizeof bytes)) > 0)
        if (write(out, bytes, (size_t)n) != n) { ok = 0; break; }
    if (n < 0 || fsync(out)) ok = 0;
    close(in); close(out); return ok;
}
static int persistent_log(const char *dir, const char *needle) {
    char path[400], bytes[8192]; int fd; ssize_t n;
    snprintf(path, sizeof path, "%s/guardian.log", dir);
    fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    n = read(fd, bytes, sizeof bytes - 1); close(fd);
    if (n < 0) return 0;
    bytes[n] = 0; return strstr(bytes, needle) != NULL;
}
static int persistent_ready(const char *dir, pid_t pid) {
    struct timespec pause = {0, 10000000}; int i, status;
    for (i = 0; i < 300; i++) {
        if (persistent_log(dir, "\"event\":\"persistent-hold\"")) return 1;
        if (waitpid(pid, &status, WNOHANG) != 0) return 0;
        nanosleep(&pause, NULL);
    }
    return 0;
}
static int persistent_fixture(const char *dir, const char *backend) {
    char target[340], helper[340], barrier[340], modules[360], control[380];
    snprintf(target, sizeof target, "%s/target", dir);
    snprintf(helper, sizeof helper, "%s/wlan-helper", dir);
    snprintf(barrier, sizeof barrier, "%s/module-control", dir);
    snprintf(modules, sizeof modules, "%s/modules", barrier);
    snprintf(control, sizeof control, "%s/mt6639-6g", modules);
    return mkdir(dir, 0700) == 0 && fixture_file(target) &&
        persistent_copy(backend, helper) && mkdir(barrier, 0700) == 0 &&
        mkdir(modules, 0700) == 0 && chmod(modules, 0777) == 0 &&
        mkdir(control, 0700) == 0 && chmod(control, 0755) == 0 &&
        persistent_text(dir, "wlan-scan", "1\n") &&
        persistent_text(dir, "wlan-wifi", "1\n") &&
        persistent_text(dir, "wlan-override", "null\n") &&
        persistent_text(dir, "wlan-country", "CN\n") &&
        persistent_text(dir, "wlan-link", "1\n") &&
        persistent_text(dir, "wlan-ipv4", "1\n");
}
static const char *persistent_apply_actions =
    "get-scan\nget-wifi\nget-override\nget-country\nscan-off\ncountry-us\nwifi-off\nwifi-on\n";
static const char *persistent_all_actions =
    "get-scan\nget-wifi\nget-override\nget-country\nscan-off\ncountry-us\nwifi-off\nwifi-on\n"
    "scan-off\ncountry-cn\nwifi-off\nwifi-on\ncountry-auto\nscan-on\nscan-now\n"
    "get-scan\nget-wifi\nget-link\nget-ipv4\n";
static int persistent_case(const char *base, const char *backend, int which) {
    static const char *names[] = {"cancel", "disable", "remove", "missing", "term", "replace", "invalid"};
    static const char *outcomes[] = {"cancelled", "module_disabled", "module_removed",
        "module_directory_changed", "cancelled", "module_directory_changed", "module_control_invalid"};
    char dir[300], target[340], control[400], moved[400], out[4096];
    struct sx before, at; int pid, status = 0, rc, i;
    struct timespec pause = {0, 10000000};
    snprintf(dir, sizeof dir, "%s-%s", base, names[which]);
    snprintf(target, sizeof target, "%s/target", dir);
    snprintf(control, sizeof control, "%s/module-control/modules/mt6639-6g", dir);
    snprintf(moved, sizeof moved, "%s/module-control/modules/mt6639-6g-old", dir);
    printf("CASE persistent-%s\n", names[which]);
    if (!check(persistent_fixture(dir, backend) && identity(AT_FDCWD, target, &before),
               "persistent-owned-fixture-and-baseline")) return 0;
    /* A numeric hold would accidentally reintroduce the bounded interface. */
    if (!check(command("run-wlan-persistent", dir, "1000", NULL, out, sizeof out) == 64,
               "persistent-rejects-numeric-duration")) return 0;
    rc = command("run-wlan-persistent", dir, NULL, NULL, out, sizeof out);
    if (!check(rc == 0 && strstr(out, "\"mode\":\"persistent\"") &&
               (pid = guardian_pid(out)) > 1 && persistent_ready(dir, pid),
               "persistent-real-executor-enters-stable-hold")) return 0;
    if (!check(command("recover", dir, NULL, NULL, out, sizeof out) == 3,
               "persistent-live-guardian-retains-recovery-lock")) return 0;
    /* The original short bounded tests restore after 1000 ms. Check beyond
     * that boundary, including zero repeated Binder calls during polling. */
    if (which == 0) for (i = 0; i < 160; i++) nanosleep(&pause, NULL);
    if (!check(waitpid(pid, &status, WNOHANG) == 0 && exact_config(target, 1) &&
               is_readonly(target) && persistent_content(dir, "wlan-actions", persistent_apply_actions),
               "persistent-holds-without-deadline-or-radio-reset")) return 0;
    if (which == 0) rc = command("cancel", dir, NULL, NULL, out, sizeof out) == 0;
    else if (which == 1 || which == 2)
        rc = persistent_text(control, which == 1 ? "disable" : "remove", "");
    else if (which == 3 || which == 5)
        rc = rename(control, moved) == 0 && (which != 5 || mkdir(control, 0755) == 0);
    else if (which == 4) rc = kill(pid, SIGTERM) == 0;
    else rc = chmod(control, 0777) == 0;
    if (!check(rc, "persistent-owned-lifecycle-trigger")) return 0;
    if (!check(reap(pid, &status) && WIFEXITED(status) &&
               WEXITSTATUS(status) == (which == 6 ? 4 : 0),
               "persistent-trigger-recovers-and-reaps-with-bounded-wait")) return 0;
    if (!check(identity(AT_FDCWD, target, &at) && same(&at, &before) && exact_config(target, 0) &&
               persistent_content(dir, "wlan-scan", "1\n") &&
               persistent_content(dir, "wlan-wifi", "1\n") &&
               persistent_content(dir, "wlan-override", "null\n") &&
               persistent_content(dir, "wlan-actions", persistent_all_actions) &&
               persistent_log(dir, outcomes[which]),
               "persistent-restores-exact-mount-settings-and-ordered-actions")) return 0;
    return check(command("recover", dir, NULL, NULL, out, sizeof out) == 0 &&
                 persistent_content(dir, "wlan-actions", persistent_all_actions),
                 "persistent-recovery-remains-idempotent");
}
static int persistent_preflight(const char *base, const char *backend) {
    char dir[300], control[400], target[340], out[4096];
    snprintf(dir, sizeof dir, "%s-pre-disabled", base);
    snprintf(control, sizeof control, "%s/module-control/modules/mt6639-6g", dir);
    snprintf(target, sizeof target, "%s/target", dir);
    return check(persistent_fixture(dir, backend) && persistent_text(control, "disable", "") &&
                 command("inspect-module", dir, NULL, NULL, out, sizeof out) == 4 &&
                 strstr(out, "\"module_state\":\"disabled\"") &&
                 command("run-wlan-persistent", dir, NULL, NULL, out, sizeof out) == 4 &&
                 strstr(out, "module_control_unavailable") && exact_config(target, 0),
                 "persistent-disabled-module-rejected-before-mutation");
}
static int persistent_layout(const char *base, const char *backend) {
    char dir[300], barrier[340], modules[360], control[380], target[340], out[4096];
    struct stat s; int fd, valid;
    snprintf(dir, sizeof dir, "%s-layout", base);
    snprintf(barrier, sizeof barrier, "%s/module-control", dir);
    snprintf(modules, sizeof modules, "%s/modules", barrier);
    snprintf(control, sizeof control, "%s/mt6639-6g", modules);
    snprintf(target, sizeof target, "%s/target", dir);
    if (!check(persistent_fixture(dir, backend), "module-layout-owned-fixture")) return 0;
    fd = open(modules, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    valid = fd >= 0 && !fstat(fd, &s) && s.st_uid == 0 && (s.st_mode & 07777) == 0777;
    if (fd >= 0) close(fd);
    if (!check(valid && command("inspect-module", dir, NULL, NULL, out, sizeof out) == 0 &&
               strstr(out, "\"status\":\"module_control_available\"") &&
               strstr(out, "\"read_only\":true"),
               "module-layout-0700-barrier-allows-root-0777-modules")) return 0;
    if (!check(chmod(barrier, 0755) == 0 &&
               command("inspect-module", dir, NULL, NULL, out, sizeof out) == 4 &&
               strstr(out, "module_control_unavailable") && chmod(barrier, 0700) == 0,
               "module-layout-requires-private-0700-barrier")) return 0;
    if (!check(chmod(control, 0777) == 0 &&
               command("inspect-module", dir, NULL, NULL, out, sizeof out) == 4 &&
               strstr(out, "module_control_unavailable") && chmod(control, 0755) == 0,
               "module-layout-rejects-writable-final-module")) return 0;
    if (!check(persistent_text(control, "remove", "") &&
               command("inspect-module", dir, NULL, NULL, out, sizeof out) == 4 &&
               strstr(out, "\"module_state\":\"removed\"") && exact_config(target, 0),
               "module-inspection-reports-remove-without-mutation")) return 0;
    snprintf(control, sizeof control, "%s/guardlock", dir);
    fd = open(control, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    valid = fd < 0 && errno == ENOENT;
    if (fd >= 0) close(fd);
    return check(valid && !persistent_log(dir, ""), "module-inspection-creates-no-authority-or-guardian");
}
int main(int argc, char **argv) {
    const char *prefix = "/data/local/tmp/mt6639-native-"; int ok, i;
    if (argc != 4 || strncmp(argv[1], "/data/local/tmp/mt6639-", strlen("/data/local/tmp/mt6639-")) ||
        strncmp(argv[2], "/data/local/tmp/mt6639-", strlen("/data/local/tmp/mt6639-")) ||
        strncmp(argv[3], prefix, strlen(prefix)) || strlen(argv[3]) > 180 ||
        strlen(argv[3]) <= strlen(prefix) || strchr(argv[3] + strlen(prefix), '/')) return 64;
    binary = argv[1];
    if (!check(syscall(NR_PRCTL, PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0 &&
        syscall(NR_UNSHARE, CLONE_NEWNS) == 0 &&
        syscall(NR_MOUNT, (char *)0, "/", (char *)0, MS_REC | MS_PRIVATE, (void *)0) == 0,
        "persistent-test-isolated-private-and-subreaper")) return 1;
    ok = persistent_layout(argv[3], argv[2]);
    for (i = 0; ok && i < 7; i++) ok = persistent_case(argv[3], argv[2], i);
    if (ok) ok = persistent_preflight(argv[3], argv[2]);
    stop_remaining_children();
    if (!ok || failures) return 1;
    printf("ALL-PERSISTENT-TRANSACTION-CASES-PASS failures=0\n"); return 0;
}
