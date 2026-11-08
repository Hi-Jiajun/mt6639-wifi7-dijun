/* Drives the REAL mount-txn test-profile executable. Never runs production
 * mutators. Isolation is owned here, not by the executor under test. */
#define main anchor_fixture_unused_main
#include "mount_identity_fixture.c"
#undef main
extern int execv(const char *, char *const *);
extern int dup2(int, int);
extern int sscanf(const char *, const char *, ...);
extern char *strstr(const char *, const char *);
extern int memcmp(const void *, const void *, size_t);
#define NR_PRCTL 167
#define PR_SET_CHILD_SUBREAPER 36
#define MS_BIND 4096UL
#define MS_SHARED (1UL << 20)
static const char *binary;

static int command(const char *verb, const char *dir, const char *hold,
                   const char *stage, char *output, size_t cap) {
    int p[2], status = 0, elapsed = 0, done = 0;
    size_t used = 0;
    pid_t pid;
    struct pollfd poller;
    fflush(NULL);
    if (pipe(p)) return -1;
    pid = fork();
    if (pid < 0) { close(p[0]); close(p[1]); return -1; }
    if (!pid) {
        char *args[7];
        int n = 0;
        close(p[0]);
        if (dup2(p[1], 1) < 0 || dup2(p[1], 2) < 0) _exit(126);
        close(p[1]);
        args[n++] = (char *)binary; args[n++] = (char *)verb;
        if (dir) args[n++] = (char *)dir;
        if (hold) args[n++] = (char *)hold;
        if (stage) args[n++] = (char *)stage;
        args[n] = NULL;
        execv(binary, args); _exit(127);
    }
    close(p[1]);
    poller.fd = p[0]; poller.events = POLLIN; poller.revents = 0;
    while (elapsed < 7000 && used + 1 < cap) {
        int rc = poll(&poller, 1, 100);
        elapsed += 100;
        if (rc < 0 && errno == EINTR) continue;
        if (rc < 0) break;
        if (rc > 0) {
            ssize_t n = read(p[0], output + used, cap - used - 1);
            if (n == 0) { done = 1; break; }
            if (n < 0) break;
            used += (size_t)n;
        }
    }
    close(p[0]); output[used] = 0;
    if (!done) kill(pid, SIGKILL);
    if (!reap(pid, &status)) { kill(pid, SIGKILL); reap(pid, &status); return -1; }
    printf("command verb=%s rc=%d output=%s\n", verb,
           WIFEXITED(status) ? WEXITSTATUS(status) : -1, output);
    return done && WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}
static int fixture_file(const char *path) {
    const char *text = "header\nDisallowBand6G 1\ntail\n";
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    int ok;
    if (fd < 0) return 0;
    ok = write(fd, text, strlen(text)) == (ssize_t)strlen(text) && fsync(fd) == 0;
    if (close(fd)) ok = 0;
    return ok;
}
static int guardian_pid(const char *out) {
    const char *p = strstr(out, "\"guardian_pid\"");
    int pid = 0;
    if (!p || !(p = strchr(p, ':')) || sscanf(p + 1, "%d", &pid) != 1 || pid <= 1) return -1;
    return pid;
}
static int is_readonly(const char *path) {
    int fd = open(path, O_WRONLY | O_NOFOLLOW | O_CLOEXEC), err = fd < 0 ? errno : 0;
    if (fd >= 0) close(fd);
    return fd < 0 && err == 30;
}
static int exact_config(const char *path, int enabled) {
    const char *expected = enabled ? "header\nDisallowBand6G 0\ntail\n" :
                                     "header\nDisallowBand6G 1\ntail\n";
    char bytes[128];
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    ssize_t n;
    if (fd < 0) return 0;
    n = read(fd, bytes, sizeof bytes); close(fd);
    return n == (ssize_t)strlen(expected) && !memcmp(bytes, expected, (size_t)n);
}
static int run_case(const char *base, const char *name, const char *stage,
                    int crash, int foreign, int cancel) {
    char dir[300], peer[320], target[360], peer_target[360], box[360];
    char foreign_path[360], out[4096];
    struct sx before, peer_before, box_before, at, other, box_after, foreign_id;
    int pid, status = 0, fd, rc;
    snprintf(dir, sizeof dir, "%s-%s", base, name);
    snprintf(peer, sizeof peer, "%s-peer", dir);
    snprintf(target, sizeof target, "%s/target", dir);
    snprintf(peer_target, sizeof peer_target, "%s/target", peer);
    snprintf(box, sizeof box, "%s/stagebox", dir);
    snprintf(foreign_path, sizeof foreign_path, "%s/foreign", dir);
    printf("CASE %s\n", name);
    if (!check(mkdir(dir, 0700) == 0 && mkdir(peer, 0700) == 0 &&
               fixture_file(target) && file(foreign_path),
               "exclusive-executor-fixture")) return 0;
    if (!check(syscall(NR_MOUNT, dir, dir, (char *)0, MS_BIND, (void *)0) == 0 &&
               syscall(NR_MOUNT, (char *)0, dir, (char *)0, MS_SHARED, (void *)0) == 0 &&
               syscall(NR_MOUNT, dir, peer, (char *)0, MS_BIND, (void *)0) == 0 &&
               identity(AT_FDCWD, target, &before) &&
               identity(AT_FDCWD, peer_target, &peer_before),
               "shared-fixture-layout-and-baselines")) return 0;
    rc = command("run", dir, cancel || crash ? "5000" : "1000", stage, out, sizeof out);
    if (!check(rc == 0 && (pid = guardian_pid(out)) > 1,
               "real-executor-launcher-handshake")) return 0;
    if (!check(identity(AT_FDCWD, box, &box_before), "executor-created-container")) return 0;
    box_before.mnt_id = before.mnt_id;
    if (!check(waitpid(pid, &status, WNOHANG) == 0,
               "guardian-adopted-as-live-direct-child")) return 0;
    if (!check(command("recover", dir, NULL, NULL, out, sizeof out) != 0,
               "live-guardian-lock-refuses-recovery")) return 0;
    if (!stage || !strcmp(stage, "published")) {
        if (!check(identity(AT_FDCWD, target, &at) && !same(&at, &before) &&
                   identity(AT_FDCWD, peer_target, &other) && !same(&other, &peer_before) &&
                   is_readonly(target) && is_readonly(peer_target) &&
                   exact_config(target, 1) && exact_config(peer_target, 1),
                   "published-target-and-peer-are-readonly")) return 0;
    }
    if (foreign) {
        fd = clone(foreign_path);
        if (!check(fd >= 0 && attach(fd, target) && identity(AT_FDCWD, target, &foreign_id),
                   "test-owned-foreign-overlay")) { if (fd >= 0) close(fd); return 0; }
        close(fd);
    }
    if (crash) {
        if (!check(kill(pid, SIGKILL) == 0 && reap(pid, &status) &&
                   (status & 127) == SIGKILL, "guardian-killed-and-reaped")) return 0;
        rc = command("recover", dir, NULL, NULL, out, sizeof out);
        if (foreign)
            return check(rc == 4 && identity(AT_FDCWD, target, &at) && same(&at, &foreign_id),
                         "fresh-recover-refuses-and-preserves-foreign-mount");
        if (!check(rc == 0, "fresh-executor-process-recovers-after-guardian-death")) return 0;
    } else {
        if (cancel && !check(command("cancel", dir, NULL, NULL, out, sizeof out) == 0,
                             "explicit-cancel-request")) return 0;
        if (!check(reap(pid, &status) && WIFEXITED(status) && WEXITSTATUS(status) == 0,
                   "independent-guardian-restores-and-exits")) return 0;
    }
    if (!check(identity(AT_FDCWD, target, &at) && same(&at, &before) &&
               identity(AT_FDCWD, peer_target, &other) && same(&other, &peer_before) &&
               identity(AT_FDCWD, box, &box_after) && same(&box_after, &box_before) &&
               exact_config(target, 0) && exact_config(peer_target, 0),
               "target-peer-and-container-baselines-restored")) return 0;
    if (!check(command("recover", dir, NULL, NULL, out, sizeof out) == 0,
               "repeat-recovery-idempotent")) return 0;
    if (!check(syscall(NR_UMOUNT2, peer, MNT_DETACH) == 0 &&
               syscall(NR_UMOUNT2, dir, MNT_DETACH) == 0,
               "test-parent-layout-explicitly-removed")) return 0;
    return 1;
}
/* PR_SET_CHILD_SUBREAPER makes detached descendants OUR direct children after
 * their launcher exits. Read only our child list; kill before reaping, never
 * act on arbitrary device PIDs. This also handles assertion-failure cleanup. */
static void stop_remaining_children(void) {
    char path[100], text[4096], *p;
    int fd, pid, used, status;
    ssize_t n;
    snprintf(path, sizeof path, "/proc/self/task/%d/children", (int)getpid());
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    n = read(fd, text, sizeof text - 1); close(fd);
    if (n <= 0) return;
    text[n] = 0; p = text;
    while (sscanf(p, "%d%n", &pid, &used) == 1 && used > 0) {
        if (pid > 1 && waitpid(pid, &status, WNOHANG) == 0) {
            kill(pid, SIGKILL); reap(pid, &status);
        }
        p += used;
    }
}
#ifndef MT_MOUNT_HELPERS_ONLY
int main(int argc, char **argv) {
    char oldns[128] = {0}, newns[128] = {0};
    const char *prefix = "/data/local/tmp/mt6639-native-";
    int ok;
    if (argc != 3 || strncmp(argv[1], "/data/local/tmp/mt6639-", strlen("/data/local/tmp/mt6639-")) ||
        strncmp(argv[2], prefix, strlen(prefix)) || strlen(argv[2]) > 180 ||
        strlen(argv[2]) <= strlen(prefix) || strchr(argv[2] + strlen(prefix), '/')) return 64;
    binary = argv[1];
    if (!check(syscall(NR_PRCTL, PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0 &&
               readlink("/proc/self/ns/mnt", oldns, sizeof oldns - 1) > 0 &&
               syscall(NR_UNSHARE, CLONE_NEWNS) == 0 &&
               readlink("/proc/self/ns/mnt", newns, sizeof newns - 1) > 0 &&
               strcmp(oldns, newns) &&
               syscall(NR_MOUNT, (char *)0, "/", (char *)0,
                       MS_REC | MS_PRIVATE, (void *)0) == 0,
               "subreaper-and-private-test-namespace")) return 1;
    printf("namespaces original=%s isolated=%s\n", oldns, newns);
    ok = run_case(argv[2], "normal", NULL, 0, 0, 0) &&
         run_case(argv[2], "cancel", "published", 0, 0, 1) &&
         run_case(argv[2], "kill-prepared", "prepared", 1, 0, 0) &&
         run_case(argv[2], "kill-container", "container", 1, 0, 0) &&
         run_case(argv[2], "kill-staged", "staged", 1, 0, 0) &&
         run_case(argv[2], "kill-published", "published", 1, 0, 0) &&
         run_case(argv[2], "foreign", "published", 1, 1, 0);
    stop_remaining_children();
    if (!ok || failures) return 1;
    printf("ALL-EXECUTOR-INTEGRATION-CASES-PASS failures=0\n");
    return 0;
}
#endif
