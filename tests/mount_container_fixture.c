/* Kernel characterization, NOT production recovery. Only newly-created task
 * paths inside an isolated mount namespace. No WLAN or original-ns operations.
 * The harness observes identities AFTER a marker, before killing its direct
 * child. This is NOT durable pre-effect authority or guardian-death recovery.
 */
#define main anchor_fixture_unused_main
#include "mount_identity_fixture.c"
#undef main
#define MS_BIND 4096UL
#define MS_SHARED (1UL << 20)

struct pair { struct sx local, peer; };
static int pair_read(const char *local, const char *peer, struct pair *p) {
    return identity(AT_FDCWD, local, &p->local) && identity(AT_FDCWD, peer, &p->peer);
}
static int pair_same(const struct pair *a, const struct pair *b) {
    return same(&a->local, &b->local) && same(&a->peer, &b->peer);
}
static int setup_container(const char *container, const char *child,
                           const char *source, int phase) {
    int fd, ok;
    if (syscall(NR_MOUNT, container, container, (char *)0, MS_BIND, (void *)0)) return 0;
    if (phase == 1) return 1;
    if (syscall(NR_MOUNT, (char *)0, container, (char *)0, MS_PRIVATE, (void *)0)) return 0;
    if (phase == 2) return 1;
    fd = clone(source);
    if (fd < 0) return 0;
    ok = attach(fd, child);
    if (close(fd)) ok = 0;
    return ok;
}

/* Parent is the sole observer/recovery writer in this synthetic namespace.
 * No mount FD crosses fork: source clone is created and closed by the child. */
static int setup_and_crash(const char *container, const char *peer_container,
                          const char *child, const char *peer_child,
                          const char *source, int phase,
                          struct pair *owned, struct pair *children) {
    int p[2], ready, status = 0, killed, waited;
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
        if (!setup_container(container, child, source, phase)) _exit(20);
        if (write(p[1], "M", 1) != 1) _exit(21);
        close(p[1]);
        for (;;) nanosleep(&delay, NULL);
    }
    close(p[1]);
    poller.fd = p[0]; poller.events = POLLIN; poller.revents = 0;
    ready = poll(&poller, 1, 5000) > 0 && (poller.revents & POLLIN) &&
            read(p[0], &marker, 1) == 1 && marker == 'M' &&
            pair_read(container, peer_container, owned) && pair_read(child, peer_child, children);
    close(p[0]);
    killed = kill(pid, SIGKILL) == 0;
    waited = reap(pid, &status);
    if (!waited) { kill(pid, SIGKILL); waited = reap(pid, &status); }
    printf("setup phase=%d pid=%d ready=%d reaped=%d status=%d\n",
           phase, pid, ready, waited, status);
    return ready && killed && waited && (status & 127) == SIGKILL;
}

/* An occupied child forbids parent detach: otherwise lazy-unmount would hide
 * an unfinished staged resource. No concurrent writers is a fixture premise. */
static int clean_empty_container(const char *container, const char *peer_container,
                                 const char *child, const char *peer_child,
                                 const struct pair *owned,
                                 const struct pair *empty_children) {
    struct pair now, children;
    if (!pair_read(container, peer_container, &now) || !pair_same(&now, owned) ||
        !pair_read(child, peer_child, &children) || !pair_same(&children, empty_children)) return 4;
    /* Distinguish a refused precondition (4) from an attempted syscall failing
     * (5), so occupied-child tests cannot mistake the latter for prevention. */
    return syscall(NR_UMOUNT2, container, MNT_DETACH) == 0 ? 0 : 5;
}

static int container_case(const char *root, const char *name, int phase) {
    char dir[512], parent[600], peer[600], source[600];
    char container[700], peer_container[700], child[800], peer_child[800];
    struct pair base, owned, children, empty_children, now, after;
    struct sx source_id;
    int rc;
    printf("CASE %s\n", name);
    snprintf(dir, sizeof dir, "%s/%s", root, name);
    snprintf(parent, sizeof parent, "%s/parent", dir);
    snprintf(peer, sizeof peer, "%s/peer", dir);
    snprintf(source, sizeof source, "%s/source", dir);
    snprintf(container, sizeof container, "%s/container", parent);
    snprintf(peer_container, sizeof peer_container, "%s/container", peer);
    snprintf(child, sizeof child, "%s/child", container);
    snprintf(peer_child, sizeof peer_child, "%s/child", peer_container);
    if (!check(mkdir(dir, 0700) == 0 && mkdir(parent, 0700) == 0 && mkdir(peer, 0700) == 0 &&
               mkdir(container, 0700) == 0 && file(child) && file(source),
               "exclusive-case-directories-and-files")) return 0;
    if (!check(syscall(NR_MOUNT, parent, parent, (char *)0, MS_BIND, (void *)0) == 0 &&
               syscall(NR_MOUNT, (char *)0, parent, (char *)0, MS_SHARED, (void *)0) == 0 &&
               syscall(NR_MOUNT, parent, peer, (char *)0, MS_BIND, (void *)0) == 0,
               "new-isolated-shared-parent-and-peer")) return 0;
    if (!check(pair_read(container, peer_container, &base) &&
               pair_read(child, peer_child, &empty_children) &&
               identity(AT_FDCWD, source, &source_id), "baselines")) return 0;
    if (!phase) {
        if (!check(setup_container(container, child, source, 3) &&
                   pair_read(container, peer_container, &owned) &&
                   pair_read(child, peer_child, &children), "normal-setup")) return 0;
    } else if (!check(setup_and_crash(container, peer_container, child, peer_child,
                                     source, phase, &owned, &children),
                      "setup-worker-killed-and-reaped-after-identity-observation")) return 0;
    if (!check(!same(&owned.local, &base.local) && !same(&owned.peer, &base.peer) &&
               owned.local.mnt_id != owned.peer.mnt_id &&
               owned.local.ino == base.local.ino && owned.peer.ino == base.peer.ino &&
               pair_read(container, peer_container, &now) && pair_same(&now, &owned),
               "container-and-propagated-peer-owned-identities")) return 0;
    /* Same baseline file inode/dev, but now reached through container mounts. */
    empty_children.local.mnt_id = owned.local.mnt_id;
    empty_children.peer.mnt_id = owned.peer.mnt_id;
    if (!phase || phase == 3) {
        if (!check(!same(&children.local, &empty_children.local) &&
                   children.local.ino == source_id.ino &&
                   children.local.dev_major == source_id.dev_major &&
                   children.local.dev_minor == source_id.dev_minor &&
                   same(&children.peer, &empty_children.peer),
                   "private-container-child-not-propagated")) return 0;
        rc = clean_empty_container(container, peer_container, child, peer_child, &owned, &empty_children);
        if (!check(rc == 4 && pair_read(container, peer_container, &now) && pair_same(&now, &owned) &&
                   pair_read(child, peer_child, &after) && pair_same(&after, &children),
                   "occupied-child-blocks-parent-cleanup-preserves-both-pairs")) return 0;
        if (!check(identity(AT_FDCWD, child, &now.local) && same(&now.local, &children.local) &&
                   syscall(NR_UMOUNT2, child, MNT_DETACH) == 0,
                   "owned-child-unmounted-first")) return 0;
    }
    if (!check(pair_read(child, peer_child, &after) && pair_same(&after, &empty_children),
               "child-pair-empty-before-container-cleanup")) return 0;
    if (!check(clean_empty_container(container, peer_container, child, peer_child, &owned,
                                     &empty_children) == 0 &&
               pair_read(container, peer_container, &now) && pair_same(&now, &base),
               "one-local-container-unmount-restores-peer-too")) return 0;
    if (!check(syscall(NR_UMOUNT2, peer, MNT_DETACH) == 0 &&
               syscall(NR_UMOUNT2, parent, MNT_DETACH) == 0,
               "explicit-fixture-parent-teardown")) return 0;
    return 1;
}
int main(int argc, char **argv) {
    char before[128] = {0}, after[128] = {0};
    const char *prefix = "/data/local/tmp/mt6639-t02e-container-";
    if (argc != 2 || strncmp(argv[1], prefix, strlen(prefix)) ||
        strlen(argv[1]) <= strlen(prefix) || strlen(argv[1]) > 240 ||
        strchr(argv[1] + strlen(prefix), '/')) return 64;
    if (!check(readlink("/proc/self/ns/mnt", before, sizeof before - 1) > 0 &&
               syscall(NR_UNSHARE, CLONE_NEWNS) == 0 &&
               readlink("/proc/self/ns/mnt", after, sizeof after - 1) > 0 &&
               strcmp(before, after), "namespace-changed")) return 1;
    if (!check(syscall(NR_MOUNT, (char *)0, "/", (char *)0,
                       MS_REC | MS_PRIVATE, (void *)0) == 0,
               "recursive-private-before-new-shared-groups")) return 1;
    if (!check(mkdir(argv[1], 0700) == 0, "exclusive-root")) return 1;
    printf("namespaces original=%s isolated=%s\n", before, after);
    if (!container_case(argv[1], "normal-private", 0) ||
        !container_case(argv[1], "kill-after-self-bind", 1) ||
        !container_case(argv[1], "kill-after-private", 2) ||
        !container_case(argv[1], "kill-after-child", 3)) return 1;
    printf("ALL-CONTAINER-CASES-PASS failures=%d\n", failures);
    return failures ? 1 : 0;
}
