"""Push one frozen commit with bounded network retries and remote verification.

Run through uv; uses only the Python standard library. No commit/staging,
force push, credential changes, persistent DNS overrides, or automatic merge.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit


def redact(value: str) -> str:
    value = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", value)
    return re.sub(r"(?i)(authorization:|access_token=|token=)[^\s&]+", r"\1[redacted]", value)


def command(args: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    # Do not inherit HTTP/credential trace logging into captured output.
    for name in list(env):
        if name.startswith("GIT_TRACE") or name == "GIT_CURL_VERBOSE":
            env.pop(name)
    with subprocess.Popen(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
            return proc.returncode, out, err
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            # Kill Git and its transport together before any retry.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                out, err = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                out, err = proc.communicate()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return 124, out, err + f"\nCommand timed out after {timeout:g}s."


def git(repo: Path, args: list[str], timeout: float = 10) -> tuple[int, str, str]:
    return command(["git", *args], repo, timeout)


def required(repo: Path, args: list[str]) -> str:
    rc, out, err = git(repo, args)
    if rc:
        raise ValueError(redact(err.strip()) or f"Git command failed ({rc})")
    return out.strip()


def http_setting(repo: Path, url: str, field: str, fallback: str) -> str:
    rc, out, err = git(repo, ["config", "--get-urlmatch", f"http.{field}", url])
    if rc not in {0, 1}:
        raise ValueError(redact(err.strip()) or "Unable to read effective HTTP configuration")
    return out.strip() if rc == 0 else fallback


def ipv4(host: str, port: int) -> list[str]:
    # DNS itself is bounded; resolve in a child rather than an unkillable thread.
    code = "import json,socket,sys; print(json.dumps(sorted({r[4][0] for r in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),socket.AF_INET,socket.SOCK_STREAM)})))"
    rc, out, _ = command(["uv", "run", "--no-sync", "python", "-c", code, host, str(port)], Path.cwd(), 8)
    if rc:
        return []
    return [str(ipaddress.IPv4Address(item)) for item in json.loads(out)]


def failure_kind(rc: int, output: str) -> str:
    low = output.lower()
    permanent = ("[rejected]", "[remote rejected]", "non-fast-forward", "fetch first",
                 "authentication failed", "permission denied", "repository not found",
                 "could not read username", "terminal prompts disabled", "access denied",
                 "protected branch", "pre-receive hook declined", "hook declined",
                 "certificate problem", "host key verification failed", "http 403", "error: 403")
    if any(item in low for item in permanent):
        return "action_required"
    transient = ("timed out", "timeout", "could not resolve", "couldn't resolve",
                 "failed to connect", "couldn't connect", "connection reset", "connection refused",
                 "connection closed", "empty reply", "recv failure", "send failure",
                 "http/2", "curl 16", "curl 18", "curl 28", "curl 35", "curl 52", "curl 55",
                 "curl 56", "curl 92", "unexpected disconnect", "remote end hung up",
                 "error: 429", "error: 500", "error: 502", "error: 503", "error: 504")
    return "network_retryable" if rc == 124 or any(item in low for item in transient) else "action_required"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--remote", help="Existing remote; defaults to current branch upstream")
    parser.add_argument("--branch", help="Must match the current local branch")
    parser.add_argument("--set-upstream", action="store_true", help="Set tracking after verified first push")
    parser.add_argument("--attempts", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--timeout", type=int, default=45, help="Per push attempt, seconds (10..300)")
    args = parser.parse_args()
    if not 10 <= args.timeout <= 300:
        parser.error("--timeout must be 10..300 seconds")
    repo = Path(required(args.repo.resolve(), ["rev-parse", "--show-toplevel"]))
    branch = required(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if args.branch and args.branch != branch:
        raise ValueError("Requested branch differs from current branch; switch deliberately before pushing")
    tip = required(repo, ["rev-parse", "HEAD"])
    upstream = required(repo, ["for-each-ref", "--format=%(upstream:remotename)|%(upstream:remoteref)", f"refs/heads/{branch}"])
    tracking_remote, tracking_ref = upstream.split("|", 1)
    remote = args.remote or tracking_remote
    known_remotes = required(repo, ["remote"]).splitlines()
    if not remote or remote not in known_remotes:
        raise ValueError("No unambiguous upstream remote. Use --remote <existing-name> explicitly")
    urls = required(repo, ["remote", "get-url", "--push", "--all", remote]).splitlines()
    if len(urls) != 1:
        raise ValueError("Multiple push URLs require separate explicit review; this command pushes one destination")
    url = urls[0]
    parsed = urlsplit(url)
    if parsed.scheme and parsed.scheme not in {"https", "http", "ssh", "file"}:
        raise ValueError("Unsupported Git transport; inspect it before using this helper")
    target_ref = tracking_ref if remote == tracking_remote and tracking_ref else f"refs/heads/{branch}"
    if not target_ref.startswith("refs/heads/"):
        raise ValueError("Target must be one remote branch")
    required(repo, ["check-ref-format", target_ref])
    target_branch = target_ref.removeprefix("refs/heads/")
    is_http = parsed.scheme in {"https", "http"}
    version = http_setting(repo, url, "version", "HTTP/1.1") if is_http else None
    base = (["-c", f"http.version={version}",
             "-c", "http.lowSpeedLimit=" + http_setting(repo, url, "lowSpeedLimit", "1"),
             "-c", "http.lowSpeedTime=" + http_setting(repo, url, "lowSpeedTime", "20")] if is_http else [])
    events: list[dict] = []
    last_failure = "not_started"
    for attempt in range(1, args.attempts + 1):
        if required(repo, ["rev-parse", "HEAD"]) != tip or required(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"]) != branch:
            raise ValueError("Local HEAD/branch changed during synchronization; review before continuing")
        if required(repo, ["remote", "get-url", "--push", "--all", remote]).splitlines() != [url]:
            raise ValueError("Push destination changed during synchronization")
        options = list(base)
        mode = str(version) if is_http else "existing_transport"
        if attempt > 1 and is_http and parsed.hostname:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = ipv4(parsed.hostname, port)
            if addresses:
                # Re-resolve on every retry; this override exists only for this command.
                options += ["-c", f"http.curloptResolve={parsed.hostname}:{port}:{','.join(addresses)}"]
                mode += "_fresh_ipv4"
        print(f"Attempt {attempt}/{args.attempts}: {remote} {target_branch} ({mode})", file=sys.stderr, flush=True)
        # Freeze the source SHA so concurrent commits can never be pushed by accident.
        rc, out, err = git(repo, [*options, "push", "--porcelain", "--", remote, f"{tip}:{target_ref}"], args.timeout)
        check_rc, check_out, check_err = git(repo, [*options, "ls-remote", "--exit-code", "--refs", "--", url, target_ref], min(args.timeout, 20))
        found = [line.split()[0] for line in check_out.splitlines()
                 if len(line.split()) == 2 and line.split()[1] == target_ref]
        event = {"attempt": attempt, "mode": mode, "push_exit": rc, "verify_exit": check_rc}
        events.append(event)
        if check_rc == 0 and found == [tip]:
            warnings = []
            if rc:
                warnings.append("Push returned an error; remote nevertheless verified at the requested commit")
            if args.set_upstream:
                track_rc, _, track_err = git(repo, ["branch", f"--set-upstream-to={remote}/{target_branch}", branch])
                if track_rc:
                    warnings.append("Remote synchronized, but upstream setup failed: " + redact(track_err.strip()))
            head_changed = required(repo, ["rev-parse", "HEAD"]) != tip
            print(json.dumps({"status": "synced", "repo": str(repo), "remote": remote,
                              "branch": branch, "target_branch": target_branch, "commit": tip,
                              "remote_verified": True, "local_head_changed": head_changed,
                              "attempts": events, "warnings": warnings}, ensure_ascii=False, indent=2))
            return 0
        failure_output = out + err if rc else check_out + check_err
        last_failure = failure_kind(rc if rc else check_rc, failure_output)
        event["failure_kind"] = last_failure
        print(redact(failure_output.strip())[-3000:], file=sys.stderr, flush=True)
        if last_failure != "network_retryable" or attempt == args.attempts:
            break
        time.sleep(3 if attempt == 1 else 8)
    print(json.dumps({"status": "not_confirmed", "remote": remote, "branch": branch,
                      "target_branch": target_branch, "commit": tip, "remote_verified": False,
                      "failure_kind": last_failure, "attempts": events}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "blocked", "error": redact(str(error))}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
