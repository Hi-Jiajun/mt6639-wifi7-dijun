/* Deterministic fault injection at the first real durability syscall.
 * Production is compiled with -Dfsync=mt_test_signal_fsync for this test only;
 * the wrapper preserves the actual fsync and all filesystem side effects.
 */
#undef fsync
#include "../native/recovery/abi.h"

int mt_test_signal_fsync(int fd) {
    static int sent;
    if (!sent) {
        sent = 1;
        if (kill(getpid(), SIGTERM)) return -1;
    }
    return fsync(fd);
}
