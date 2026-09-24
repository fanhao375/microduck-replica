"""Shared wandb helpers for play_latest and export_latest scripts."""

import os
import subprocess
import sys
from datetime import datetime

import wandb

WANDB_PROJECT = "pollen-robotics/mjlab_microduck"


def find_latest_run(user_filter: str) -> wandb.apis.public.Run | None:
    """Return the most recent run whose metadata email contains *user_filter*."""
    api = wandb.Api()
    runs = api.runs(WANDB_PROJECT, per_page=100, order="-created_at")
    for run in runs:
        email = (run.metadata or {}).get("email", "")
        if user_filter.lower() in email.lower():
            return run
    return None


def find_latest_runs(user_filter, task_match, n):
    """Return up to *n* most recent runs whose metadata email contains
    *user_filter* and whose task_id (metadata args[0]) satisfies *task_match*.

    task_match: Callable[[str], bool] applied to the wandb task_id string.
    Ordered most-recent first.
    """
    api = wandb.Api()
    runs = api.runs(WANDB_PROJECT, per_page=100, order="-created_at")
    matched = []
    for run in runs:
        email = (run.metadata or {}).get("email", "")
        if user_filter.lower() not in email.lower():
            continue
        args = (run.metadata or {}).get("args", [])
        task_id = args[0] if args else ""
        if not task_match(task_id):
            continue
        matched.append(run)
        if len(matched) >= n:
            break
    return matched


def _format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h{m:02d}m"


def print_run_info(run: wandb.apis.public.Run) -> dict:
    """Print run details and return dict with env_name, run_path, checkpoints."""
    meta = run.metadata
    summary = run.summary
    train_cfg = run.config.get("train_cfg", {})
    env_cfg = run.config.get("env_cfg", {})

    env_name = meta.get("args", ["?"])[0]
    run_path = f"{WANDB_PROJECT}/{run.id}"
    duration = summary.get("_runtime", 0)
    total_steps = summary.get("_step", 0)
    max_iter = train_cfg.get("max_iterations", "?")
    num_envs = env_cfg.get("scene", {}).get("terrain", {}).get("num_envs", "?")
    mean_reward = summary.get("Train/mean_reward")
    lr = summary.get("Loss/learning_rate")

    reward_items = [
        (k.removeprefix("Episode_Reward/"), v)
        for k, v in summary.items()
        if k.startswith("Episode_Reward/") and isinstance(v, (int, float))
    ]
    reward_items.sort(key=lambda x: abs(x[1]), reverse=True)

    checkpoints = sorted(
        [f.name for f in run.files() if f.name.startswith("model_") and f.name.endswith(".pt")]
    )
    last_ckpt = checkpoints[-1] if checkpoints else "none"

    created = datetime.fromisoformat(run.created_at.replace("Z", "+00:00"))
    date_str = created.astimezone().strftime("%Y-%m-%d %H:%M")

    print("=" * 60)
    print(f"  Run:          {run.name}")
    print(f"  ID:           {run.id}")
    print(f"  Date:         {date_str}")
    print(f"  Status:       {run.state}")
    print(f"  Environment:  {env_name}")
    print(f"  Duration:     {_format_duration(duration)}")
    print(f"  Progress:     {total_steps} / {max_iter} iterations")
    print(f"  Num envs:     {num_envs}")
    print(f"  Mean reward:  {mean_reward:.2f}" if mean_reward else "  Mean reward:  ?")
    print(f"  Learning rate:{lr:.2e}" if lr else "  Learning rate:?")
    print(f"  Last ckpt:    {last_ckpt}")
    print(f"  Host:         {meta.get('host', '?')}")
    print(f"  GPU:          {meta.get('gpu', '?')}")
    print(f"  User:         {meta.get('email', '?')}")
    print()
    print("  Top rewards (by magnitude):")
    for name, val in reward_items[:8]:
        sign = "+" if val >= 0 else ""
        print(f"    {name:<35s} {sign}{val:.4f}")
    print("=" * 60)

    return {"env_name": env_name, "run_path": run_path, "checkpoints": checkpoints}


def resolve_run(user: str, task_substr: str | None = None) -> tuple[wandb.apis.public.Run, dict]:
    """Find latest run for user (optionally filtered to a task-id substring),
    print info, return (run, info). Exits on failure."""
    if task_substr:
        print(f"Searching latest '{task_substr}' run for user '{user}'...")
        runs = find_latest_runs(user, lambda t: task_substr.lower() in t.lower(), 1)
        run = runs[0] if runs else None
    else:
        print(f"Searching latest run for user '{user}'...")
        run = find_latest_run(user)
    if run is None:
        suffix = f" matching '{task_substr}'" if task_substr else ""
        print(f"No run found for user '{user}'{suffix}", file=sys.stderr)
        sys.exit(1)
    info = print_run_info(run)
    return run, info


def run_command(cmd: list[str], dry_run: bool) -> None:
    """Print and optionally execute a command from the project root."""
    print()
    print("Command:")
    print(f"  {' '.join(cmd)}")
    print()
    if dry_run:
        print("(dry-run, not executing)")
        return
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(cmd, cwd=project_root)
