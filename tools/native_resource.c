/* SPDX-License-Identifier: MIT
 * One direct child per invocation; Linux wait4 resource data only.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

static int write_all(int fd, const void *data, size_t size) {
    const char *position = data;
    while (size > 0) {
        ssize_t written = write(fd, position, size);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) {
            if (written == 0) errno = EIO;
            return -1;
        }
        position += written;
        size -= (size_t)written;
    }
    return 0;
}

static int report_error(const char *operation) {
    int saved = errno;
    fprintf(stderr, "native-resource: %s: %s\n", operation, strerror(saved));
    return 1;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        puts("CppPerfLab native-resource 1");
        return 0;
    }
    if (argc < 5 || strcmp(argv[1], "--output") != 0 || argv[2][0] != '/' ||
        strcmp(argv[3], "--") != 0 || argv[4][0] == '\0') {
        fprintf(stderr, "usage: native-resource --output /absolute/file.json -- TARGET [ARG ...]\n");
        return 2;
    }
    int output = open(argv[2], O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0666);
    if (output < 0) return report_error("open output");
    int error_pipe[2];
    if (pipe2(error_pipe, O_CLOEXEC) < 0) {
        int saved = errno; close(output); errno = saved;
        return report_error("create exec-error pipe");
    }
    pid_t child = fork();
    if (child < 0) {
        int saved = errno; close(error_pipe[0]); close(error_pipe[1]); close(output); errno = saved;
        return report_error("fork");
    }
    if (child == 0) {
        close(error_pipe[0]);
        execvp(argv[4], &argv[4]);
        int exec_error = errno;
        (void)write_all(error_pipe[1], &exec_error, sizeof(exec_error));
        dprintf(STDERR_FILENO, "native-resource: exec %s: %s\n", argv[4], strerror(exec_error));
        _exit(127);
    }
    close(error_pipe[1]);
    int exec_error = 0;
    size_t received = 0;
    while (received < sizeof(exec_error)) {
        ssize_t n = read(error_pipe[0], (char *)&exec_error + received, sizeof(exec_error) - received);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) {
            int saved = errno; close(error_pipe[0]); close(output); errno = saved;
            return report_error("read exec-error pipe");
        }
        if (n == 0) break;
        received += (size_t)n;
    }
    close(error_pipe[0]);
    if (received != 0 && received != sizeof(exec_error)) {
        close(output);
        fprintf(stderr, "native-resource: incomplete exec-error status\n");
        return 1;
    }
    int status;
    struct rusage usage;
    pid_t waited;
    do { waited = wait4(child, &status, 0, &usage); } while (waited < 0 && errno == EINTR);
    if (waited != child) {
        int saved = errno; close(output); errno = saved;
        return report_error("wait4");
    }
    int exited = WIFEXITED(status);
    int signaled = WIFSIGNALED(status);
    if (!exited && !signaled) {
        close(output);
        fprintf(stderr, "native-resource: wait4 returned a nonterminal child status\n");
        return 1;
    }
    char exit_text[32], signal_text[32], exec_text[32];
    if (exited) snprintf(exit_text, sizeof(exit_text), "%d", WEXITSTATUS(status));
    else strcpy(exit_text, "null");
    if (signaled) snprintf(signal_text, sizeof(signal_text), "%d", WTERMSIG(status));
    else strcpy(signal_text, "null");
    if (received) snprintf(exec_text, sizeof(exec_text), "%d", exec_error);
    else strcpy(exec_text, "null");
    char json[2048];
    int length = snprintf(json, sizeof(json),
        "{\"schema_version\":1,\"collector\":\"linux-wait4\",\"version\":1,"
        "\"child_pid\":%lld,\"wait_status\":%d,\"exited\":%s,\"exit_code\":%s,\"signal\":%s,\"exec_errno\":%s,"
        "\"rusage\":{\"max_rss_kib\":%lld,\"user_time\":{\"seconds\":%lld,\"microseconds\":%lld},"
        "\"system_time\":{\"seconds\":%lld,\"microseconds\":%lld},\"minor_faults\":%lld,\"major_faults\":%lld,"
        "\"voluntary_context_switches\":%lld,\"involuntary_context_switches\":%lld,"
        "\"filesystem_inputs\":%lld,\"filesystem_outputs\":%lld}}\n",
        (long long)child, status, exited ? "true" : "false", exit_text, signal_text, exec_text,
        (long long)usage.ru_maxrss, (long long)usage.ru_utime.tv_sec, (long long)usage.ru_utime.tv_usec,
        (long long)usage.ru_stime.tv_sec, (long long)usage.ru_stime.tv_usec,
        (long long)usage.ru_minflt, (long long)usage.ru_majflt, (long long)usage.ru_nvcsw,
        (long long)usage.ru_nivcsw, (long long)usage.ru_inblock, (long long)usage.ru_oublock);
    if (length < 0 || (size_t)length >= sizeof(json)) {
        close(output);
        fprintf(stderr, "native-resource: report formatting failed\n");
        return 1;
    }
    if (write_all(output, json, (size_t)length) < 0) {
        int saved = errno; close(output); errno = saved;
        return report_error("write output");
    }
    if (close(output) < 0) return report_error("close output");
    return exited ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
