/* Read-only A7Z Sync Read timing experiment. No motor-write encoder exists here.
 * Caller must suspend the read-only robotd and guarantee SIGCONT on every exit.
 * Keeps the existing UART configuration; sends only 0x82, address 56, length 15.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static const uint8_t all_ids[] = {20,21,22,23,24,30,31,32,33,34,10,11,12,13,14,200};

static int64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) { perror("clock_gettime"); exit(1); }
    return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}

static void sleep_until(int64_t when) {
    struct timespec t = {when / 1000000000, when % 1000000000};
    int rc;
    do { rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL); } while (rc == EINTR);
    if (rc) { errno = rc; perror("clock_nanosleep"); exit(1); }
}

static int known_id(uint8_t id) {
    for (size_t i = 0; i < sizeof(all_ids); ++i) if (all_ids[i] == id) return 1;
    return 0;
}

typedef struct {
    unsigned mask, bad, discarded, rx, status_errors;
} Reply;

static Reply sync_group(int fd, const uint8_t *ids, size_t count, int64_t deadline) {
    Reply r = {0};
    uint8_t request[11] = {255,255,254,0,0x82,56,15};
    if (!count || count > 3) { fputs("group exceeds 64-byte RX FIFO\n", stderr); exit(1); }
    for (size_t i = 0; i < count; ++i) {
        if (!known_id(ids[i]) || (ids[i] == 200 && i != count - 1)) exit(1);
        for (size_t j = 0; j < i; ++j) if (ids[i] == ids[j]) exit(1);
        request[7+i] = ids[i];
    }
    request[3] = (uint8_t)(count + 4);
    unsigned sum = 0;
    for (size_t i = 2; i < 7 + count; ++i) sum += request[i];
    request[7+count] = (uint8_t)~sum;
    if (tcflush(fd, TCIFLUSH) || write(fd, request, 8+count) != (ssize_t)(8+count)) {
        perror("Sync Read request; not retried"); exit(1);
    }
    uint8_t bytes[512];
    size_t used = 0;
    while (now_ns() < deadline && r.mask != (1u << count) - 1) {
        int64_t left = deadline - now_ns();
        if (left <= 0) break;
        int wait_ms = (int)((left + 999999) / 1000000);
        if (wait_ms > 2) wait_ms = 2;
        struct pollfd pollfd = {fd, POLLIN, 0};
        int ready = poll(&pollfd, 1, wait_ms);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) { perror("poll"); exit(1); }
        if (!ready) continue;
        ssize_t received = read(fd, bytes + used, sizeof(bytes) - used);
        if (received < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (received < 0) { perror("read"); exit(1); }
        used += (size_t)received;
        r.rx += (unsigned)received;
        size_t consumed = 0;
        while (used - consumed >= 4) {
            uint8_t *frame = bytes + consumed;
            if (frame[0] != 255 || frame[1] != 255 || frame[2] >= 254 || frame[3] != 17) {
                ++consumed; ++r.discarded; continue;
            }
            if (used - consumed < 21) break;
            sum = 0;
            for (size_t j = 2; j < 21; ++j) sum += frame[j];
            if ((sum & 255) != 255) { ++consumed; ++r.bad; continue; }
            size_t match;
            for (match = 0; match < count && ids[match] != frame[2]; ++match) {}
            if (match == count || (r.mask & (1u << match))) ++r.discarded;
            else if (frame[4]) ++r.status_errors;
            else r.mask |= 1u << match;
            consumed += 21;
        }
        memmove(bytes, bytes + consumed, used - consumed);
        used -= consumed;
        if (used == sizeof(bytes)) { fputs("RX decoder overflow\n", stderr); exit(1); }
    }
    r.discarded += (unsigned)used;
    return r;
}

static void run_case(int fd, FILE *output, const char *name, const uint8_t *ids,
                     size_t count, unsigned gap_us, unsigned cycles) {
    unsigned success = 0, checksum = 0, discarded = 0, missing[256] = {0};
    int64_t began = now_ns(), next = began, worst = 0;
    for (unsigned cycle = 0; cycle < cycles; ++cycle) {
        int64_t start = now_ns(), deadline = start + 20000000;
        int ok = 1;
        for (size_t offset = 0; offset < count; offset += 3) {
            if (offset && gap_us) sleep_until(now_ns() + (int64_t)gap_us * 1000);
            if (now_ns() >= deadline) { ok = 0; break; }
            size_t n = count - offset < 3 ? count - offset : 3;
            Reply r = sync_group(fd, ids + offset, n, deadline);
            checksum += r.bad; discarded += r.discarded;
            for (size_t j = 0; j < n; ++j) if (!(r.mask & (1u << j))) ++missing[ids[offset+j]];
            if (r.mask != (1u << n) - 1 || r.bad || r.discarded || r.status_errors) {
                ok = 0;
                if (cycle < 5) fprintf(stderr, "%s failed cycle=%u group=%zu mask=%u rx=%u bad=%u discard=%u\n",
                                       name, cycle+1, offset/3+1, r.mask, r.rx, r.bad, r.discarded);
                break;
            }
        }
        int64_t elapsed = now_ns() - start;
        if (elapsed >= 20000000) ok = 0;
        if (elapsed > worst) worst = elapsed;
        success += (unsigned)ok;
        next += 20000000;
        int64_t now = now_ns();
        if (next < now) next += ((now-next)/20000000 + 1) * 20000000;
        sleep_until(next);
    }
    double span = (now_ns() - began) / 1e9;
    fprintf(output, "{\"case\":\"%s\",\"cycles\":%u,\"success\":%u,\"failures\":%u,\"gap_us\":%u,\"hz\":%.6f,\"worst_ms\":%.6f,\"bad_checksums\":%u,\"discarded\":%u,\"missing\":{",
            name, cycles, success, cycles-success, gap_us, cycles/span, worst/1e6, checksum, discarded);
    int comma = 0;
    for (unsigned id = 0; id < 256; ++id) if (missing[id]) {
        fprintf(output, "%s\"%u\":%u", comma ? "," : "", id, missing[id]); comma = 1;
    }
    fputs("}}\n", output); fflush(output);
}

int main(int argc, char **argv) {
    if (argc != 4) { fputs("usage: sync_matrix PAUSED_PID CYCLES OUTPUT\n", stderr); return 1; }
    int pid = atoi(argv[1]);
    unsigned cycles = (unsigned)atoi(argv[2]);
    if (pid <= 1 || cycles < 1 || cycles > 3000) return 1;
    /* Root can open a TIOCEXCL port. Require the specified paused process to
     * be the read-only daemon, rather than allowing an unrelated stopped PID. */
    char command_path[128], command[1024];
    snprintf(command_path, sizeof(command_path), "/proc/%d/cmdline", pid);
    FILE *command_file = fopen(command_path, "r");
    if (!command_file) return 1;
    size_t command_size = fread(command, 1, sizeof(command)-1, command_file);
    fclose(command_file);
    command[command_size] = 0;
    int read_only = 0;
    for (size_t i = 0; i < command_size; i += strlen(command+i)+1)
        if (!strcmp(command+i, "--read-only")) read_only = 1;
    if (strcmp(command, "/opt/robot/feetech-ft5-r5/robotd") || !read_only) {
        fputs("expected the paused read-only robotd\n", stderr); return 1;
    }
    char path[128], line[512];
    snprintf(path, sizeof(path), "/proc/%d/status", pid);
    FILE *status = fopen(path, "r");
    int stopped = 0;
    if (!status) return 1;
    while (fgets(line, sizeof(line), status)) if (strncmp(line, "State:", 6) == 0 && strchr(line, 'T')) stopped = 1;
    fclose(status);
    if (!stopped) { fputs("robotd must be paused first\n", stderr); return 1; }
    int fd = open("/dev/ttyS2", O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) { perror("open tty"); return 1; }
    struct termios attrs;
    if (tcgetattr(fd, &attrs)) {
        perror("tcgetattr"); close(fd); return 1;
    }
    speed_t input = cfgetispeed(&attrs), output_speed = cfgetospeed(&attrs);
    /* Bullseye glibc returns the legacy Linux speed code 0x1008, whereas
     * newer cross headers can define B1000000 as the numeric speed itself.
     * Accept both representations; never change the existing port's speed. */
    int output_ok = output_speed == B1000000 || output_speed == 0x1008;
    int input_ok = input == B0 || input == B1000000 || input == 0x1008;
    if (!input_ok || !output_ok) {
        fprintf(stderr, "existing UART must remain B1000000: input=%lu output=%lu expected=%lu cflag=%lx\n",
                (unsigned long)input, (unsigned long)output_speed, (unsigned long)B1000000,
                (unsigned long)attrs.c_cflag);
        close(fd); return 1;
    }
    FILE *output = fopen(argv[3], "wx");
    if (!output) { perror("output"); close(fd); return 1; }
    uint8_t isolated[] = {31,32,33}, reversed[] = {33,32,31}, swapped[] = {31,33,32};
    run_case(fd, output, "isolated_31_32_33", isolated, 3, 0, cycles);
    run_case(fd, output, "isolated_33_32_31", reversed, 3, 0, cycles);
    run_case(fd, output, "isolated_31_33_32", swapped, 3, 0, cycles);
    run_case(fd, output, "full_gap0", all_ids, 16, 0, cycles);
    run_case(fd, output, "full_gap100", all_ids, 16, 100, cycles);
    run_case(fd, output, "full_gap300", all_ids, 16, 300, cycles);
    run_case(fd, output, "full_gap1000", all_ids, 16, 1000, cycles);
    uint8_t alternate[16]; memcpy(alternate, all_ids, sizeof(alternate));
    alternate[7] = 33; alternate[8] = 32;
    run_case(fd, output, "full_group3_swap", alternate, 16, 0, cycles);
    const uint8_t head_first[] = {31,32,33,20,21,22,23,24,30,34,10,11,12,13,14,200};
    run_case(fd, output, "full_head_first", head_first, 16, 0, cycles);
    fclose(output); close(fd); return 0;
}
