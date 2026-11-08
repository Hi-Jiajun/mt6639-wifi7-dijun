/* T02B process/WAL substrate, fixture adapter ONLY. No exec, networking, mount,
 * Android settings, root changes, or arbitrary target names. Not a WLAN tool.
 * A successor never signals a process it did not fork and has not yet reaped.
 */
#include "abi.h"
#include "exchange.h"

enum { OK = 0, BUSY = 2, CONFLICT = 3, BLOCKED = 4, NOT_APPLIED = 5, USAGE = 64 };
enum phase { SETUP = -3, ABORT_INTENT, ABORTED,
             BEFORE, INTENT, APPLIED, RESTORE_INTENT, RESTORED, CLOSED };
struct journal { enum phase phase; int applied; };
static volatile sig_atomic_t cancellation;
static int final_event_emitted;
static int prepared_mode;
static const char baseline[] = "baseline\n";
static const char candidate[] = "candidate\n";
static int environment(char *buf, size_t cap);

static void request_cancel(int sig) { (void)sig; cancellation = 1; }

static long long now_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int pause_ms(long ms) {
    struct timespec t;
    t.tv_sec = ms / 1000;
    t.tv_nsec = (ms % 1000) * 1000000;
    while (nanosleep(&t, &t)) if (errno != EINTR) return -1;
    return 0;
}

static int write_all(int fd, const char *buf, size_t size) {
    while (size) {
        ssize_t n = write(fd, buf, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        buf += n;
        size -= (size_t)n;
    }
    return 0;
}

/* No O_TRUNC before validation. Only our UID's singly-linked private files. */
static int safe_open(int dir, const char *name, int flags) {
    struct stat st;
    /* A foreign FIFO must not block before fstat can reject its type. */
    int fd = openat(dir, name, flags | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
    if (fd < 0) return -1;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
        st.st_uid != geteuid() || (st.st_mode & 0777) != 0600) {
        close(fd);
        return -1;
    }
    return fd;
}

static int read_fd(int fd, char *buf, size_t cap) {
    size_t used = 0;
    ssize_t n;
    while (used < cap - 1) {
        n = read(fd, buf + used, cap - 1 - used);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) return -1;
        if (!n) { buf[used] = 0; return (int)used; }
        used += (size_t)n;
    }
    /* Refuse both oversized input and ambiguous embedded NUL via callers. */
    return -1;
}

static int read_owned(int dir, const char *name, char *buf, size_t cap) {
    int fd = safe_open(dir, name, O_RDONLY), n;
    if (fd < 0) return -1;
    n = read_fd(fd, buf, cap);
    close(fd);
    if (n < 0 || strlen(buf) != (size_t)n) return -1;
    return n;
}

static int new_file(int dir, const char *name, const char *text) {
    int fd = safe_open(dir, name, O_WRONLY | O_CREAT | O_EXCL), rc;
    if (fd < 0) return -1;
    rc = write_all(fd, text, strlen(text));
    if (!rc) rc = fsync(fd);
    if (close(fd)) rc = -1;
    if (!rc) rc = fsync(dir);
    return rc;
}

static int append_event(int dir, const char *event) {
    int fd = safe_open(dir, "journal", O_WRONLY | O_APPEND), rc;
    if (fd < 0) return -1;
    rc = write_all(fd, event, strlen(event));
    if (!rc) rc = fsync(fd);
    if (close(fd)) rc = -1;
    return rc;
}

static int identity(int fd, char *buf, size_t cap) {
    struct stat st;
    int n;
    if (fstat(fd, &st)) return -1;
    n = snprintf(buf, cap, "%llu %llu\n", (unsigned long long)st.st_dev,
                 (unsigned long long)st.st_ino);
    return n > 0 && (size_t)n < cap ? 0 : -1;
}

static int fixture_open(int dir, int flags) {
    char expected[128], current[128];
    int fd;
    if (read_owned(dir, "resource", expected, sizeof(expected)) < 0) return -1;
    fd = safe_open(dir, "fixture", flags);
    if (fd < 0) return -1;
    if (identity(fd, current, sizeof(current)) || strcmp(expected, current)) {
        close(fd);
        return -1;
    }
    return fd;
}

/* Two identities registered BEFORE effect; accept only their exact pairing.
 * Same bytes in another inode (at either end) never establish ownership.
 * Both objects are retained by an atomic exchange, including across intent
 * and restore_intent windows. This is a private-fixture protocol, not a mount
 * ownership implementation or protection from a malicious same-UID WAL editor.
 */
static int prepared_pair(int dir, char *value, size_t cap) {
    char original[128], prepared[128], first[128], second[128], a[64], b[64];
    int fd, other, n, m, swapped;
    if (read_owned(dir, "resource", original, sizeof(original)) < 0 ||
        read_owned(dir, "prepared_resource", prepared, sizeof(prepared)) < 0 ||
        !strcmp(original, prepared)) return -1;
    fd = safe_open(dir, "fixture", O_RDONLY);
    if (fd < 0) return -1;
    other = safe_open(dir, "prepared", O_RDONLY);
    if (other < 0) { close(fd); return -1; }
    n = read_fd(fd, a, sizeof(a)); m = read_fd(other, b, sizeof(b));
    swapped = identity(fd, first, sizeof(first)) || identity(other, second, sizeof(second));
    close(fd); close(other);
    if (swapped || n < 0 || m < 0 || strlen(a) != (size_t)n || strlen(b) != (size_t)m) return -1;
    if (!strcmp(first, original) && !strcmp(second, prepared) &&
        !strcmp(a, baseline) && !strcmp(b, candidate)) swapped = 0;
    else if (!strcmp(first, prepared) && !strcmp(second, original) &&
             !strcmp(a, candidate) && !strcmp(b, baseline)) swapped = 1;
    else return -1;
    n = snprintf(value, cap, "%s", swapped ? candidate : baseline);
    return n >= 0 && (size_t)n < cap ? 0 : -1;
}

static int fixture_value(int dir, char *buf, size_t cap) {
    int fd, n;
    if (prepared_mode) return prepared_pair(dir, buf, cap);
    fd = fixture_open(dir, O_RDONLY);
    if (fd < 0) return -1;
    n = read_fd(fd, buf, cap);
    close(fd);
    return n >= 0 && strlen(buf) == (size_t)n ? 0 : -1;
}

static int replace_fixture(int dir, const char *expected, const char *value) {
    char buf[64];
    int fd, n, rc;
    if (fixture_value(dir, buf, sizeof(buf)) || strcmp(buf, expected)) return -1;
    if (prepared_mode) {
        if ((strcmp(expected, baseline) || strcmp(value, candidate)) &&
            (strcmp(expected, candidate) || strcmp(value, baseline))) return -1;
        return exchange_objects(dir);
    }
    fd = fixture_open(dir, O_WRONLY);
    if (fd < 0) return -1;
    n = (int)strlen(value);
    rc = write_all(fd, value, (size_t)n);
    if (!rc) rc = ftruncate(fd, n);
    if (!rc) rc = fsync(fd);
    if (close(fd)) rc = -1;
    return rc;
}

static int read_journal(int dir, struct journal *j) {
    char buf[512], env[256], *line, *end;
    int count = 0;
    if (read_owned(dir, "journal", buf, sizeof(buf)) <= 0) return -1;
    j->phase = BEFORE;
    j->applied = 0;
    line = buf;
    /* The first complete setup record carries boot/namespace itself, before
     * any other setup artifact exists. Partial headers never imply no effect.
     * Legacy before-first journals remain supported, never setup-abortable. */
    if (!strncmp(line, "setup\n", 6)) {
        if (environment(env, sizeof(env)) ||
            strncmp(line + 6, env, strlen(env))) return -1;
        line += 6 + strlen(env);
        j->phase = SETUP;
        count = 1;
    }
    while (*line) {
        end = strchr(line, '\n');
        if (!end) return -1;
        *end = 0;
        if (!count) { if (strcmp(line, "before")) return -1; }
        else if (!strcmp(line, "before") && j->phase == SETUP) j->phase = BEFORE;
        else if (!strcmp(line, "abort_intent") && j->phase == SETUP) j->phase = ABORT_INTENT;
        else if (!strcmp(line, "aborted") && j->phase == ABORT_INTENT) j->phase = ABORTED;
        else if (!strcmp(line, "intent") && j->phase == BEFORE) j->phase = INTENT;
        else if (!strcmp(line, "applied") && j->phase == INTENT) {
            j->phase = APPLIED; j->applied = 1;
        } else if (!strcmp(line, "restore_intent") && j->phase >= BEFORE && j->phase <= APPLIED)
            j->phase = RESTORE_INTENT;
        else if (!strcmp(line, "restored") && j->phase == RESTORE_INTENT)
            j->phase = RESTORED;
        else if (!strcmp(line, "closed") && j->phase == RESTORED) j->phase = CLOSED;
        else return -1;
        count++;
        line = end + 1;
    }
    return 0;
}

/* Linux identity guards boot/ns reuse; host fixtures deliberately say host,
 * and therefore cannot certify the Android boot contract. */
static int environment(char *buf, size_t cap) {
    const char *schema = prepared_mode ? "probe_exchange_v1" : "probe_v1";
#ifdef __linux__
    char boot[80], ns[80];
    int fd = open("/proc/sys/kernel/random/boot_id", O_RDONLY | O_CLOEXEC), n;
    if (fd < 0) return -1;
    n = read_fd(fd, boot, sizeof(boot));
    close(fd);
    if (n != 37 || boot[36] != '\n') return -1;
    boot[36] = 0;
    n = (int)readlink("/proc/self/ns/mnt", ns, sizeof(ns) - 1);
    if (n <= 0 || n >= (int)sizeof(ns) - 1) return -1;
    ns[n] = 0;
    n = snprintf(buf, cap, "%s\n%s\n%s\n", schema, boot, ns);
#else
    int n = snprintf(buf, cap, "%s\nhost_fixture_only\n", schema);
#endif
    return n > 0 && (size_t)n < cap ? 0 : -1;
}

static int environment_matches(int dir) {
    char expected[256], current[256];
    return read_owned(dir, "environment", expected, sizeof(expected)) >= 0 &&
           !environment(current, sizeof(current)) && !strcmp(expected, current);
}

static int stage_hold(int dir, const char *stage, const char *selected, long hold) {
    char text[32];
    int fd, rc;
    if (strcmp(stage, selected)) return 0;
    fd = safe_open(dir, "stage", O_WRONLY);
    if (fd < 0) return -1;
    snprintf(text, sizeof(text), "%s\n", stage);
    rc = ftruncate(fd, 0);
    if (!rc) rc = write_all(fd, text, strlen(text));
    if (!rc) rc = fsync(fd);
    if (close(fd)) rc = -1;
    return rc ? -1 : pause_ms(hold);
}

/* Return 0 only for ENOENT, 1 for a fully validated present object. An unsafe
 * object or any other read/open failure is not the same as an absent object. */
static int optional_object(int dir, const char *name, char *buf, size_t cap) {
    struct stat st;
    int fd = openat(dir, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK), n;
    if (fd < 0) return errno == ENOENT ? 0 : -1;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
        st.st_uid != geteuid() || (st.st_mode & 0777) != 0600) {
        close(fd); return -1;
    }
    n = read_fd(fd, buf, cap);
    close(fd);
    return n >= 0 && strlen(buf) == (size_t)n ? 1 : -1;
}

static int setup_matches(int dir) {
    char buf[256], expected[256], current[128];
    int fixture, resource, fd, present;
    /* No process may be released before the durable before record. A process
     * marker in this branch is conflicting evidence, not a disposable file. */
    if (optional_object(dir, "process", buf, sizeof(buf)) != 0) return 0;
    present = optional_object(dir, "environment", buf, sizeof(buf));
    if (present < 0 || environment(expected, sizeof(expected)) ||
        (present && strcmp(buf, expected))) return 0;
    present = optional_object(dir, "stage", buf, sizeof(buf));
    if (present < 0 || (present && *buf)) return 0;
    present = optional_object(dir, "op.lock", buf, sizeof(buf));
    if (present < 0 || (present && *buf)) return 0;
    fixture = optional_object(dir, "fixture", buf, sizeof(buf));
    if (fixture < 0 || (fixture && strcmp(buf, baseline))) return 0;
    resource = optional_object(dir, "resource", expected, sizeof(expected));
    if (resource < 0 || (resource && !fixture)) return 0;
    if (resource) {
        fd = safe_open(dir, "fixture", O_RDONLY);
        if (fd < 0) return 0;
        present = identity(fd, current, sizeof(current)); close(fd);
        if (present || strcmp(expected, current)) return 0;
    }
    if (prepared_mode) {
        fixture = optional_object(dir, "prepared", buf, sizeof(buf));
        if (fixture < 0 || (fixture && strcmp(buf, candidate))) return 0;
        resource = optional_object(dir, "prepared_resource", expected, sizeof(expected));
        if (resource < 0 || (resource && !fixture)) return 0;
        if (resource) {
            fd = safe_open(dir, "prepared", O_RDONLY);
            if (fd < 0) return 0;
            present = identity(fd, current, sizeof(current)); close(fd);
            if (present || strcmp(expected, current)) return 0;
        }
    }
    return 1;
}

static int abort_setup(int dir, const struct journal *j) {
    char buf[16];
    int lock = -1, present, rc = CONFLICT;
    present = optional_object(dir, "op.lock", buf, sizeof(buf));
    if (present < 0 || (present && *buf)) return CONFLICT;
    if (present) {
        lock = safe_open(dir, "op.lock", O_RDWR);
        if (lock < 0) return CONFLICT;
        if (flock(lock, LOCK_EX | LOCK_NB)) { close(lock); return BUSY; }
    }
    if (!setup_matches(dir)) goto out;
    if (j->phase == ABORTED) { rc = NOT_APPLIED; goto out; }
    if (j->phase == SETUP && append_event(dir, "abort_intent\n")) {
        rc = BLOCKED; goto out;
    }
    if (!setup_matches(dir)) goto out;
    if (append_event(dir, "aborted\n")) { rc = BLOCKED; goto out; }
    rc = NOT_APPLIED;
out:
    if (lock >= 0) close(lock);
    return rc;
}

/* Recovery tolerates absent optional setup objects; release does not. Re-read
 * every required object and the WAL before authorizing a modifying process.
 * This is not atomic against an external same-UID writer (see T02 limits). */
static int preparation_complete(int dir) {
    char buf[256];
    struct journal j;
    return setup_matches(dir) && environment_matches(dir) &&
           !fixture_value(dir, buf, sizeof(buf)) && !strcmp(buf, baseline) &&
           read_owned(dir, "op.lock", buf, sizeof(buf)) == 0 &&
           read_owned(dir, "stage", buf, sizeof(buf)) == 0 &&
           !read_journal(dir, &j) && j.phase == SETUP;
}

static int restore(int dir, const char *stage, long hold) {
    struct journal j;
    char current[64];
    int lock, rc = CONFLICT;
    if (read_journal(dir, &j)) return CONFLICT;
    if (j.phase < BEFORE) return abort_setup(dir, &j);
    lock = safe_open(dir, "op.lock", O_RDWR);
    if (lock < 0) return CONFLICT;
    if (flock(lock, LOCK_EX | LOCK_NB)) { close(lock); return BUSY; }
    if (!environment_matches(dir) || read_journal(dir, &j) || j.phase < BEFORE ||
        fixture_value(dir, current, sizeof(current))) goto out;
    if (strcmp(current, baseline)) {
        if (j.phase < INTENT || j.phase >= RESTORED || strcmp(current, candidate) ||
            (!prepared_mode && !j.applied)) goto out;
    }
    if (j.phase == CLOSED) { rc = OK; goto out; }
    if (j.phase < RESTORE_INTENT && append_event(dir, "restore_intent\n")) {
        rc = BLOCKED; goto out;
    }
    if (!strcmp(current, candidate) && replace_fixture(dir, candidate, baseline)) {
        rc = BLOCKED; goto out;
    }
    if (fixture_value(dir, current, sizeof(current)) || strcmp(current, baseline)) goto out;
    if (j.phase < RESTORED && append_event(dir, "restored\n")) { rc = BLOCKED; goto out; }
    if (stage_hold(dir, "restored", stage, hold)) { rc = BLOCKED; goto out; }
    if (!environment_matches(dir) || fixture_value(dir, current, sizeof(current)) ||
        strcmp(current, baseline)) goto out;
    if (append_event(dir, "closed\n")) { rc = BLOCKED; goto out; }
    rc = OK;
out:
    close(lock);
    return rc;
}

static int worker(int dir, int guard, int go, int ack, const char *stage, long hold) {
    char token, current[64];
    int lock;
    close(guard); /* A dead guardian's lease must NOT be retained by the child. */
    signal(SIGINT, SIG_DFL); signal(SIGTERM, SIG_DFL); signal(SIGHUP, SIG_DFL);
    lock = safe_open(dir, "op.lock", O_RDWR);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) return BLOCKED;
    if (write_all(ack, "L", 1)) return BLOCKED;
    close(ack);
    if (read(go, &token, 1) != 1 || token != 'G') return BLOCKED;
    close(go);
    if (!environment_matches(dir) || stage_hold(dir, "before", stage, hold) ||
        append_event(dir, "intent\n") || stage_hold(dir, "intent", stage, hold) ||
        replace_fixture(dir, baseline, candidate) || stage_hold(dir, "effect", stage, hold) ||
        (prepared_mode && (prepared_pair(dir, current, sizeof(current)) || strcmp(current, candidate))) ||
        append_event(dir, "applied\n") || stage_hold(dir, "applied", stage, hold))
        return BLOCKED;
    /* No close/unlock until no possible future write in this worker. */
    close(lock);
    return OK;
}

static int child_identity(pid_t pid, char *buf, size_t cap) {
#ifdef __linux__
    char path[80], data[4096], *p, *end;
    int fd, n, field;
    snprintf(path, sizeof(path), "/proc/%d/stat", (int)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    n = read_fd(fd, data, sizeof(data));
    close(fd);
    if (n < 0 || !(p = strrchr(data, ')')) || p[1] != ' ') return -1;
    p += 2; /* field 3; comm may contain spaces or parentheses */
    for (field = 3; field < 22; field++) {
        p = strchr(p, ' ');
        if (!p) return -1;
        p++;
    }
    end = strchr(p, ' ');
    if (!end || end == p) return -1;
    *end = 0;
    for (end = p; *end; end++) if (*end < '0' || *end > '9') return -1;
    n = snprintf(buf, cap, "%d %d %s\n", (int)getpid(), (int)pid, p);
#else
    int n = snprintf(buf, cap, "%d %d direct_unreaped_child_only\n", (int)getpid(), (int)pid);
#endif
    return n > 0 && (size_t)n < cap ? 0 : -1;
}

/* Signals target our unreaped direct child; PID cannot be recycled before
 * waitpid. A timeout acquiring a lock is not a successful takeover. */
static int reap_bounded(pid_t pid, int *status) {
    long long start = now_ms(), current;
    pid_t got;
    if (start < 0) return -1;
    if (kill(pid, SIGTERM)) { /* ESRCH is not proof: still require waitpid. */ }
    for (;;) {
        got = waitpid(pid, status, WNOHANG);
        if (got == pid) return 0;
        if (got < 0 && errno != EINTR) return -1;
        current = now_ms();
        if (current < 0 || current - start > 1500) return -1;
        if (current - start > 250) {
            if (kill(pid, SIGKILL)) { /* Still wait for actual reaping. */ }
        }
        if (pause_ms(10)) return -1;
    }
}

static int result(const char *operation, int rc) {
    const char *recovery = rc == NOT_APPLIED ? "not_applied" : rc == OK ? "restored" : "blocked";
    if (rc == NOT_APPLIED) rc = OK;
    printf("{\"event\":\"result\",\"operation\":\"%s\",\"recovery\":\"%s\",\"code\":%d}\n",
           operation, recovery, rc);
    fflush(NULL);
    final_event_emitted = 1;
    return rc;
}

static int run(int dir, int guard, const char *stage, long hold, long deadline) {
    char buf[256], setup[288], token;
    int fd, go[2], ack[2], status = 0, rc;
    pid_t pid, got;
    const char *outcome = "normal";
    long long start, current;
    struct pollfd p;
    if (environment(buf, sizeof(buf))) return BLOCKED;
    rc = snprintf(setup, sizeof(setup), "setup\n%s", buf);
    if (rc < 0 || (size_t)rc >= sizeof(setup)) return BLOCKED;
    if (new_file(dir, "journal", setup)) return CONFLICT;
    if (new_file(dir, "environment", buf) ||
        new_file(dir, "op.lock", "") || new_file(dir, "fixture", baseline)) return CONFLICT;
    fd = safe_open(dir, "fixture", O_RDONLY);
    if (fd < 0) return BLOCKED;
    rc = identity(fd, buf, sizeof(buf)); close(fd);
    if (rc || new_file(dir, "resource", buf)) return BLOCKED;
    if (prepared_mode) {
        if (new_file(dir, "prepared", candidate)) return BLOCKED;
        fd = safe_open(dir, "prepared", O_RDONLY);
        if (fd < 0) return BLOCKED;
        rc = identity(fd, buf, sizeof(buf)); close(fd);
        if (rc || new_file(dir, "prepared_resource", buf)) return BLOCKED;
    }
    if (new_file(dir, "stage", "")) return BLOCKED;
    if (!preparation_complete(dir)) return CONFLICT;
    /* A cancellation before the release record takes the same no-effect route
     * as an injected death. Once before is durable, the normal restore route
     * applies even when cancellation arrives just afterwards. */
    if (cancellation) return result("cancelled", restore(dir, "", 0));
    if (append_event(dir, "before\n")) return BLOCKED;
    if (pipe(go)) return BLOCKED;
    if (pipe(ack)) { close(go[0]); close(go[1]); return BLOCKED; }
    pid = fork();
    if (pid < 0) {
        close(go[0]); close(go[1]); close(ack[0]); close(ack[1]); return BLOCKED;
    }
    if (!pid) {
        close(go[1]); close(ack[0]);
        rc = worker(dir, guard, go[0], ack[1], stage, hold);
        _exit(rc);
    }
    close(go[0]); close(ack[1]);
    p.fd = ack[0]; p.events = POLLIN; p.revents = 0;
    rc = poll(&p, 1, 1000);
    if (rc != 1 || read(ack[0], &token, 1) != 1 || token != 'L' ||
        child_identity(pid, buf, sizeof(buf)) || new_file(dir, "process", buf)) {
        close(ack[0]); close(go[1]);
        if (reap_bounded(pid, &status)) return BLOCKED;
        return result(cancellation ? "cancelled" : "worker_died", restore(dir, "", 0));
    }
    close(ack[0]);
    if (write_all(go[1], "G", 1)) {
        close(go[1]);
        if (reap_bounded(pid, &status)) return BLOCKED;
        return result(cancellation ? "cancelled" : "worker_died", restore(dir, "", 0));
    }
    close(go[1]);
    printf("{\"event\":\"ready\",\"supervisor_pid\":%d,\"worker_pid\":%d}\n", (int)getpid(), (int)pid);
    fflush(NULL);
    start = now_ms();
    if (start < 0) { if (reap_bounded(pid, &status)) return BLOCKED; return BLOCKED; }
    for (;;) {
        got = waitpid(pid, &status, WNOHANG);
        if (got == pid) {
            if (!WIFEXITED(status) || WEXITSTATUS(status) != OK) outcome = "worker_died";
            break;
        }
        if (got < 0 && errno != EINTR) return BLOCKED;
        current = now_ms();
        if (cancellation || current < 0 || current - start >= deadline) {
            outcome = cancellation ? "cancelled" : "deadline";
            if (reap_bounded(pid, &status)) return result(outcome, BLOCKED);
            break;
        }
        if (pause_ms(10)) return BLOCKED;
    }
    return result(outcome, restore(dir, stage, hold));
}

static int milliseconds(const char *s, long min, long max, long *out) {
    long n = 0;
    if (!*s) return -1;
    for (; *s; s++) {
        if (*s < '0' || *s > '9' || n > max / 10) return -1;
        n = n * 10 + (*s - '0');
        if (n > max) return -1;
    }
    if (n < min) return -1;
    *out = n; return 0;
}

int main(int argc, char **argv) {
    struct stat st;
    int dir, guard, rc, recovery;
    long hold = 0, deadline = 0;
    const char *stage = "applied";
    /* Earliest userspace handling we control; signals before main/exec cannot
     * be caught here. No filesystem mutation precedes these installations. */
    signal(SIGINT, request_cancel); signal(SIGTERM, request_cancel);
    signal(SIGHUP, request_cancel); signal(SIGPIPE, SIG_IGN);
    if (argc < 3) return result("usage", USAGE);
    prepared_mode = !strcmp(argv[1], "probe-exchange-run") || !strcmp(argv[1], "probe-exchange-recover");
    recovery = !strcmp(argv[1], "probe-recover") || !strcmp(argv[1], "probe-exchange-recover");
    if (recovery) {
        if (argc != 3) return result("usage", USAGE);
    } else {
        if ((strcmp(argv[1], "probe-run") && strcmp(argv[1], "probe-exchange-run")) || (argc != 5 && argc != 6) ||
            milliseconds(argv[3], 0, 30000, &hold) ||
            milliseconds(argv[4], 50, 60000, &deadline)) return result("usage", USAGE);
        if (argc == 6) stage = argv[5];
        if (strcmp(stage, "before") && strcmp(stage, "intent") && strcmp(stage, "effect") &&
            strcmp(stage, "applied") && strcmp(stage, "restored")) return result("usage", USAGE);
    }
    if (argv[2][0] != '/') return result("usage", USAGE);
    dir = open(argv[2], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) return result("preflight", CONFLICT);
    if (fstat(dir, &st) || !S_ISDIR(st.st_mode) || st.st_uid != geteuid() ||
        (st.st_mode & 0777) != 0700) { close(dir); return result("preflight", CONFLICT); }
    guard = safe_open(dir, "guard.lock", O_RDWR | (recovery ? 0 : O_CREAT));
    if (guard < 0) { close(dir); return result("preflight", CONFLICT); }
    if (flock(guard, LOCK_EX | LOCK_NB)) {
        close(guard); close(dir); return result("busy", BUSY);
    }
    if (fsync(dir)) { close(guard); close(dir); return result("preflight", BLOCKED); }
    rc = recovery ? result("recovered", restore(dir, "", 0)) : run(dir, guard, stage, hold, deadline);
    if (!final_event_emitted) result("startup_failed", rc);
    close(guard); close(dir);
    return rc;
}
