#ifndef MT6639_PROBE_ABI_H
#define MT6639_PROBE_ABI_H
#if defined(__APPLE__) && !defined(_DARWIN_C_SOURCE)
#define _DARWIN_C_SOURCE 1
#endif
#ifndef _DEFAULT_SOURCE
#define _DEFAULT_SOURCE 1
#endif

/* Existing Termux tcc has no Bionic development headers on the target. Keep the
 * small LP64 AArch64 ABI surface explicit; never use these layouts elsewhere.
 * The Linux arm64 stat layout is the Bionic arm64 stat ABI (128 bytes).
 */
#ifdef MT_ANDROID_AARCH64
#if !defined(__aarch64__) || __SIZEOF_POINTER__ != 8
#error MT_ANDROID_AARCH64_requires_AArch64_LP64
#endif
typedef unsigned long size_t;
typedef long ssize_t;
typedef int pid_t;
typedef unsigned int uid_t;
typedef unsigned int mode_t;
typedef long off_t;
typedef long time_t;
typedef int sig_atomic_t;
struct timespec { time_t tv_sec; long tv_nsec; };
struct stat {
    unsigned long st_dev, st_ino;
    unsigned int st_mode, st_nlink, st_uid, st_gid;
    unsigned long st_rdev, __pad1;
    long st_size;
    int st_blksize, __pad2;
    long st_blocks;
    struct timespec st_atim, st_mtim, st_ctim;
    unsigned int __unused[2];
};
typedef char mt_stat_size_check[sizeof(struct stat) == 128 ? 1 : -1];
struct pollfd { int fd; short events, revents; };
extern int *__errno(void);
#define errno (*__errno())
#define EINTR 4
#define ENOENT 2
#define EAGAIN 11
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_CREAT 0100
#define O_EXCL 0200
#define O_APPEND 02000
#define O_TRUNC 01000
#define O_NONBLOCK 04000
/* arm64 overrides asm-generic/x86 values: using the latter opens with O_DIRECT
 * and silently loses NOFOLLOW. Verified against Bionic asm-arm64/asm/fcntl.h. */
#define O_DIRECTORY 040000
#define O_NOFOLLOW 0100000
#define O_CLOEXEC 02000000
#define AT_FDCWD (-100)
#define S_ISREG(m) (((m) & 0170000) == 0100000)
#define S_ISDIR(m) (((m) & 0170000) == 0040000)
#define LOCK_EX 2
#define LOCK_NB 4
#define CLOCK_MONOTONIC 1
#define WNOHANG 1
#define WIFEXITED(s) (((s) & 127) == 0)
#define WEXITSTATUS(s) (((s) >> 8) & 255)
#define SIGINT 2
#define SIGKILL 9
#define SIGPIPE 13
#define SIGTERM 15
#define SIGHUP 1
#define SIG_DFL ((void (*)(int))0)
#define SIG_IGN ((void (*)(int))1)
#define POLLIN 1
#define NULL ((void *)0)
extern int open(const char *, int, ...);
extern int openat(int, const char *, int, ...);
extern int close(int);
extern ssize_t read(int, void *, size_t);
extern ssize_t write(int, const void *, size_t);
extern int fstat(int, struct stat *);
extern int fsync(int);
extern int ftruncate(int, off_t);
extern int flock(int, int);
extern uid_t geteuid(void);
extern pid_t getpid(void);
extern pid_t fork(void);
extern int pipe(int *);
extern int poll(struct pollfd *, unsigned long, int);
extern int kill(pid_t, int);
extern pid_t waitpid(pid_t, int *, int);
extern void _exit(int);
extern void (*signal(int, void (*)(int)))(int);
extern int clock_gettime(int, struct timespec *);
extern int nanosleep(const struct timespec *, struct timespec *);
extern ssize_t readlink(const char *, char *, size_t);
extern unsigned long strlen(const char *);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, size_t);
extern char *strchr(const char *, int);
extern char *strrchr(const char *, int);
extern int snprintf(char *, size_t, const char *, ...);
extern int printf(const char *, ...);
extern int fflush(void *);
#else
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#endif
#endif
