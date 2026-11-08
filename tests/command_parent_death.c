/* Android integration: kill only our forked owner; its exec'd command must
 * inherit PDEATHSIG and be adopted/reaped by this test's subreaper. No WLAN. */
#include "../native/recovery/abi.h"
#include "../native/recovery/command.h"
extern int sscanf(const char *, const char *, ...);
static int bounded_reap(pid_t pid, int *status) {
    struct timespec pause = {0, 10000000}; int i;
    for (i = 0; i < 100; i++) {
        pid_t got = waitpid(pid, status, WNOHANG);
        if (got == pid) return 1;
        if (got < 0 && errno != EINTR) return 0;
        nanosleep(&pause, NULL);
    }
    return 0;
}
int main(void) {
    int p[2], status = 0, child = -1, ok = 0; pid_t owner;
    char line[80] = {0}; struct pollfd poller;
    if (syscall(167, 36, 1, 0, 0, 0) || pipe(p)) return 2;
    owner = fork();
    if (owner < 0) return 2;
    if (!owner) {
        char out[80];
        char *args[] = {"/system/bin/sh", "-c", "printf '%s\\n' \"$$\" >&9; exec sleep 5", NULL};
        close(p[0]);
        if (dup2(p[1], 9) < 0) _exit(3);
        if (p[1] != 9) close(p[1]);
        _exit(mt_command(args, 10000, out, sizeof out));
    }
    close(p[1]); poller.fd = p[0]; poller.events = POLLIN; poller.revents = 0;
    if (poll(&poller, 1, 2000) > 0 && (poller.revents & POLLIN) &&
        read(p[0], line, sizeof line - 1) > 0 && sscanf(line, "%d", &child) == 1 && child > 1 &&
        waitpid(owner, &status, WNOHANG) == 0 && kill(owner, SIGKILL) == 0 &&
        bounded_reap(owner, &status) && (status & 127) == SIGKILL &&
        bounded_reap(child, &status) && (status & 127) == SIGKILL) ok = 1;
    /* Never signal an unverified/non-child PID, including malformed readiness. */
    if (waitpid(owner, &status, WNOHANG) == 0) {
        kill(owner, SIGKILL); bounded_reap(owner, &status);
    }
    if (child > 1 && waitpid(child, &status, WNOHANG) == 0) {
        kill(child, SIGKILL); bounded_reap(child, &status);
    }
    close(p[0]);
    printf("%s direct-command-dies-with-owner owner=%d command=%d\n", ok ? "PASS" : "FAIL", owner, child);
    return ok ? 0 : 1;
}
