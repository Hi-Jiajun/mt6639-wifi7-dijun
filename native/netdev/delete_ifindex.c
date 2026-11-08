#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <linux/genetlink.h>
#include <linux/netlink.h>
#include <linux/nl80211.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#define REPLY_CAPACITY 8192
#define REQUEST_CAPACITY 512
#define NL80211_FAMILY_VERSION 1

static int attribute_is_valid(const struct nlattr *attribute, int remaining) {
    return remaining >= NLA_HDRLEN && attribute->nla_len >= NLA_HDRLEN &&
        attribute->nla_len <= remaining;
}

struct request_buffer {
    struct nlmsghdr netlink;
    struct genlmsghdr generic;
    unsigned char attributes[REQUEST_CAPACITY];
};

static int parse_ifindex(const char *text, int *result) {
    char *end = NULL;
    long value;
    if (text == NULL || *text == '\0') return -1;
    for (const char *p = text; *p != '\0'; ++p) {
        if (*p < '0' || *p > '9') return -1;
    }
    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value <= 0 || value > INT_MAX)
        return -1;
    *result = (int)value;
    return 0;
}

static int add_attribute(struct request_buffer *request, size_t *used,
                         uint16_t type, const void *data, size_t length) {
    size_t raw = NLA_HDRLEN + length;
    size_t aligned = NLA_ALIGN(raw);
    if (*used + aligned > sizeof(request->attributes) || raw > UINT16_MAX)
        return -1;
    struct nlattr attribute = {.nla_len = (uint16_t)raw, .nla_type = type};
    memcpy(request->attributes + *used, &attribute, NLA_HDRLEN);
    memcpy(request->attributes + *used + NLA_HDRLEN, data, length);
    if (aligned > raw)
        memset(request->attributes + *used + raw, 0, aligned - raw);
    *used += aligned;
    return 0;
}

static int send_request(int fd, struct request_buffer *request, uint16_t family,
                        uint8_t command, uint8_t version, uint16_t flags,
                        uint32_t sequence, size_t attributes_length) {
    struct sockaddr_nl kernel = {.nl_family = AF_NETLINK};
    size_t header_length = NLMSG_LENGTH(GENL_HDRLEN);
    request->netlink.nlmsg_len = (uint32_t)(header_length + attributes_length);
    request->netlink.nlmsg_type = family;
    request->netlink.nlmsg_flags = flags;
    request->netlink.nlmsg_seq = sequence;
    request->generic.cmd = command;
    request->generic.version = version;
    request->generic.reserved = 0;
    ssize_t sent = sendto(fd, request, request->netlink.nlmsg_len, 0,
            (struct sockaddr *)&kernel, sizeof(kernel));
    if (sent != (ssize_t)request->netlink.nlmsg_len) {
        perror("sendto(Generic Netlink)");
        return -1;
    }
    return 0;
}

static int receive_family_id(int fd, uint32_t sequence, uint16_t *family_id) {
    unsigned char reply[REPLY_CAPACITY];
    for (;;) {
        struct sockaddr_nl sender = {0};
        socklen_t sender_length = sizeof(sender);
        ssize_t received = recvfrom(fd, reply, sizeof(reply), 0,
                (struct sockaddr *)&sender, &sender_length);
        if (received < 0) {
            perror("recvfrom(GENL_CTRL_GETFAMILY)");
            return -1;
        }
        if (sender.nl_pid != 0) continue;
        int remaining = (int)received;
        for (struct nlmsghdr *message = (struct nlmsghdr *)reply;
                NLMSG_OK(message, remaining);
                message = NLMSG_NEXT(message, remaining)) {
            if (message->nlmsg_seq != sequence) continue;
            if (message->nlmsg_type == NLMSG_ERROR) {
                if (message->nlmsg_len < NLMSG_LENGTH(sizeof(struct nlmsgerr)))
                    return -1;
                struct nlmsgerr *error = (struct nlmsgerr *)NLMSG_DATA(message);
                if (error->error == 0) continue;
                errno = -error->error;
                perror("GENL_CTRL_GETFAMILY");
                return -1;
            }
            if (message->nlmsg_type != GENL_ID_CTRL ||
                    message->nlmsg_len < NLMSG_LENGTH(GENL_HDRLEN))
                continue;
            size_t attributes_length = message->nlmsg_len - NLMSG_LENGTH(GENL_HDRLEN);
            struct nlattr *attribute = (struct nlattr *)((unsigned char *)NLMSG_DATA(message) + GENL_HDRLEN);
            while (attribute_is_valid(attribute, (int)attributes_length)) {
                if (attribute->nla_type == CTRL_ATTR_FAMILY_ID &&
                        attribute->nla_len >= NLA_HDRLEN + sizeof(*family_id)) {
                    memcpy(family_id, (unsigned char *)attribute + NLA_HDRLEN,
                            sizeof(*family_id));
                    return 0;
                }
                size_t step = NLA_ALIGN(attribute->nla_len);
                if (step > attributes_length) break;
                attributes_length -= step;
                attribute = (struct nlattr *)((unsigned char *)attribute + step);
            }
        }
    }
}

static int receive_ack(int fd, uint32_t sequence) {
    unsigned char reply[REPLY_CAPACITY];
    for (;;) {
        struct sockaddr_nl sender = {0};
        socklen_t sender_length = sizeof(sender);
        ssize_t received = recvfrom(fd, reply, sizeof(reply), 0,
                (struct sockaddr *)&sender, &sender_length);
        if (received < 0) {
            perror("recvfrom(NL80211_CMD_DEL_INTERFACE ACK)");
            return -1;
        }
        if (sender.nl_pid != 0) continue;
        int remaining = (int)received;
        for (struct nlmsghdr *message = (struct nlmsghdr *)reply;
                NLMSG_OK(message, remaining);
                message = NLMSG_NEXT(message, remaining)) {
            if (message->nlmsg_seq != sequence || message->nlmsg_type != NLMSG_ERROR ||
                    message->nlmsg_len < NLMSG_LENGTH(sizeof(struct nlmsgerr)))
                continue;
            struct nlmsgerr *ack = (struct nlmsgerr *)NLMSG_DATA(message);
            if (ack->error == 0 || ack->error == -ENODEV) return 0;
            int error_code = -ack->error;
            errno = error_code;
            perror("NL80211_CMD_DEL_INTERFACE");
            return error_code == EPERM || error_code == EACCES ? 77 : 1;
        }
    }
}

int main(int argc, char **argv) {
    int ifindex;
    int fd;
    uint16_t family_id = 0;
    uint32_t sequence = (uint32_t)getpid();
    struct sockaddr_nl local = {.nl_family = AF_NETLINK};
    struct timeval timeout = {.tv_sec = 3, .tv_usec = 0};
    struct request_buffer request = {0};
    size_t attributes_length = 0;

    if (argc != 2 || parse_ifindex(argc == 2 ? argv[1] : NULL, &ifindex) != 0) {
        fprintf(stderr, "usage: delete_ifindex POSITIVE_IFINDEX\n");
        return 64;
    }
    fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_GENERIC);
    if (fd < 0) {
        perror("socket(NETLINK_GENERIC)");
        return 70;
    }
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) != 0 ||
            bind(fd, (struct sockaddr *)&local, sizeof(local)) != 0) {
        perror("netlink socket setup");
        close(fd);
        return 70;
    }

    const char family_name[] = "nl80211";
    if (add_attribute(&request, &attributes_length, CTRL_ATTR_FAMILY_NAME,
                family_name, sizeof(family_name)) != 0 ||
            send_request(fd, &request, GENL_ID_CTRL, CTRL_CMD_GETFAMILY, 1,
                NLM_F_REQUEST, sequence, attributes_length) != 0 ||
            receive_family_id(fd, sequence, &family_id) != 0) {
        close(fd);
        return 70;
    }

    memset(&request, 0, sizeof(request));
    attributes_length = 0;
    uint32_t target_ifindex = (uint32_t)ifindex;
    sequence++;
    if (add_attribute(&request, &attributes_length, NL80211_ATTR_IFINDEX,
                &target_ifindex, sizeof(target_ifindex)) != 0 ||
            send_request(fd, &request, family_id, NL80211_CMD_DEL_INTERFACE,
                NL80211_FAMILY_VERSION, NLM_F_REQUEST | NLM_F_ACK,
                sequence, attributes_length) != 0) {
        close(fd);
        return 70;
    }
    int result = receive_ack(fd, sequence);
    if (result == 0)
        printf("NL80211_CMD_DEL_INTERFACE ifindex=%d ack=accepted_or_already_absent\n", ifindex);
    close(fd);
    return result;
}
