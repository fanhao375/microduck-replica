# Consolidate HD1910 training in microduck-replica

The user requested that the just-published training work live in microduck-replica,
and delegated the repository arrangement. This supersedes the earlier decision
to keep training only in a separate fork.

**Goal:** Clone or download one repository and get the complete HD1910 training
source, geometry, parameters, initialization fix, dependencies, tests and docs.

**Design:** Import the tracked training tree from fanhao375/microduck_rl revision
`d9e1926c5220a0eb79fa5aab9df7b336f8e9a226` as a squashed Git subtree at
`software/training/`. Ordinary clones include the contents; no submodule or
cross-repository setup is required. Preserve licenses, parameter bytes and
upstream attribution. Keep logs, caches, videos and virtual environments excluded.
The existing fork remains an historical source, not a second active entry point.

**Scope:** This migration covers the HD1910 training work from this conversation.
It does not move the unrelated Rust runtime or CAD repositories. Preserve the
pre-existing uncommitted training-data checklist in the user's main checkout.

## Steps

- [ ] Import the fixed training revision using `git subtree add --prefix=software/training
  F:/chengshenzhilu/Robot/Microduck/microduck_rl-hd1910 codex/hd1910-m6 --squash`.
- [ ] Make the subtree README's first runnable instructions target this repository
  and HD1910. Preserve the original README as `README.upstream.md`.
- [ ] Update root Chinese/English README, software README, parameter provenance,
  training guide, NOTICE and LICENSE routing to the in-repository training path.
- [ ] Verify parameter checksum and source-file identity for physics and tests.
  Install the moved project into an isolated WSL environment using the lockfile;
  run the 26 relevant tests and the 64-env/5-iteration smoke test from its new path.
- [ ] Independently review migration links, licenses and path behavior. If a real
  issue is reported, have a second agent verify that issue before changing code.
- [ ] Fast-forward local master without changing the user's uncommitted checklist,
  push master and verify that remote code and parameters match.

## Validation boundary

The unchanged simulation baseline was previously verified; migration checks must
prove it works from software/training with the correct import path. A smoke
checkpoint is not a trained or hardware-validated gait. No hardware access occurs.
