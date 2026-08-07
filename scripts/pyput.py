#!/usr/bin/env python3
"""Tiny scp replacement for hosts without sftp-server.

Uses plain SSH to push or pull files by piping through stdin/stdout.
Requires ssh(1) in PATH and key-based auth (no password prompts).
"""
import argparse
import os
import subprocess
import sys


def ssh_cmd(user_host, remote_command, stdin=None, capture_stdout=False):
    user, _, host = user_host.partition("@")
    target = f"{user}@{host}" if user else host
    cmd = ["ssh", target, remote_command]
    kwargs = {}
    if stdin is not None:
        kwargs["input"] = stdin
    if capture_stdout:
        kwargs["stdout"] = subprocess.PIPE
    return subprocess.run(cmd, check=False, **kwargs)


def push_file(user_host, local_path, remote_path):
    if not os.path.isfile(local_path):
        print(f"error: local file not found: {local_path}", file=sys.stderr)
        return 1
    with open(local_path, "rb") as fh:
        data = fh.read()
    # Use cat with explicit mode to avoid issues with binary data.
    result = ssh_cmd(user_host, f"cat > {remote_path}", stdin=data)
    if result.returncode == 0:
        print(f"{local_path} -> {user_host}:{remote_path}")
    return result.returncode


def pull_file(user_host, remote_path, local_path):
    result = ssh_cmd(
        user_host,
        f"cat {remote_path}",
        capture_stdout=True,
    )
    if result.returncode != 0:
        return result.returncode
    with open(local_path, "wb") as fh:
        fh.write(result.stdout)
    print(f"{user_host}:{remote_path} -> {local_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Minimal scp replacement using SSH stdin/stdout"
    )
    parser.add_argument(
        "-P", "--port", default="22", help="SSH port (currently ignored; configure ~/.ssh/config)"
    )
    parser.add_argument("src", help="Source path: [user@]host:path or local path")
    parser.add_argument("dst", nargs="?", help="Destination path (required for push)")
    args = parser.parse_args()

    if ":" in args.src:
        # Pull: user@host:remote -> local
        if not args.dst:
            print("error: destination required for pull", file=sys.stderr)
            return 1
        return pull_file(args.src.split(":", 1)[0], args.src.split(":", 1)[1], args.dst)

    # Push: local -> user@host:remote
    if not args.dst or ":" not in args.dst:
        print("error: destination must be [user@]host:path", file=sys.stderr)
        return 1
    return push_file(args.dst.split(":", 1)[0], args.src, args.dst.split(":", 1)[1])


if __name__ == "__main__":
    sys.exit(main())
