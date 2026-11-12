"""Local Hermes worker for Atlantis.

The worker is intentionally pull based: the hub never executes commands on a
worker's computer.  A user must opt in with ``--allow-execution``.
"""
# ruff: noqa: E701, E702

from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class HubClient:
    def __init__(self, server: str, token: str):
        self.server, self.token = server.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.server + path,
            data=data,
            method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read())


def git_worktree(workspace: Path, task_id: str) -> tuple[Path, str | None]:
    if not (workspace / ".git").exists():
        return workspace, None
    path = workspace / ".atlantis" / "worktrees" / task_id
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(path), "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    return path, commit


def run_job(client: HubClient, task: dict, workspace: Path, hermes: str, allow: bool, timeout: int) -> None:
    task_id, lease = task["id"], task["lease_id"]
    if not allow:
        client.call(
            "POST",
            f"/v2/worker/tasks/{task_id}/complete",
            {
                "run_id": lease,
                "status": "interrupted",
                "summary": "Ejecución bloqueada: falta --allow-execution.",
                "exit_code": None,
            },
        )
        return
    workdir, base_commit = git_worktree(workspace, task_id)
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(25):
            try:
                client.call("POST", f"/v2/worker/tasks/{task_id}/heartbeat", {"lease_id": lease})
            except Exception:
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    output: list[str] = []
    code = 1
    status = "failed"
    prompt = f"Tarea Atlantis: {task['title']}\n\n{task['description']}\n\nArchivos permitidos: {', '.join(task.get('paths', [])) or 'los necesarios'}\nTrabaja solo en este worktree. Ejecuta verificaciones. Resume cambios y pruebas al final."
    try:
        # Hermes' current CLI uses ``chat --oneshot -q``.  ``--in`` keeps the
        # worktree explicit for versions that support filesystem tools.
        proc = subprocess.Popen(
            [hermes, "chat", "--oneshot", "-q", prompt, "--in", str(workdir), "--yolo"],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        started = time.monotonic()
        for line in proc.stdout or ():
            if time.monotonic() - started > timeout:
                proc.kill()
                break
            output.append(line)
            client.call("POST", f"/v2/worker/tasks/{task_id}/log", {"lease_id": lease, "text": line[-8192:]})
        code = proc.wait(timeout=10)
        status = "completed" if code == 0 else "failed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        output.append(f"worker error: {exc}\n")
        status = "failed"
    finally:
        stop.set()
        thread.join(timeout=2)
    diff = ""
    if base_commit:
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", base_commit, "--", *task.get("paths", [])],
            cwd=workdir,
            capture_output=True,
            text=True,
        ).stdout[:262144]
    client.call(
        "POST",
        f"/v2/worker/tasks/{task_id}/complete",
        {
            "run_id": lease,
            "status": status,
            "summary": "".join(output)[-24000:],
            "diff": diff,
            "exit_code": code,
            "base_commit": base_commit,
            "worktree_path": str(workdir),
            "verifications": [],
        },
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlantis Hermes worker")
    p.add_argument("--server", default="http://127.0.0.1:8790")
    p.add_argument("--token-env", default="ATLANTIS_WORKER_TOKEN")
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--hermes", default="hermes")
    p.add_argument("--allow-execution", action="store_true")
    p.add_argument("--once", action="store_true")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--poll", type=int, default=5)
    args = p.parse_args(argv)
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"Falta la variable {args.token_env}", file=sys.stderr)
        return 2
    client = HubClient(args.server, token)
    while True:
        try:
            response = client.call("POST", "/v2/worker/claim")
            task = response.get("task")
            if task:
                run_job(
                    client, task, args.workspace.expanduser().resolve(), args.hermes, args.allow_execution, args.timeout
                )
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
            print(f"Atlantis worker: {exc}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(max(1, args.poll))


if __name__ == "__main__":
    raise SystemExit(main())
