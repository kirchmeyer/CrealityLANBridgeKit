#!/usr/bin/env python3
"""Tiny curl replacement for OpenWrt-style hosts that lack curl/wget."""
import argparse
import json
import sys
import urllib.error
import urllib.request


def fetch(url, method="GET", data=None, headers=None, timeout=10):
    req_headers = {}
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        elif isinstance(data, str):
            body = data.encode("utf-8")
        elif isinstance(data, bytes):
            body = data
        else:
            body = str(data).encode("utf-8")
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            print(f"HTTP {resp.status}")
            for k, v in resp.headers.items():
                print(f"{k}: {v}")
            print()
            try:
                print(raw.decode("utf-8"))
            except UnicodeDecodeError:
                print(raw.decode("utf-8", errors="replace"))
            return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}", file=sys.stderr)
        for k, v in exc.headers.items():
            print(f"{k}: {v}", file=sys.stderr)
        body = exc.read()
        try:
            print(body.decode("utf-8"), file=sys.stderr)
        except UnicodeDecodeError:
            print(body.decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Minimal HTTP fetch utility")
    parser.add_argument("url")
    parser.add_argument("-X", "--method", default="GET")
    parser.add_argument("-d", "--data", help="Request body (string) or use -j for JSON")
    parser.add_argument("-j", "--json", help="JSON request body")
    parser.add_argument("-H", "--header", action="append", default=[], help="Header in Name:Value form")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    headers = {}
    for h in args.header:
        name, _, value = h.partition(":")
        headers[name.strip()] = value.strip()

    data = args.json if args.json is not None else args.data
    return fetch(args.url, method=args.method, data=data, headers=headers, timeout=args.timeout)


if __name__ == "__main__":
    sys.exit(main())
