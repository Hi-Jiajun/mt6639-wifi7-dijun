#ifndef MT6639_EXCHANGE_H
#define MT6639_EXCHANGE_H
/* Only two task-owned names. No two-rename fallback: losing the original
 * object between operations would invalidate the prepared-pair protocol. */
#if defined(__linux__) && !defined(MT_ANDROID_AARCH64)
#include <sys/syscall.h>
#endif
static int exchange_objects(int dir) {
#ifdef __APPLE__
    if (renameatx_np(dir, "fixture", dir, "prepared", RENAME_SWAP)) return -1;
#elif defined(__linux__)
#ifdef MT_ANDROID_AARCH64
    /* Linux arm64 UAPI __NR_renameat2; only compiled under LP64 guard in abi.h. */
    extern long syscall(long, ...);
    if (syscall(276, dir, "fixture", dir, "prepared", 2)) return -1;
#else
    if (syscall(SYS_renameat2, dir, "fixture", dir, "prepared", 2)) return -1;
#endif
#else
    (void)dir;
    return -1;
#endif
    return fsync(dir);
}
#endif
