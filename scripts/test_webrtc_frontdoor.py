#!/usr/bin/env python3
"""Verify /call/webrtc_local is reachable on HTTP (LAN) and HTTPS (cloud)."""
import argparse
import base64
import json
import ssl
import urllib.request

# Minimal SDP offer that go2rtc will accept for src=camera.
FAKE_OFFER = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtcp:9 IN IP4 0.0.0.0\r\n"
    "a=ice-ufrag:abc123\r\n"
    "a=ice-pwd:def45678901234567890\r\n"
    "a=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99\r\n"
    "a=setup:actpass\r\n"
    "a=mid:0\r\n"
    "a=sendrecv\r\n"
    "a=rtcp-mux\r\n"
    "a=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1\r\n"
)


def check(url: str, host_header: str = None, insecure: bool = False) -> bool:
    req = urllib.request.Request(
        url,
        data=FAKE_OFFER.encode(),
        method="POST",
        headers={"Content-Type": "plain/text"},
    )
    if host_header:
        req.add_header("Host", host_header)

    ctx = None
    if insecure and url.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            body = resp.read().decode()
            payload = json.loads(base64.b64decode(body))
            sdp = payload.get("sdp", "")
            print(f"OK  {url}: HTTP {resp.status}, type={payload.get('type')}, sdp_len={len(sdp)}")
            return payload.get("type") == "answer" and len(sdp) > 100
    except Exception as exc:
        print(f"FAIL {url}: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test webrtc_local frontdoor")
    parser.add_argument("--host", default=os.environ.get("PRINTER_HOST", "192.168.1.100"))
    parser.add_argument("--domain", default=os.environ.get("PUBLIC_HOST", "printer.lan"))
    parser.add_argument("--insecure", action="store_true", help="Skip TLS verification for HTTPS test")
    args = parser.parse_args()

    ok = True
    ok &= check(f"http://{args.host}/call/webrtc_local")
    ok &= check(f"https://{args.domain}/call/webrtc_local", host_header=args.domain, insecure=args.insecure)

    if ok:
        print("\nAll frontdoor checks passed.")
        return 0
    print("\nOne or more frontdoor checks failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
