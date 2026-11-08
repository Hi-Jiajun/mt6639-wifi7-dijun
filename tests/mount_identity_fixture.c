/* Kernel characterization fixture, NOT production recovery.
 * No caller-supplied mount targets; only newly-created private fixture files.
 * Foreign mounts live until this private namespace exits. No WLAN operations.
 * Build on device: tcc -DMT_ANDROID_AARCH64 -o fixture this-file.c
 */
#include "../native/recovery/abi.h"
#ifndef MT_ANDROID_AARCH64
#error This_fixture_requires_AArch64_Android_explicit_ABI
#endif
extern long syscall(long, ...);
extern int mkdir(const char *, unsigned int);
#define NR_UMOUNT2 39
#define NR_MOUNT 40
#define NR_UNSHARE 97
#define NR_STATX 291
#define NR_OPEN_TREE 428
#define NR_MOVE_MOUNT 429
#define CLONE_NEWNS 0x20000
#define MS_PRIVATE (1UL << 18)
#define MS_REC 16384UL
#define AT_EMPTY_PATH 0x1000
#define STATX_MNT_ID_UNIQUE 0x4000
#define OPEN_TREE_CLONE 1
#define MOVE_MOUNT_F_EMPTY_PATH 4
#define MNT_DETACH 2

struct sx_time { long long sec; unsigned int nsec; int reserved; };
struct sx {
    unsigned int mask, blksize;
    unsigned long long attributes;
    unsigned int nlink, uid, gid;
    unsigned short mode, spare0;
    unsigned long long ino, size, blocks, attributes_mask;
    struct sx_time atime, btime, ctime, mtime;
    unsigned int rdev_major, rdev_minor, dev_major, dev_minor;
    unsigned long long mnt_id;
    unsigned int dio_mem_align, dio_offset_align;
    unsigned long long spare[12];
};
typedef char statx_size_check[sizeof(struct sx) == 256 ? 1 : -1];
static int failures;

static int check(int ok, const char *name) {
    printf("%s %s errno=%d\n", ok ? "PASS" : "FAIL", name, ok ? 0 : errno);
    fflush(NULL);
    if (!ok) failures++;
    return ok;
}
static int identity(int fd, const char *path, struct sx *s) {
    struct sx zero = {0};
    *s = zero;
    /* UNIQUE alone: do not silently fall back to recyclable mount IDs. */
    return syscall(NR_STATX, fd, path, AT_EMPTY_PATH,
                   STATX_MNT_ID_UNIQUE | 0x100 /* STATX_INO */, s) == 0 &&
           (s->mask & (STATX_MNT_ID_UNIQUE | 0x100)) ==
                       (STATX_MNT_ID_UNIQUE | 0x100);
}
static int same(const struct sx *a, const struct sx *b) {
    return a->mnt_id == b->mnt_id && a->ino == b->ino &&
           a->dev_major == b->dev_major && a->dev_minor == b->dev_minor;
}
static int file(const char *p) {
    int fd = open(p, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    int ok;
    if (fd < 0) return 0;
    ok = write(fd, "same-bytes\n", 11) == 11 && fsync(fd) == 0;
    if (close(fd) != 0) ok = 0;
    return ok;
}
static int attach(int fd, const char *target) {
    return syscall(NR_MOVE_MOUNT, fd, "", AT_FDCWD, target,
                   MOVE_MOUNT_F_EMPTY_PATH) == 0;
}
static int clone(const char *source) {
    return (int)syscall(NR_OPEN_TREE, AT_FDCWD, source,
                        OPEN_TREE_CLONE | O_CLOEXEC);
}
static int reap(pid_t pid, int *status) {
    int n;
    struct timespec delay = {0, 10000000};
    for (n = 0; n < 500; n++) {
        pid_t rc = waitpid(pid, status, WNOHANG);
        if (rc == pid) return 1;
        if (rc < 0 && errno != EINTR) return 0;
        nanosleep(&delay, NULL);
    }
    return 0;
}
/* Parent owns anchor before fork. Marker is NOT a durable after-record. */
static int worker(int anchor, const char *target, int mode) {
    int p[2], status = 0, ready, waited, killed = 1;
    char marker = 0;
    pid_t pid;
    struct pollfd poller;
    if (pipe(p) != 0) return 0;
    pid = fork();
    if (pid < 0) { close(p[0]); close(p[1]); return 0; }
    if (pid == 0) {
        struct timespec delay = {1, 0};
        close(p[0]);
        if (mode != 1 && !attach(anchor, target)) _exit(10);
        if (write(p[1], "M", 1) != 1) _exit(11);
        close(p[1]);
        if (mode == 0) _exit(0);
        for (;;) nanosleep(&delay, NULL);
    }
    close(p[1]);
    poller.fd = p[0]; poller.events = POLLIN; poller.revents = 0;
    ready = poll(&poller, 1, 5000) > 0 &&
            (poller.revents & POLLIN) && read(p[0], &marker, 1) == 1 && marker == 'M';
    close(p[0]);
    /* Never reap before kill: this direct child PID cannot be reused. */
    if (mode != 0 || !ready) killed = kill(pid, SIGKILL) == 0;
    waited = reap(pid, &status);
    if (!waited) {
        kill(pid, SIGKILL);
        waited = reap(pid, &status);
        check(0, "worker-deadline");
    }
    printf("worker pid=%d mode=%d ready=%d reaped=%d status=%d\n",
           pid, mode, ready, waited, status);
    return ready && waited && killed &&
           (mode == 0 ? (WIFEXITED(status) && WEXITSTATUS(status) == 0) :
                        ((status & 127) == SIGKILL));
}
static int scenario(const char *root, const char *name, int mode) {
    char dir[512], src[600], dst[600], foreign[600];
    struct sx before, source, other, at, held, detached;
    int anchor = -1, extra = -1, ok = 0;
    snprintf(dir, sizeof dir, "%s/%s", root, name);
    snprintf(src, sizeof src, "%s/source", dir);
    snprintf(dst, sizeof dst, "%s/target", dir);
    snprintf(foreign, sizeof foreign, "%s/foreign", dir);
    printf("CASE %s\n", name);
    if (!check(mkdir(dir, 0700) == 0 && file(src) && file(dst) && file(foreign),
               "exclusive-same-byte-files")) goto out;
    if (!check(identity(AT_FDCWD, dst, &before) && identity(AT_FDCWD, src, &source) &&
               identity(AT_FDCWD, foreign, &other) && source.ino != before.ino &&
               other.ino != source.ino && other.ino != before.ino,
               "distinct-inodes-baseline")) goto out;
    anchor = clone(src);
    if (!check(anchor >= 0 && identity(anchor, "", &detached), "anchor-before-fork")) goto out;
    if (mode == 3) {
        extra = clone(foreign);
        if (!check(extra >= 0 && attach(extra, dst) && identity(AT_FDCWD, dst, &at),
                   "foreign-occupies-target")) goto out;
        if (!check(!same(&at, &before), "preflight-refuses-occupied")) goto out;
        /* The application worker MUST NOT run in this branch. */
        if (!check(identity(AT_FDCWD, dst, &held) && same(&held, &at),
                   "occupied-preserved-no-stack")) goto out;
        ok = 1; goto out;
    }
    if (!check(identity(AT_FDCWD, dst, &at) && same(&at, &before),
               "preflight-baseline")) goto out;
    if (!check(worker(anchor, dst, mode == 4 ? 0 : mode), "worker-marker-and-reaped")) goto out;
    if (!check(identity(anchor, "", &held) && identity(AT_FDCWD, dst, &at),
               "post-effect-anchor-and-path")) goto out;
    printf("mount_ids detached=%llu anchor_after=%llu target=%llu baseline=%llu\n",
           detached.mnt_id, held.mnt_id, at.mnt_id, before.mnt_id);
    if (mode == 1) {
        ok = check(same(&at, &before) && !same(&at, &held), "kill-before-no-unmount");
        goto out;
    }
    if (!check(same(&at, &held) && !same(&at, &before), "attached-owned-by-anchor")) goto out;
    if (mode == 4) {
        extra = clone(foreign);
        if (!check(extra >= 0 && attach(extra, dst) && identity(AT_FDCWD, dst, &other),
                   "foreign-overlays-owned")) goto out;
        if (!check(identity(anchor, "", &held) && !same(&held, &other),
                   "foreign-mismatch-refuses-unmount")) goto out;
        ok = check(identity(AT_FDCWD, dst, &at) && same(&at, &other),
                   "foreign-preserved-until-namespace-exit");
        goto out;
    }
    /* No concurrent namespace writers in this synthetic case. This comparison
     * does NOT solve production TOCTOU/fencing against another privileged actor. */
    if (!check(syscall(NR_UMOUNT2, dst, MNT_DETACH) == 0, "owned-target-unmounted")) goto out;
    ok = check(identity(AT_FDCWD, dst, &at) && same(&at, &before), "baseline-restored");
out:
    if (anchor >= 0) close(anchor);
    if (extra >= 0) close(extra);
    return ok;
}
int main(int argc, char **argv) {
    char oldns[128] = {0}, newns[128] = {0};
    const char *prefix = "/data/local/tmp/mt6639-t02e-";
    if (argc != 2 || strncmp(argv[1], prefix, strlen(prefix)) ||
        strlen(argv[1]) <= strlen(prefix) || strlen(argv[1]) > 240 ||
        strchr(argv[1] + strlen(prefix), '/')) return 64;
    if (!check(readlink("/proc/self/ns/mnt", oldns, sizeof oldns - 1) > 0,
               "read-original-namespace")) return 1;
    if (!check(syscall(NR_UNSHARE, CLONE_NEWNS) == 0, "unshare-mount-namespace")) return 1;
    if (!check(readlink("/proc/self/ns/mnt", newns, sizeof newns - 1) > 0 &&
               strcmp(oldns, newns) != 0, "namespace-changed")) return 1;
    if (!check(syscall(NR_MOUNT, (char *)0, "/", (char *)0,
                       MS_REC | MS_PRIVATE, (void *)0) == 0,
               "recursive-private-before-any-attach")) return 1;
    printf("namespaces before=%s private=%s\n", oldns, newns);
    if (!check(mkdir(argv[1], 0700) == 0, "exclusive-fixture-root")) return 1;
    if (!scenario(argv[1], "normal", 0)) return 1;
    if (!scenario(argv[1], "kill_before", 1)) return 1;
    if (!scenario(argv[1], "kill_after", 2)) return 1;
    if (!scenario(argv[1], "occupied", 3)) return 1;
    if (!scenario(argv[1], "foreign_after", 4)) return 1;
    printf("ALL-MOUNT-IDENTITY-CASES-PASS failures=%d\n", failures);
    return failures ? 1 : 0;
}
