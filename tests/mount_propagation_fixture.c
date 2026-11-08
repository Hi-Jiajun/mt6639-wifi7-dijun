/* Test-only shared-parent publication semantics. All shared groups are created
 * AFTER unshare+recursive-private; no peers in the original namespace.
 * This is not a WLAN tool or production mount adapter.
 */
#define main anchor_fixture_unused_main
#include "mount_identity_fixture.c"
#undef main
#define MS_BIND 4096UL
#define MS_SHARED (1UL << 20)
#define MS_REMOUNT 32UL
#define MS_RDONLY 1UL

static int self_bind(const char *p, unsigned long propagation) {
    return syscall(NR_MOUNT, p, p, (char *)0, MS_BIND, (void *)0) == 0 &&
           syscall(NR_MOUNT, (char *)0, p, (char *)0, propagation, (void *)0) == 0;
}
static int readonly_open_denied(const char *path) {
    int fd = open(path, O_WRONLY | O_NOFOLLOW | O_CLOEXEC);
    int err = fd < 0 ? errno : 0;
    if (fd >= 0) close(fd);
    return fd < 0 && err == 30;
}
static int publication(const char *source, const char *stage,
                       const char *target, const char *peer, int want_success) {
    struct sx base_stage, base_target, base_peer, detached, prepared, at, other, after_stage;
    int fd, rc, err;
    if (!check(identity(AT_FDCWD, stage, &base_stage) &&
               identity(AT_FDCWD, target, &base_target) &&
               identity(AT_FDCWD, peer, &base_peer), "three-baselines")) return 0;
    fd = clone(source);
    if (!check(fd >= 0 && identity(fd, "", &detached) && attach(fd, stage),
               "attach-prepared-stage")) {
        if (fd >= 0) close(fd);
        return 0;
    }
    if (!check(close(fd) == 0 && identity(AT_FDCWD, stage, &prepared),
               "stage-identity-after-closing-anchor")) return 0;
    rc = (int)syscall(NR_MOVE_MOUNT, AT_FDCWD, stage, AT_FDCWD, target, 0);
    err = rc < 0 ? errno : 0;
    printf("publication expected=%d rc=%d errno=%d detached=%llu prepared=%llu\n",
           want_success, rc, err, detached.mnt_id, prepared.mnt_id);
    if (!check(identity(AT_FDCWD, target, &at) && identity(AT_FDCWD, peer, &other) &&
               identity(AT_FDCWD, stage, &after_stage), "post-publication-identities")) return 0;
    printf("identities prepared=%llu target=%llu peer=%llu stage=%llu\n",
           prepared.mnt_id, at.mnt_id, other.mnt_id, after_stage.mnt_id);
    if (!want_success) {
        if (!check(rc == -1 && err == 22 && same(&at, &base_target) &&
                   same(&other, &base_peer) && same(&after_stage, &prepared),
                   "shared-parent-move-EINVAL-no-target-effect")) return 0;
        if (!check(syscall(NR_UMOUNT2, stage, MNT_DETACH) == 0 &&
                   identity(AT_FDCWD, stage, &after_stage) && same(&after_stage, &base_stage),
                   "shared-stage-owned-cleanup")) return 0;
        return 1;
    }
    if (!check(rc == 0 && same(&at, &prepared) && same(&after_stage, &base_stage),
               "private-stage-to-shared-target-preserves-prepared-id")) return 0;
    if (!check(!same(&at, &other) && other.ino == prepared.ino &&
               other.dev_major == prepared.dev_major && other.dev_minor == prepared.dev_minor &&
               other.mnt_id != prepared.mnt_id && !same(&other, &base_peer),
               "propagated-peer-has-distinct-unique-id")) return 0;
    if (!check(readonly_open_denied(target) && readonly_open_denied(peer),
               "published-target-and-peer-still-readonly")) return 0;
    /* No concurrent external writers. Never attempt to unmount a peer using
     * the original's ID: unmount the identity-matched publication only. */
    if (!check(syscall(NR_UMOUNT2, target, MNT_DETACH) == 0 &&
               identity(AT_FDCWD, target, &at) && identity(AT_FDCWD, peer, &other) &&
               same(&at, &base_target) && same(&other, &base_peer),
               "unmount-original-propagates-peer-cleanup")) return 0;
    return 1;
}
int main(int argc, char **argv) {
    char oldns[128] = {0}, newns[128] = {0};
    char srcdir[512], shared[512], private[512], targetdir[512], peerdir[512];
    char source[600], s1[600], s2[600], t1[600], t2[600], p1[600], p2[600];
    int write_probe, write_error;
    const char *prefix = "/data/local/tmp/mt6639-t02e-propagation-";
    if (argc != 2 || strncmp(argv[1], prefix, strlen(prefix)) ||
        strlen(argv[1]) <= strlen(prefix) || strlen(argv[1]) > 240 ||
        strchr(argv[1] + strlen(prefix), '/')) return 64;
    if (!check(readlink("/proc/self/ns/mnt", oldns, sizeof oldns - 1) > 0 &&
               syscall(NR_UNSHARE, CLONE_NEWNS) == 0 &&
               readlink("/proc/self/ns/mnt", newns, sizeof newns - 1) > 0 &&
               strcmp(oldns, newns), "namespace-changed")) return 1;
    if (!check(syscall(NR_MOUNT, (char *)0, "/", (char *)0, MS_REC | MS_PRIVATE,
                       (void *)0) == 0, "recursive-private-before-new-shared-groups")) return 1;
    if (!check(mkdir(argv[1], 0700) == 0, "exclusive-root")) return 1;
    printf("namespaces original=%s isolated=%s\n", oldns, newns);
    snprintf(srcdir, sizeof srcdir, "%s/source-parent", argv[1]);
    snprintf(shared, sizeof shared, "%s/shared-stage-parent", argv[1]);
    snprintf(private, sizeof private, "%s/private-stage-parent", argv[1]);
    snprintf(targetdir, sizeof targetdir, "%s/target-parent", argv[1]);
    snprintf(peerdir, sizeof peerdir, "%s/peer-parent", argv[1]);
    snprintf(source, sizeof source, "%s/source", srcdir);
    snprintf(s1, sizeof s1, "%s/stage", shared);
    snprintf(s2, sizeof s2, "%s/stage", private);
    snprintf(t1, sizeof t1, "%s/target1", targetdir);
    snprintf(t2, sizeof t2, "%s/target2", targetdir);
    snprintf(p1, sizeof p1, "%s/target1", peerdir);
    snprintf(p2, sizeof p2, "%s/target2", peerdir);
    if (!check(mkdir(srcdir, 0700) == 0 && mkdir(shared, 0700) == 0 &&
               mkdir(private, 0700) == 0 && mkdir(targetdir, 0700) == 0 &&
               mkdir(peerdir, 0700) == 0 && file(source) && file(s1) && file(s2) &&
               file(t1) && file(t2), "create-exclusive-files")) return 1;
    if (!check(self_bind(srcdir, MS_PRIVATE) && self_bind(shared, MS_SHARED) &&
               self_bind(private, MS_PRIVATE) && self_bind(targetdir, MS_SHARED),
               "explicit-parent-propagation-layout")) return 1;
    /* A read-only destination parent does not make a covering mount read-only.
     * Clone from a read-only source mount so publication inherits that flag. */
    if (!check(syscall(NR_MOUNT, (char *)0, srcdir, (char *)0,
                       MS_REMOUNT | MS_BIND | MS_RDONLY, (void *)0) == 0 &&
               readonly_open_denied(source),
               "source-parent-readonly-before-clone")) return 1;
    if (!check(syscall(NR_MOUNT, (char *)0, targetdir, (char *)0,
                       MS_REMOUNT | MS_BIND | MS_RDONLY, (void *)0) == 0,
               "target-parent-readonly")) return 1;
    /* No O_TRUNC and no write: prove read-only behavior, not merely flags. */
    write_probe = open(t1, O_WRONLY | O_NOFOLLOW | O_CLOEXEC);
    write_error = write_probe < 0 ? errno : 0;
    if (write_probe >= 0) close(write_probe);
    if (!check(write_probe < 0 && write_error == 30, "readonly-target-open-EROFS")) return 1;
    if (!check(syscall(NR_MOUNT, targetdir, peerdir, (char *)0, MS_BIND, (void *)0) == 0,
               "create-shared-peer-inside-isolated-namespace")) return 1;
    printf("CASE shared-stage-parent\n");
    if (!publication(source, s1, t1, p1, 0)) return 1;
    printf("CASE private-stage-parent-to-readonly-shared-target\n");
    if (!publication(source, s2, t2, p2, 1)) return 1;
    if (failures) return 1;
    printf("ALL-PROPAGATION-CASES-PASS failures=0\n");
    return 0;
}
