/* Read-only capability probe for detached-mount identity APIs.
 * Creates at most one OPEN_TREE_CLONE detached mount in this process and
 * closes the fd immediately; never attaches it to any path, never moves
 * anything, never touches WLAN. Test-only, not a product tool.
 */
/* Termux tcc on this device has no Bionic development headers, so keep the
 * tiny ABI surface explicit (same approach as native/recovery/abi.h). */
extern int printf(const char *, ...);
extern long syscall(long, ...);
extern unsigned int getuid(void);
extern int close(int);
extern void *memset(void *, int, unsigned long);
extern char *strerror(int);
extern int *__errno(void);
#define errno (*__errno())

#define AT_FDCWD (-100)
#define AT_EMPTY_PATH 0x1000
#define OPEN_TREE_CLONE 1
#define STATX_MNT_ID 0x1000
#define STATX_MNT_ID_UNIQUE 0x4000
#define NR_OPEN_TREE 428
#define NR_STATX 291

struct probe_ts {
    long long sec;
    unsigned int nsec;
    int reserved;
};

struct probe_statx {
    unsigned int mask;
    unsigned int blksize;
    unsigned long long attributes;
    unsigned int nlink, uid, gid;
    unsigned short mode, spare0;
    unsigned long long ino, size, blocks, attributes_mask;
    struct probe_ts atime, btime, ctime, mtime;
    unsigned int rdev_major, rdev_minor, dev_major, dev_minor;
    unsigned long long mnt_id;
    unsigned int dio_mem_align, dio_offset_align;
    unsigned long long spare3[12];
};


static void show(const char *tag, long rc, int err) {
    if (rc >= 0) printf("%s ok rc=%ld\n", tag, rc);
    else printf("%s fail errno=%d (%s)\n", tag, err, strerror(err));
}

int main(int argc, char **argv) {
    const char *dir = argc > 1 ? argv[1] : ".";
    struct probe_statx sx;
    long fd, rc;
    int err;

    /* Historical attach branch used syscall40 (mount), not umount2(39).
     * Retired: all attaching experiments now use mount_identity_fixture.c
     * inside a new recursively-private mount namespace. */
    if (argc > 2) { printf("attach mode retired; use isolated fixture\n"); return 64; }
    printf("probe_uid=%u\n", (unsigned)getuid());

    memset(&sx, 0, sizeof sx);
    rc = syscall(NR_STATX, AT_FDCWD, dir, AT_EMPTY_PATH, STATX_MNT_ID, &sx);
    err = errno;
    show("statx_mnt_id_on_path", rc, err);
    if (rc == 0) printf("  mnt_id=%llu mask=0x%x\n",
                        (unsigned long long)sx.mnt_id, sx.mask);

    memset(&sx, 0, sizeof sx);
    rc = syscall(NR_STATX, AT_FDCWD, dir, AT_EMPTY_PATH,
                 STATX_MNT_ID | STATX_MNT_ID_UNIQUE, &sx);
    err = errno;
    show("statx_mnt_id_unique", rc, err);
    if (rc == 0)
        printf("  unique_requested mask=0x%x mnt_id=%llu unique_supported=%d\n",
               sx.mask, (unsigned long long)sx.mnt_id,
               (sx.mask & STATX_MNT_ID_UNIQUE) ? 1 : 0);

    fd = syscall(NR_OPEN_TREE, AT_FDCWD, dir, OPEN_TREE_CLONE, 0L);
    err = errno;
    show("open_tree_clone", fd, err);
    if (fd >= 0) {
        memset(&sx, 0, sizeof sx);
        rc = syscall(NR_STATX, (int)fd, "", AT_EMPTY_PATH,
                     STATX_MNT_ID | STATX_MNT_ID_UNIQUE, &sx);
        err = errno;
        show("statx_mnt_id_on_detached_fd", rc, err);
        if (rc == 0)
            printf("  detached mnt_id=%llu mask=0x%x unique_supported=%d\n",
                   (unsigned long long)sx.mnt_id, sx.mask,
                   (sx.mask & STATX_MNT_ID_UNIQUE) ? 1 : 0);
        close((int)fd);
        printf("detached_fd_closed=1\n");
    }
    return 0;
}
