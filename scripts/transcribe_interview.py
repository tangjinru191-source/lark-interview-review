#!/usr/bin/env python3
"""Upload an interview recording to Lark Minutes and download its raw transcript."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Any
from urllib.parse import urlparse


SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".wma", ".amr",
    ".avi", ".wmv", ".mov", ".mp4", ".m4v", ".mpeg", ".flv",
}
MAX_BYTES = 6 * 1024 * 1024 * 1024
REQUIRED_SCOPES = {
    "drive:file:upload",
    "minutes:minutes.upload:write",
    "minutes:minutes.basic:read",
    "minutes:minutes.artifacts:read",
}


class WorkflowError(RuntimeError):
    pass


def lark_binary() -> str:
    explicit = os.environ.get("LARK_CLI_BIN")
    if explicit:
        return explicit
    binary = shutil.which("lark-cli")
    if binary is None:
        raise WorkflowError("lark-cli is not installed or is not on PATH")
    return binary


def parse_last_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    found: list[tuple[int, dict[str, Any]]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            found.append((consumed, value))
    if not found:
        raise WorkflowError("lark-cli output did not contain a JSON object")
    return max(found, key=lambda item: item[0])[1]


def run_lark(
    arguments: list[str],
    cwd: Path,
    allow_failure: bool = False,
    timeout_seconds: int = 1800,
) -> tuple[int, dict[str, Any], str]:
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    try:
        process = subprocess.Popen(
            [lark_binary(), *arguments],
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise WorkflowError("lark-cli executable was not found") from exc
    try:
        output, _ = process.communicate(timeout=max(1, timeout_seconds))
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise WorkflowError(
            f"lark-cli timed out after {timeout_seconds}s: {' '.join(arguments[:2])}"
        ) from exc
    try:
        payload = parse_last_json(output)
    except WorkflowError:
        if allow_failure:
            payload = {"ok": False, "raw_output": output.strip()}
        else:
            raise
    if process.returncode != 0 and not allow_failure:
        raise WorkflowError(output.strip() or f"lark-cli exited with {process.returncode}")
    return process.returncode, payload, output


def check_auth(cwd: Path) -> dict[str, Any]:
    try:
        _, payload, _ = run_lark(
            ["auth", "status", "--json", "--verify"],
            cwd,
            allow_failure=True,
            timeout_seconds=60,
        )
    except WorkflowError as exc:
        return {
            "verified": False,
            "user_status": None,
            "missing_scopes": sorted(REQUIRED_SCOPES),
            "error": str(exc),
            "check_failed": True,
        }
    user = payload.get("identities", {}).get("user", {})
    verified = bool(user.get("verified") or (
        payload.get("identity") == "user" and payload.get("verified")
    ))
    raw_scopes = user.get("scope", "")
    scopes = set(raw_scopes if isinstance(raw_scopes, list) else str(raw_scopes).split())
    missing = sorted(REQUIRED_SCOPES - scopes)
    result = {
        "verified": verified,
        "user_status": user.get("status"),
        "missing_scopes": missing,
    }
    if not verified:
        result["error"] = user.get("message") or payload.get("note") or "User authorization is not verified"
    return result


def validate_media(media: Path) -> None:
    if not media.is_file():
        raise WorkflowError(f"media file not found: {media}")
    if media.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise WorkflowError(f"unsupported media extension: {media.suffix}")
    if media.stat().st_size > MAX_BYTES:
        raise WorkflowError("media file exceeds the 6 GB Lark Minutes limit")


def minute_token_from_url(minute_url: str) -> str:
    token = urlparse(minute_url).path.rstrip("/").split("/")[-1]
    if not token:
        raise WorkflowError(f"could not extract minute_token from {minute_url}")
    return token


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", nargs="?", help="Local interview media path")
    parser.add_argument("--output-dir", default="./interview-review-output")
    parser.add_argument("--name", help="Optional uploaded file name")
    parser.add_argument("--poll-interval-seconds", type=int, default=30)
    parser.add_argument("--max-wait-seconds", type=int, default=1800)
    parser.add_argument("--auth-check-only", action="store_true")
    args = parser.parse_args()

    invocation_cwd = Path.cwd().resolve()
    auth = check_auth(invocation_cwd)
    auth_ok = auth["verified"] and not auth["missing_scopes"]
    if args.auth_check_only:
        print(json.dumps({"ok": auth_ok, "auth": auth}, ensure_ascii=False, indent=2))
        return 0 if auth_ok else 3

    if not auth["verified"]:
        print(
            json.dumps(
                {"ok": False, "error": "AUTH_REQUIRED", "auth": auth},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3
    if auth["missing_scopes"]:
        print(
            json.dumps(
                {"ok": False, "error": "MISSING_SCOPES", "auth": auth},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3
    if not args.media:
        parser.error("media is required unless --auth-check-only is used")

    media = Path(args.media).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    validate_media(media)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "result.json"

    upload_name = args.name or media.name
    print(f"Uploading {media.name} to the user's Lark Drive...", flush=True)
    _, upload, _ = run_lark(
        [
            "drive", "+upload", "--as", "user",
            "--file", media.name, "--name", upload_name,
        ],
        media.parent,
    )
    upload_data = upload.get("data", {})
    file_token = upload_data.get("file_token")
    if not file_token:
        raise WorkflowError("drive +upload succeeded without file_token")

    manifest: dict[str, Any] = {
        "ok": False,
        "source_media": str(media),
        "file_token": file_token,
        "drive_url": upload_data.get("url"),
        "minute_url": None,
        "minute_token": None,
        "transcript_path": None,
    }
    save_manifest(manifest_path, manifest)

    print("Creating Lark Minutes...", flush=True)
    _, minute, _ = run_lark(
        ["minutes", "+upload", "--as", "user", "--file-token", str(file_token)],
        output_dir,
    )
    minute_url = minute.get("data", {}).get("minute_url")
    if not minute_url:
        raise WorkflowError("minutes +upload succeeded without minute_url")
    minute_token = minute_token_from_url(str(minute_url))
    manifest.update({"minute_url": minute_url, "minute_token": minute_token})
    save_manifest(manifest_path, manifest)

    deadline = time.monotonic() + max(1, args.max_wait_seconds)
    attempt = 0
    detail_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        attempt += 1
        print(f"Polling transcript, attempt {attempt}...", flush=True)
        code, payload, output = run_lark(
            [
                "minutes", "+detail", "--as", "user",
                "--minute-tokens", minute_token,
                "--transcript", "--overwrite", "--output-dir", "./artifacts",
            ],
            output_dir,
            allow_failure=True,
        )
        if code == 0 and payload.get("ok") is True:
            detail_payload = payload
            break
        if "minute not ready" not in output.lower():
            raise WorkflowError(output.strip() or "minutes +detail failed")
        time.sleep(max(1, args.poll_interval_seconds))

    if detail_payload is None:
        manifest["error"] = "TRANSCRIPT_TIMEOUT"
        save_manifest(manifest_path, manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 4

    minutes = detail_payload.get("data", {}).get("minutes", [])
    if not minutes:
        raise WorkflowError("minutes +detail returned no minute records")
    transcript_value = minutes[0].get("artifacts", {}).get("transcript_file")
    if not transcript_value:
        raise WorkflowError("minutes +detail returned no transcript_file")
    transcript_path = Path(str(transcript_value))
    if not transcript_path.is_absolute():
        transcript_path = (output_dir / transcript_path).resolve()
    if not transcript_path.is_file():
        raise WorkflowError(f"transcript file not found after download: {transcript_path}")

    manifest.update(
        {
            "ok": True,
            "title": minutes[0].get("title"),
            "transcript_path": str(transcript_path),
        }
    )
    save_manifest(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
