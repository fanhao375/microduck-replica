# Hotspot Recovery Implementation Plan

> Execute sequentially in this isolated worktree. Existing user authorization covers the expected three-step recovery behavior; hardware diagnosis and deployment remain distinct from source tests.

**Goal:** Close reproducible offline-recovery gaps without altering robot calibration or models.

**Architecture:** Keep the existing netcheck state machine and firstboot hotspot-only path. Add a continuous offline budget, explicit loading of the existing keyfile, and verified activation. Tests use the existing injected NetworkManager runner and temporary root; no host network is mutated by tests.

**Tech Stack:** Python standard library, NetworkManager nmcli, unittest, systemd image overlay.

## Task 1 — Baseline and regressions

- [x] Run `py -3.13 -I -m unittest discover -s software/image/tests -p test_netcheck.py -v`: 28 tests passed on cc253f3.
- [x] Extend `software/image/tests/test_netcheck.py` so its fake models disk configuration versus NetworkManager-loaded connections separately.
- [x] Reproduce connecting/disconnected cycles every 30 seconds through second 123, with a saved unreachable network. Require an AP activation attempt by the deadline.
- [x] Require `connection load <verified keyfile>` before activation when only the disk profile exists, preserving custom hotspot credentials.
- [x] Exercise missing-file regeneration, regeneration failure, load failure, missing loaded identity, failed state readback, and retry backoff.
- [x] Run the new suite against unchanged production code and save the failing result.

## Task 2 — Minimal recovery correction

- [x] Modify only `software/image/overlay/usr/local/sbin/microduck-netcheck` for the runtime correction.
- [x] Track an uninterrupted offline window across connection retries; reset it on successful connection, device unavailability, and the existing explicit webpage connection window.
- [x] Prefer loading the existing keyfile; create only if absent. Abort activation when preparation fails and preserve retry backoff.
- [x] Read back activation state before publishing hotspot success.
- [x] Re-run netcheck tests, then `py -3.13 -I -m unittest discover -s software/image/tests -v`; inspect every failure or skip. Focused: 37 passed. Full Windows run: 136 tests, 1 failure/3 errors/1 skip; preserved in verification record with baseline comparison.

## Task 3 — Evidence and boundary

- [x] Update the network troubleshooting documentation with exact behavior and operator steps.
- [x] Save test output and source diff under the dated artifact directory, then inspect `git diff --check` and the final diff.
- [x] Record whether the device-specific root cause was established from its card logs. It was not established; the device was separately restored with approved Wi-Fi and SSH access additions. Do not claim hardware repair or publish an image from unit tests alone.
