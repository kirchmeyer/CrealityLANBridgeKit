#!/usr/bin/env python3
"""Raw-socket mDNS announcer that replicates the stock Creality mdns binary.

Uses the exact socket options observed in strace of the stock binary:
  AF_INET SOCK_DGRAM IPPROTO_UDP
  SO_REUSEADDR=1, SO_REUSEPORT=1
  IP_MULTICAST_TTL=1, IP_MULTICAST_LOOP=1
  IP_ADD_MEMBERSHIP 224.0.0.251 on 0.0.0.0
  bind 0.0.0.0:5353

Announces _Creality-{SN}._udp.local with real keybox SN/MAC values and
responds to mDNS queries for that service. Unlike the stock binary, this
announcer does not truncate the SN/MAC values by default.
"""
import os
import socket
import struct
import subprocess
import sys
import time

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
RECORD_TTL = 4500  # mDNS default for stable records (~75 min)


def get_keybox(key):
    try:
        r = subprocess.run(
            ["/usr/bin/keybox", "-r", key],
            capture_output=True, text=True, timeout=2
        )
        for line in r.stdout.strip().split("\n"):
            if f"{key} =" in line:
                return line.split(f"{key} =")[1].strip()
    except Exception:
        pass
    return None


def normalize_mac(value):
    """Return an uppercase, colon-free MAC matching the stock /info format."""
    if not isinstance(value, str):
        return value
    cleaned = value.strip().replace(":", "").replace("-", "").replace(".", "")
    if len(cleaned) == 12 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        return cleaned.upper()
    return value.strip().upper()


def get_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "192.168.1.100"
    finally:
        s.close()


def encode_name_parts(parts):
    """Encode a list of labels to DNS wire format."""
    out = b""
    for p in parts:
        if not p:
            continue
        out += bytes([len(p)]) + p.encode("utf-8")
    return out + b"\x00"


def encode_name(name):
    """Encode a dot-separated name to DNS wire format."""
    return encode_name_parts(name.rstrip(".").split("."))


def build_txt(labels):
    """Build TXT RDATA from a list of 'key=value' strings."""
    return b"".join(bytes([len(l)]) + l.encode("utf-8") for l in labels)


def parse_name(data, offset):
    """Parse a DNS name from data at offset. Returns (name_parts, new_offset).

    Handles standard labels and DNS pointer compression.
    """
    parts = []
    jumped = False
    jump_offset = None
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if (length & 0xC0) == 0xC0:
            if not jumped:
                jump_offset = offset + 2
            pointer = struct.unpack(">H", data[offset:offset + 2])[0] & 0x3FFF
            offset = pointer
            jumped = True
            continue
        if (length & 0xC0) != 0:
            # Invalid label type
            break
        offset += 1
        parts.append(data[offset:offset + length].decode("utf-8", errors="replace"))
        offset += length
    if jumped:
        offset = jump_offset
    return parts, offset


def name_matches(parts, target_parts):
    """Case-insensitive compare of parsed name parts to target parts."""
    if len(parts) != len(target_parts):
        return False
    return all(a.lower() == b.lower() for a, b in zip(parts, target_parts))


def build_response_records(sn, mac, model, hostname, port, ipv4, truncate_txt=False):
    """Pre-build all resource records we may announce/respond with."""
    service_type = f"_Creality-{sn}._udp.local."
    service_instance = f"{hostname}.{service_type}"
    host_fqdn = f"{hostname}.local."

    if truncate_txt:
        # Match the stock binary's visible truncation behavior
        txt_labels = [f"SN={sn[:6]}", f"MAC={mac[:7]}", f"MODEL={model}"]
    else:
        txt_labels = [f"SN={sn}", f"MAC={mac}", f"MODEL={model}"]

    svc_name = encode_name(service_type)
    inst_name = encode_name(service_instance)
    host_name = encode_name(host_fqdn)
    txt_rdata = build_txt(txt_labels)
    srv_rdata = struct.pack(">HHH", 0, 0, port) + host_name
    a_rdata = socket.inet_aton(ipv4)

    ptr_rr = (
        svc_name
        + struct.pack(">HHIH", 12, 0x8001, RECORD_TTL, len(inst_name))
        + inst_name
    )
    srv_rr = (
        inst_name
        + struct.pack(">HHIH", 33, 0x8001, RECORD_TTL, len(srv_rdata))
        + srv_rdata
    )
    a_rr = (
        host_name
        + struct.pack(">HHIH", 1, 0x8001, RECORD_TTL, len(a_rdata))
        + a_rdata
    )
    txt_rr = (
        inst_name
        + struct.pack(">HHIH", 16, 0x8001, RECORD_TTL, len(txt_rdata))
        + txt_rdata
    )

    return {
        "service_type": service_type,
        "service_instance": service_instance,
        "host_fqdn": host_fqdn,
        "ptr": ptr_rr,
        "srv": srv_rr,
        "txt": txt_rr,
        "a": a_rr,
    }


def build_packet(answer_rrs, additional_rrs=None):
    additional_rrs = additional_rrs or []
    header = struct.pack(
        ">HHHHHH",
        0,       # transaction ID
        0x8400,  # flags: response, authoritative
        0,       # questions
        len(answer_rrs),
        0,       # authority records
        len(additional_rrs),
    )
    return header + b"".join(answer_rrs) + b"".join(additional_rrs)


def build_enum_response(service_type):
    enum_name = encode_name("_services._dns-sd._udp.local.")
    svc_name = encode_name(service_type)
    record = (
        enum_name
        + struct.pack(">HHIH", 12, 0x8001, RECORD_TTL, len(svc_name))
        + svc_name
    )
    return build_packet([record])


def get_question_names(data):
    """Parse question section and return list of (parts, qtype, qclass)."""
    if len(data) < 12:
        return []
    qdcount = struct.unpack(">H", data[4:6])[0]
    offset = 12
    questions = []
    for _ in range(qdcount):
        parts, offset = parse_name(data, offset)
        if offset + 4 > len(data):
            break
        qtype, qclass = struct.unpack(">HH", data[offset:offset + 4])
        offset += 4
        questions.append((parts, qtype, qclass))
    return questions


def respond_to_query(data, records, truncate_txt=False):
    """Build a response packet for an incoming mDNS query."""
    questions = get_question_names(data)
    if not questions:
        return None

    service_parts = records["service_type"].rstrip(".").split(".")
    instance_parts = records["service_instance"].rstrip(".").split(".")
    host_parts = records["host_fqdn"].rstrip(".").split(".")

    answers = []
    additional = []

    for parts, qtype, qclass in questions:
        # PTR query for service type
        if name_matches(parts, service_parts) and qtype in (12, 255):
            if records["ptr"] not in answers:
                answers.append(records["ptr"])
            for rr in (records["srv"], records["txt"], records["a"]):
                if rr not in additional:
                    additional.append(rr)

        # SRV/TXT/ANY query for service instance
        if name_matches(parts, instance_parts):
            if qtype in (33, 255) and records["srv"] not in answers:
                answers.append(records["srv"])
            if qtype in (16, 255) and records["txt"] not in answers:
                answers.append(records["txt"])
            if records["a"] not in additional:
                additional.append(records["a"])

        # A/AAAA/ANY query for host
        if name_matches(parts, host_parts):
            if qtype in (1, 255) and records["a"] not in answers:
                answers.append(records["a"])

        # Service enumeration
        if name_matches(parts, ["_services", "_dns-sd", "_udp", "local"]):
            if qtype in (12, 255):
                return build_enum_response(records["service_type"])

    if not answers:
        return None
    # Dedupe additional against answers
    additional = [rr for rr in additional if rr not in answers]
    return build_packet(answers, additional)


def main():
    sn = get_keybox("sn") or "UNKNOWN"
    mac = normalize_mac(get_keybox("wifi_mac") or "")
    model = get_keybox("model") or "N/A"

    hostname = os.environ.get("MDNS_HOST", "").strip()
    if not hostname:
        try:
            hostname = subprocess.check_output(
                ["hostname", "-s"], text=True, timeout=2
            ).strip()
        except Exception:
            hostname = sn[:8] if len(sn) >= 4 else "CREALITY"

    # Stock /rom/usr/bin/mdns advertises port 5353 in the SRV record and
    # truncates the SN/MAC in the TXT record. Match that exactly.
    port = int(
        os.environ.get("MDNS_SERVICE_PORT")
        or os.environ.get("MDNS_PORT")
        or "5353"
    )
    truncate_txt = os.environ.get("MDNS_TRUNCATE_TXT", "1").strip() in ("1", "true", "yes")
    ipv4 = get_ipv4()

    records = build_response_records(sn, mac, model, hostname, port, ipv4, truncate_txt)
    service_type = records["service_type"]

    print(f"Announcing {service_type}", file=sys.stderr)
    print(
        f"SN={sn} MAC={mac} MODEL={model} HOST={hostname} IP={ipv4} PORT={port} "
        f"TRUNCATE_TXT={truncate_txt}",
        file=sys.stderr,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton("0.0.0.0")),
    )
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_IF,
        struct.pack("4s", socket.inet_aton("0.0.0.0")),
    )
    sock.bind(("0.0.0.0", MDNS_PORT))

    announcement = build_packet([records["ptr"], records["srv"], records["txt"], records["a"]])
    enum_response = build_enum_response(service_type)

    for _ in range(5):
        sock.sendto(announcement, (MDNS_GROUP, MDNS_PORT))
        time.sleep(0.2)

    last_announce = time.monotonic()
    announce_interval = 10  # re-announce frequently to stay visible

    try:
        while True:
            sock.settimeout(1.0)
            try:
                data, addr = sock.recvfrom(2048)
                if len(data) < 12:
                    continue
                flags = struct.unpack(">H", data[2:4])[0]
                if flags & 0x8000:
                    continue  # ignore responses

                resp = respond_to_query(data, records)
                if resp:
                    print(f"Query from {addr}, responding ({len(resp)} bytes)", file=sys.stderr)
                    sock.sendto(resp, (MDNS_GROUP, MDNS_PORT))
                    last_announce = time.monotonic()

                # Also send enum response if asked
                if b"_services._dns-sd._udp" in data.lower():
                    sock.sendto(enum_response, (MDNS_GROUP, MDNS_PORT))

            except socket.timeout:
                pass

            if time.monotonic() - last_announce >= announce_interval:
                sock.sendto(announcement, (MDNS_GROUP, MDNS_PORT))
                print("Sent periodic announcement", file=sys.stderr)
                last_announce = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
