"""Generate and install the launchd user agent that runs the MemoryMark bridge.

macOS only. Mirrors the launchd handling in the sibling imap-mcp project
(``design.941.imap-mcp.*``): the plist is generated here rather than checked in
as a static file, because every meaningful key -- interpreter path, working
directory, palace location -- is absolute and machine-specific. A static plist
would either hardcode one developer's paths or need templating anyway.

Stdlib only, so it can run via ``uv run --no-project`` without syncing the
bridge's dependency tree just to write a file.

Loading/unloading is left to the caller (see the Makefile's ``install`` and
``uninstall`` targets), matching imap-mcp where the Makefile owns the
``launchctl bootout``/``bootstrap``/``kickstart`` dance.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LABEL = "design.941.memorymark.bridge"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 21517


@dataclass(frozen=True)
class LaunchdUnit:
    label: str
    program_arguments: list[str]
    working_directory: Path
    stdout_path: Path
    stderr_path: Path
    environment: dict[str, str] | None = None
    keep_alive: bool = True
    run_at_load: bool = True

    def to_plist(self) -> bytes:
        payload = {
            "Label": self.label,
            "ProgramArguments": self.program_arguments,
            "WorkingDirectory": str(self.working_directory),
            "StandardOutPath": str(self.stdout_path),
            "StandardErrorPath": str(self.stderr_path),
            "RunAtLoad": self.run_at_load,
            "KeepAlive": self.keep_alive,
        }
        if self.environment:
            payload["EnvironmentVariables"] = self.environment
        return plistlib.dumps(payload, sort_keys=True)


def bridge_launchd_unit(
    uv_executable: Path,
    project_dir: Path,
    palace_path: Path,
    log_dir: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    label: str = DEFAULT_LABEL,
) -> LaunchdUnit:
    """Build the unit that serves REST at /api/* and MCP at /mcp.

    ``--frozen`` keeps uv from re-resolving uv.lock on every start: launchd
    restarts this process on crash and at login, and a resolve would make
    startup depend on network reachability.
    """
    bridge_dir = project_dir / "bridge"
    # launchd hands user agents a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin).
    # uv is invoked by absolute path, but it shells out for git and for
    # provisioning an interpreter, so put its own directory on PATH too.
    path_entries: list[str] = []
    for entry in (str(uv_executable.parent), "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if entry not in path_entries:
            path_entries.append(entry)
    return LaunchdUnit(
        label=label,
        program_arguments=[
            str(uv_executable),
            "run",
            "--frozen",
            "uvicorn",
            "server:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        working_directory=bridge_dir,
        stdout_path=log_dir / "bridge.out.log",
        stderr_path=log_dir / "bridge.err.log",
        environment={
            # Without this the bridge would resolve mempalace's default
            # ~/.mempalace/palace instead of the repo-local palace the Docker
            # deployment bind-mounts -- i.e. it would silently come up empty.
            "MEMPALACE_PALACE_PATH": str(palace_path),
            "PATH": ":".join(path_entries),
        },
    )


def install_launchd_unit(unit: LaunchdUnit, launch_agents_dir: Path | None = None) -> Path:
    target_dir = launch_agents_dir or Path("~/Library/LaunchAgents").expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    unit.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    unit.stderr_path.parent.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{unit.label}.plist"
    path.write_bytes(unit.to_plist())
    os.chmod(path, 0o644)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the MemoryMark bridge launchd unit")
    parser.add_argument("--uv", default="uv", help="Path to the uv executable")
    parser.add_argument("--project-dir", default=str(Path.cwd()))
    parser.add_argument("--palace-path", required=True, help="Directory holding the MemPalace store")
    parser.add_argument("--log-dir", default=str(Path("~/Library/Logs/memorymark").expanduser()))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--launch-agents-dir", default=None)
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Write the plist to stdout instead of installing it",
    )
    args = parser.parse_args()

    unit = bridge_launchd_unit(
        uv_executable=Path(args.uv).expanduser(),
        project_dir=Path(args.project_dir).expanduser().resolve(),
        palace_path=Path(args.palace_path).expanduser().resolve(),
        log_dir=Path(args.log_dir).expanduser(),
        host=args.host,
        port=args.port,
        label=args.label,
    )

    if args.print_only:
        sys.stdout.write(unit.to_plist().decode())
        return

    path = install_launchd_unit(
        unit,
        Path(args.launch_agents_dir).expanduser() if args.launch_agents_dir else None,
    )
    print(
        json.dumps(
            {
                "installed": str(path),
                "label": unit.label,
                "palace_path": unit.environment["MEMPALACE_PALACE_PATH"],
                "stdout": str(unit.stdout_path),
                "stderr": str(unit.stderr_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
