/* Test-only staged-mount/WAL characterization. No WLAN operations.
 * Reuse explicit ABI and isolation/identity helpers from the anchor fixture.
 * The harness holds the private namespace alive, NOT an anchor mount FD.
 */
#define main anchor_fixture_unused_main
#include "mount_identity_fixture.c"
#undef main
extern int sscanf(const char *, const char *, ...);

struct prepared_record {
    char boot[37], ns[128];
    struct sx target_before, stage_before, owned;
};

static int text_read(const char *path, char *buf, size_t cap, int owned) {
    struct stat st;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    ssize_t n, extra;
    char c;
    if (fd < 0) return 0;
    if (owned && (fstat(fd, &st) || !S_ISREG(st.st_mode) ||
        st.st_nlink != 1 || st.st_uid != geteuid() || (st.st_mode & 0777) != 0600)) {
        close(fd); return 0;
    }
    n = read(fd, buf, cap - 1);
    extra = n >= 0 ? read(fd, &c, 1) : -1;
    close(fd);
    if (n <= 0 || extra != 0) return 0;
    buf[n] = 0;
    return strlen(buf) == (size_t)n;
}
static int context(char *boot, char *ns) {
    char b[64];
    ssize_t n;
    if (!text_read("/proc/sys/kernel/random/boot_id", b, sizeof b, 0) ||
        strlen(b) != 37 || b[36] != '\n') return 0;
    b[36] = 0;
    snprintf(boot, 37, "%s", b);
    n = readlink("/proc/self/ns/mnt", ns, 127);
    if (n <= 0 || n >= 127) return 0;
    ns[n] = 0;
    return 1;
}
#define ID_ARGS(s) (s).mnt_id, (s).dev_major, (s).dev_minor, (s).ino
#define ID_PTRS(s) &(s).mnt_id, &(s).dev_major, &(s).dev_minor, &(s).ino
#define ID_FMT "%llu %u %u %llu"
static int encode(const struct prepared_record *r, char *buf, size_t cap) {
    int n = snprintf(buf, cap, "MTM1 %s %s " ID_FMT " " ID_FMT " " ID_FMT "\n",
                     r->boot, r->ns, ID_ARGS(r->target_before),
                     ID_ARGS(r->stage_before), ID_ARGS(r->owned));
    return n > 0 && (size_t)n < cap;
}
static int write_record(const char *dir, const char *path, const struct prepared_record *r) {
    char buf[512];
    int fd, d, ok;
    if (!encode(r, buf, sizeof buf)) return 0;
    fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return 0;
    ok = write(fd, buf, strlen(buf)) == (ssize_t)strlen(buf) && fsync(fd) == 0;
    if (close(fd)) ok = 0;
    d = open(dir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (d < 0) return 0;
    if (fsync(d)) ok = 0;
    if (close(d)) ok = 0;
    return ok;
}
static int read_record(const char *path, struct prepared_record *r) {
    char buf[512], canonical[512], boot[37], ns[128];
    int used = -1;
    if (!text_read(path, buf, sizeof buf, 1) ||
        sscanf(buf, "MTM1 %36s %127s " ID_FMT " " ID_FMT " " ID_FMT "%n",
               r->boot, r->ns, ID_PTRS(r->target_before),
               ID_PTRS(r->stage_before), ID_PTRS(r->owned), &used) != 14 ||
        used < 0 || (size_t)used + 1 != strlen(buf) || buf[used] != '\n' ||
        !encode(r, canonical, sizeof canonical) || strcmp(buf, canonical) ||
        !context(boot, ns) || strcmp(boot, r->boot) || strcmp(ns, r->ns) ||
        !r->owned.mnt_id || !r->owned.ino ||
        r->owned.mnt_id == r->target_before.mnt_id ||
        r->owned.mnt_id == r->stage_before.mnt_id) return 0;
    return 1;
}

/* Frozen prepared authority, not an append-only general transaction WAL.
 * Recovery has no mount FD and derives ownership only from the pre-effect
 * record plus exact live identities. No concurrent writers in this fixture. */
static int recover_recorded(const char *journal, const char *stage, const char *target) {
    struct prepared_record r = {0};
    struct sx at, staging;
    const char *owned_path;
    if (!read_record(journal, &r) || !identity(AT_FDCWD, target, &at) ||
        !identity(AT_FDCWD, stage, &staging)) return 4;
    printf("recover_ids prepared=%llu target=%llu stage=%llu\n",
           r.owned.mnt_id, at.mnt_id, staging.mnt_id);
    if (same(&at, &r.target_before) && same(&staging, &r.stage_before)) return 5;
    if (same(&at, &r.owned) && same(&staging, &r.stage_before)) owned_path = target;
    else if (same(&at, &r.target_before) && same(&staging, &r.owned)) owned_path = stage;
    else return 4;
    if (syscall(NR_UMOUNT2, owned_path, MNT_DETACH) != 0 ||
        !identity(AT_FDCWD, target, &at) || !identity(AT_FDCWD, stage, &staging) ||
        !same(&at, &r.target_before) || !same(&staging, &r.stage_before)) return 4;
    return 0;
}

/* Create staged mount, persist its attached identity, close the only anchor,
 * then optionally move it. This child is killed after a marker; the fresh
 * successor never inherits this child's mount FD. */
static int transaction(const char *dir, const char *src, const char *stage,
                       const char *target, const char *journal, int moved) {
    struct prepared_record r = {0};
    int fd = -1, ok = 0;
    if (!context(r.boot, r.ns) || !identity(AT_FDCWD, target, &r.target_before) ||
        !identity(AT_FDCWD, stage, &r.stage_before)) return 0;
    fd = clone(src);
    if (fd < 0 || !attach(fd, stage) || !identity(AT_FDCWD, stage, &r.owned)) goto out;
    if (close(fd)) { fd = -1; goto out; }
    fd = -1;
    if (!write_record(dir, journal, &r)) goto out;
    if (moved && syscall(NR_MOVE_MOUNT, AT_FDCWD, stage,
                         AT_FDCWD, target, 0) != 0) goto out;
    ok = 1;
out:
    if (fd >= 0) close(fd);
    return ok;
}
static int crash_transaction(const char *dir, const char *src, const char *stage,
                             const char *target, const char *journal, int moved) {
    int p[2], status = 0, ready, killed, waited;
    char marker;
    pid_t pid;
    struct pollfd poller;
    fflush(NULL);
    if (pipe(p)) return 0;
    pid = fork();
    if (pid < 0) { close(p[0]); close(p[1]); return 0; }
    if (!pid) {
        struct timespec delay = {1, 0};
        close(p[0]);
        if (!transaction(dir, src, stage, target, journal, moved)) _exit(20);
        if (write(p[1], "M", 1) != 1) _exit(21);
        close(p[1]);
        for (;;) nanosleep(&delay, NULL);
    }
    close(p[1]);
    poller.fd = p[0]; poller.events = POLLIN; poller.revents = 0;
    ready = poll(&poller, 1, 5000) > 0 && (poller.revents & POLLIN) &&
            read(p[0], &marker, 1) == 1 && marker == 'M';
    close(p[0]);
    killed = kill(pid, SIGKILL) == 0;
    waited = reap(pid, &status);
    if (!waited) { kill(pid, SIGKILL); waited = reap(pid, &status); }
    printf("transaction pid=%d moved=%d marker=%d reaped=%d status=%d\n",
           pid, moved, ready, waited, status);
    return ready && killed && waited && (status & 127) == SIGKILL;
}
static int successor(const char *journal, const char *stage, const char *target, int want, int wrong_ns) {
    int status = 0;
    pid_t pid;
    fflush(NULL);
    pid = fork();
    if (pid < 0) return 0;
    if (!pid) {
        int rc;
        if (wrong_ns && syscall(NR_UNSHARE, CLONE_NEWNS) != 0) _exit(64);
        rc = recover_recorded(journal, stage, target);
        printf("successor pid=%d recovery_rc=%d\n", getpid(), rc);
        fflush(NULL); _exit(rc);
    }
    if (!reap(pid, &status)) { kill(pid, SIGKILL); reap(pid, &status); return 0; }
    return WIFEXITED(status) && WEXITSTATUS(status) == want;
}

static int staged_case(const char *root, const char *name, int mode) {
    char dir[512], src[600], stage[600], target[600], journal[600], foreign[600];
    struct sx before, stage_before, after, stage_after, preserved, stage_preserved;
    int fd, want = mode >= 2 ? 4 : 0;
    snprintf(dir, sizeof dir, "%s/%s", root, name);
    snprintf(src, sizeof src, "%s/source", dir);
    snprintf(stage, sizeof stage, "%s/stage", dir);
    snprintf(target, sizeof target, "%s/target", dir);
    snprintf(journal, sizeof journal, "%s/prepared-record", dir);
    snprintf(foreign, sizeof foreign, "%s/foreign", dir);
    printf("CASE %s\n", name);
    if (!check(mkdir(dir, 0700) == 0 && file(src) && file(stage) && file(target) && file(foreign),
               "exclusive-fixture-files")) return 0;
    if (!check(identity(AT_FDCWD, target, &before) && identity(AT_FDCWD, stage, &stage_before),
               "baseline-pair")) return 0;
    if (!check(crash_transaction(dir, src, stage, target, journal, mode != 0),
               "transaction-killed-all-anchor-fds-gone")) return 0;
    if (mode == 2) {
        fd = clone(foreign);
        if (!check(fd >= 0 && attach(fd, target), "foreign-overlay-after-crash")) {
            if (fd >= 0) close(fd);
            return 0;
        }
        close(fd);
    } else if (mode == 3) {
        fd = open(journal, O_WRONLY | O_NOFOLLOW | O_CLOEXEC);
        if (!check(fd >= 0 && ftruncate(fd, 7) == 0 && fsync(fd) == 0, "inject-torn-record")) {
            if (fd >= 0) close(fd);
            return 0;
        }
        close(fd);
    } else if (mode == 4) {
        const char *stale = "MTM1 00000000-0000-0000-0000-000000000000";
        fd = open(journal, O_WRONLY | O_NOFOLLOW | O_CLOEXEC);
        if (!check(fd >= 0 && write(fd, stale, strlen(stale)) == (ssize_t)strlen(stale) &&
                   fsync(fd) == 0, "inject-other-boot-record")) {
            if (fd >= 0) close(fd);
            return 0;
        }
        close(fd);
    }
    if (!check(identity(AT_FDCWD, target, &preserved) &&
               identity(AT_FDCWD, stage, &stage_preserved), "pair-before-successor")) return 0;
    if (!check(successor(journal, stage, target, want, mode == 5), "fresh-successor-exit")) return 0;
    if (!check(identity(AT_FDCWD, target, &after) && identity(AT_FDCWD, stage, &stage_after),
               "post-recovery-pair")) return 0;
    if (mode >= 2)
        return check(same(&preserved, &after) && same(&stage_preserved, &stage_after),
                     "blocked-preserves-both-visible-objects");
    if (!check(same(&before, &after) && same(&stage_before, &stage_after), "both-baselines-restored")) return 0;
    return check(successor(journal, stage, target, 5, 0), "repeat-recovery-idle");
}
int main(int argc, char **argv) {
    char oldns[128] = {0}, newns[128] = {0};
    const char *prefix = "/data/local/tmp/mt6639-t02e-wal-";
    if (argc != 2 || strncmp(argv[1], prefix, strlen(prefix)) ||
        strlen(argv[1]) <= strlen(prefix) || strlen(argv[1]) > 240 ||
        strchr(argv[1] + strlen(prefix), '/')) return 64;
    if (!check(readlink("/proc/self/ns/mnt", oldns, sizeof oldns - 1) > 0 &&
               syscall(NR_UNSHARE, CLONE_NEWNS) == 0 &&
               readlink("/proc/self/ns/mnt", newns, sizeof newns - 1) > 0 &&
               strcmp(oldns, newns), "private-namespace-changed")) return 1;
    if (!check(syscall(NR_MOUNT, (char *)0, "/", (char *)0, MS_REC | MS_PRIVATE,
                       (void *)0) == 0, "recursive-private-before-attach")) return 1;
    if (!check(mkdir(argv[1], 0700) == 0, "exclusive-fixture-root")) return 1;
    printf("namespaces original=%s private=%s\n", oldns, newns);
    if (!staged_case(argv[1], "kill_before_move", 0) ||
        !staged_case(argv[1], "kill_after_move", 1) ||
        !staged_case(argv[1], "foreign_after_crash", 2) ||
        !staged_case(argv[1], "torn_record", 3) ||
        !staged_case(argv[1], "other_boot_record", 4) ||
        !staged_case(argv[1], "other_namespace", 5)) return 1;
    if (failures) return 1;
    printf("ALL-STAGED-WAL-CASES-PASS failures=0\n");
    return 0;
}
