/* Test-only durability-boundary fault injection; real fsync always runs. */
#undef fsync
#include "../native/recovery/abi.h"
extern char *getenv(const char *);
extern int unlinkat(int, const char *, int);

int mt_test_setup_fsync(int fd) {
    static unsigned count;
    unsigned at = 0;
    const char *s = getenv("MT_TEST_FSYNC_AT");
    const char *action = getenv("MT_TEST_FSYNC_ACTION");
    int rc = fsync(fd);
    if (s) for (; *s >= '0' && *s <= '9'; s++) at = at * 10 + (unsigned)(*s - '0');
    if (++count == at) {
        if (action && !strcmp(action, "drift")) {
            int object = openat(fd, "fixture", O_WRONLY);
            if (object < 0) return -1;
            if (write(object, "external\n", 9) != 9) { close(object); return -1; }
            return close(object) ? -1 : rc;
        }
        if (action && !strcmp(action, "remove-stage")) return unlinkat(fd, "stage", 0) ? -1 : rc;
        if (action && !strcmp(action, "fail")) { errno = 5; return -1; }
        if (action && !strcmp(action, "term")) return kill(getpid(), SIGTERM) ? -1 : rc;
        if (kill(getpid(), SIGKILL)) return -1;
    }
    return rc;
}
