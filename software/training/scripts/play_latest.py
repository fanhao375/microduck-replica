"""Find the latest wandb run for a given user and launch `uv run play`.

Par défaut : le tout dernier run (toutes tâches confondues).
Avec un flag de type, le dernier run de CE type seulement :
    md-play --crouch      # dernier Mjlab-RollerCrouch-...
    md-play --roller      # dernier Mjlab-...-Rollers
    md-play --swizzle     # dernier Mjlab-...-Swizzle-...
    md-play --slope       # dernier Mjlab-RollerSlope-...
Les arguments inconnus sont transmis tels quels à `uv run play`
(ex: md-play --crouch --action-scale 0.8).
"""

import argparse

from wandb_utils import resolve_run, run_command

# flag -> sous-chaîne recherchée dans le task_id (metadata args[0])
TYPE_SUBSTR = {
    "crouch": "Crouch",     # Mjlab-RollerCrouch-Flat-MicroDuck
    "roller": "MicroDuck-Rollers",  # Mjlab-Velocity-Flat-MicroDuck-Rollers (≠ RollerSlope/RollerCrouch)
    "swizzle": "Swizzle",   # Mjlab-Velocity-Swizzle-MicroDuck
    "slope": "Slope",       # Mjlab-RollerSlope-Flat-MicroDuck
}


def main():
    parser = argparse.ArgumentParser(description="Play latest wandb run for a user")
    parser.add_argument(
        "--user", default="coralie",
        help="Filter runs by user (matched against email, default: coralie)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the play command without executing it",
    )
    for t in TYPE_SUBSTR:
        parser.add_argument(
            f"--{t}", dest="type", action="store_const", const=t,
            help=f"latest '{t}' run only",
        )
    # unknown args (e.g. --action-scale 0.8) are forwarded to `uv run play`
    args, extra = parser.parse_known_args()

    task_substr = TYPE_SUBSTR[args.type] if getattr(args, "type", None) else None
    _, info = resolve_run(args.user, task_substr)

    cmd = [
        "uv", "run", "play",
        info["env_name"],
        "--wandb-run-path", info["run_path"],
        *extra,
    ]
    run_command(cmd, args.dry_run)


if __name__ == "__main__":
    main()
