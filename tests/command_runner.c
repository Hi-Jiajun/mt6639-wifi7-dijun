/*
 * TDD test suite for mt_command native subprocess runner.
 * Exercises actual subprocesses (not mocks) on host macOS and Android AArch64.
 */
#if defined(__APPLE__) && !defined(_DARWIN_C_SOURCE)
#define _DARWIN_C_SOURCE 1
#endif
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif

#ifdef MT_ANDROID_AARCH64
#include "../native/recovery/abi.h"
extern void *memset(void *, int, size_t);
#ifndef SIGUSR1
#define SIGUSR1 10
#endif
#else
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#endif

#include "../native/recovery/command.h"

#ifdef MT_ANDROID_AARCH64
#define SHELL_PATH "/system/bin/sh"
#else
#define SHELL_PATH "/bin/sh"
#endif

static int g_failures = 0;

static void check(int condition, const char *name) {
    if (condition) {
        printf("PASS: %s\n", name);
    } else {
        printf("FAIL: %s\n", name);
        g_failures++;
    }
}

static long long test_now_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return -1;
    return (long long)ts.tv_sec * 1000 + (long long)(ts.tv_nsec / 1000000);
}

/* 1. Success exact output */
static void test_success_exact_output(void) {
    char out[128];
    memset(out, 0xaa, sizeof(out));
    char *const argv[] = {SHELL_PATH, "-c", "printf 'hello mt6639\\n'", NULL};
    int rc = mt_command(argv, 2000, out, sizeof(out));
    check(rc == 0, "success_rc_zero");
    check(strcmp(out, "hello mt6639\n") == 0, "success_exact_output_matches");
}

/* 2. Exit 7 after valid-looking output (must failure) */
static void test_exit7_after_valid_looking_output(void) {
    char out[128];
    memset(out, 0xaa, sizeof(out));
    char *const argv[] = {SHELL_PATH, "-c", "printf '{\"status\":\"ok\",\"ready\":1}\\n'; exit 7", NULL};
    int rc = mt_command(argv, 2000, out, sizeof(out));
    check(rc == 1, "exit7_returns_nonzero_exit1");
    check(strcmp(out, "{\"status\":\"ok\",\"ready\":1}\n") == 0, "exit7_preserves_captured_output");
}

/* 3. Malformed text belongs consumer not runner */
static void test_malformed_text_belongs_consumer(void) {
    char out[128];
    memset(out, 0xaa, sizeof(out));
    char *const argv[] = {SHELL_PATH, "-c", "printf '\\xff\\xfe\\x00malformed_text\\n'", NULL};
    int rc = mt_command(argv, 2000, out, sizeof(out));
    check(rc == 0, "malformed_text_runner_succeeds");
    check((unsigned char)out[0] == 0xff && (unsigned char)out[1] == 0xfe, "malformed_bytes_preserved");
}

/* 4. Missing executable */
static void test_missing_executable(void) {
    char out[128];
    memset(out, 0xaa, sizeof(out));
    char *const argv[] = {"/nonexistent_command_binary_12345", NULL};
    int rc = mt_command(argv, 2000, out, sizeof(out));
    check(rc == 126, "missing_executable_returns_exec_error126");
    check(out[0] == '\0', "missing_executable_clears_output");
}

/* 5. Overflowing output */
static void test_overflowing_output(void) {
    char out[16];
    memset(out, 0xaa, sizeof(out));
    char *const argv[] = {SHELL_PATH, "-c", "printf '0123456789abcdefghijklmnopqrstuvwxyz'", NULL};
    int rc = mt_command(argv, 2000, out, sizeof(out));
    check(rc == 125, "overflowing_output_returns_overflow125");
    check(strlen(out) == 15, "overflowing_output_bounded_at_cap_minus_one");
    check(out[15] == '\0', "overflowing_output_nul_terminated");
}

/* 6. Hanging child (Timeout) */
static void test_hanging_child(void) {
    char out[128];
    memset(out, 0xaa, sizeof(out));
    char *const argv[] = {SHELL_PATH, "-c", "sleep 5", NULL};
    long long t0 = test_now_ms();
    int rc = mt_command(argv, 150, out, sizeof(out));
    long long elapsed = test_now_ms() - t0;
    check(rc == 124, "hanging_child_returns_timeout124");
    check(elapsed >= 130 && elapsed < 2000, "hanging_child_bounded_duration");
    check(out[0] == '\0', "hanging_child_nul_terminated");
}
static void test_early_eof_does_not_extend_deadline(void) {
    char out[16];
    char *const argv[] = {SHELL_PATH, "-c", "exec 1>&- 2>&-; sleep 5", NULL};
    long long start = test_now_ms();
    int rc = mt_command(argv, 150, out, sizeof out);
    long long elapsed = test_now_ms() - start;
    check(rc == 124, "early_eof_still_times_out124");
    check(elapsed < 700, "early_eof_preserves_original_deadline");
}

/* 7. Forked descendant holding pipe after parent exit */
static void test_forked_descendant_holding_pipe(void) {
    char out[128];
    memset(out, 0xaa, sizeof(out));
    char *const argv[] = {SHELL_PATH, "-c", "(sleep 5 &) ; printf 'parent_exit\\n' ; exit 0", NULL};
    long long t0 = test_now_ms();
    int rc = mt_command(argv, 200, out, sizeof(out));
    long long elapsed = test_now_ms() - t0;
    check(rc == 124, "descendant_holding_pipe_times_out124");
    check(elapsed >= 180 && elapsed < 2500, "descendant_holding_pipe_bounded_and_killed");
    check(strcmp(out, "parent_exit\n") == 0, "descendant_pipe_captured_early_output");
}

/* 8. Signal interruption when feasible */
static volatile sig_atomic_t g_sigusr1_count = 0;
static void handle_sigusr1(int sig) {
    (void)sig;
    g_sigusr1_count++;
}

static void test_signal_interruption(void) {
    void (*old_handler)(int) = signal(SIGUSR1, handle_sigusr1);

    char out[128];
    memset(out, 0xaa, sizeof(out));
    /* Child sends SIGUSR1 to parent while parent is polling, then outputs */
    char *const argv[] = {SHELL_PATH, "-c", "kill -USR1 $PPID ; sleep 0.05 ; printf 'interrupted_and_resumed\\n'", NULL};
    int rc = mt_command(argv, 500, out, sizeof(out));
    check(rc == 0, "signal_interruption_handles_eintr_and_succeeds");
    check(g_sigusr1_count >= 1, "signal_handler_was_executed");
    check(strcmp(out, "interrupted_and_resumed\n") == 0, "signal_interruption_output_intact");

    /* Verify runner did not mutate caller signal handler */
    void (*cur_handler)(int) = signal(SIGUSR1, old_handler);
    check(cur_handler == handle_sigusr1, "runner_did_not_mutate_caller_signal_handler");
}

/* 9. Invalid arguments */
static void test_invalid_arguments(void) {
    char out[16];
    char *const argv_ok[] = {SHELL_PATH, "-c", "echo ok", NULL};
    char *const argv_empty[] = {NULL};

    check(mt_command(argv_ok, 0, out, sizeof(out)) == 126, "budget_underflow_rejected126");
    check(mt_command(argv_ok, 20001, out, sizeof(out)) == 126, "budget_overflow_rejected126");
    check(mt_command(argv_ok, 100, out, 1) == 126, "cap_too_small_rejected126");
    check(mt_command(NULL, 100, out, sizeof(out)) == 126, "null_argv_rejected126");
    check(mt_command(argv_empty, 100, out, sizeof(out)) == 126, "empty_argv_rejected126");
    check(mt_command(argv_ok, 100, NULL, sizeof(out)) == 126, "null_out_rejected126");
}

int main(void) {
    printf("=== Starting mt_command tests ===\n");
    test_success_exact_output();
    test_exit7_after_valid_looking_output();
    test_malformed_text_belongs_consumer();
    test_missing_executable();
    test_overflowing_output();
    test_hanging_child();
    test_early_eof_does_not_extend_deadline();
    test_forked_descendant_holding_pipe();
    test_signal_interruption();
    test_invalid_arguments();

    printf("=== Finished mt_command tests: %d failures ===\n", g_failures);
    return g_failures == 0 ? 0 : 1;
}
