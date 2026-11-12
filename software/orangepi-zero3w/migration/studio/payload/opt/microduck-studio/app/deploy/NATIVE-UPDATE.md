# Signed native update boundary

The existing software update remains the first step. Its stable 20261027 engine
updates Studio, this helper and duck-echo without restarting robotd or padd. Its
existing standing/HOME readiness rule is unchanged. The second step is an
explicit, supported and unloaded native update; it adds no HOME requirement.

The publisher may supply `--native-robotd /reviewed/input/robotd` to
`software/updater/build_package.py`. Only the reviewed 5,564,672-byte AArch64 ELF
with SHA256 `febf44e2e5d4e573f4513f25a1ae6480b976c3988dd3d5e7cdfcf84d2a2d5063`
is accepted. Its source is `bdf494ab3fd74898b5159419fbffe87113b4097c`, build
`0791fcd-feetech-qinmo-control.45`, official version 0.15.1, model API2 and IPC40.
The generated `studio/deploy/native/{robotd,manifest.json}` join signed suite
file hashes. Omitting that option retains the original package behavior and
reports native update unavailable. No native binary belongs in Git.

The root helper accepts only `robotd-update` with JSON `by: "Studio"` and
`action: "status" | "install" | "recover"`. Mutations require `confirmed: true`.
There is no path, URL, executable, service, force or downgrade argument. Code
loads only from the current root-owned managed release, after validating all
parents and the suite hashes of the helper, native module and optional payload
pair. The release stays pinned throughout the detached operation.

The only binary target is `/opt/robot/feetech-ft5-r5/robotd`. The supported old
SHA256 is `edf182edb7bf11aeeeedfdab98170ab2ec48a9ec18417f9b68c744b0a60c6d16`.
Preflight checks the direct known systemd ExecStart, actual PID/generation,
`/proc/PID/exe` inode and bytes, exact configuration paths, battery and disk.
Normal control requires two advancing, fresh, stopped fifteen-joint snapshots,
policy off, confirmed torque off and no pending unload, HOME recovery or gain
restoration. Known pre-control startup may proceed with explicit user support
confirmation, then lands in the existing factory commissioning flow. It does
not claim that unknown startup torque was measured off.

Root-private backups preserve the original binary and exact main/saved
parameters and calibration. The factory image can lack
`hd1910-calibration.saved.toml`; absence is preserved. These user files are
never rewritten, including during recovery. A later change or newly created
saved file blocks restoration to normal operation and is never overwritten.

`/var/lib/microduck-helper/native-update/journal.json` is root-owned 0644 under
0755 public parents. It contains bounded status, hashes and process identities,
not user-file bytes. Its transaction backup directory is 0700 and files 0600.
The persistent `active` marker and journal `restore_pending` / `cleanup_pending`
also mean busy. Any remaining owned exclusion file keeps the public reader busy,
including interruption after the active marker was removed. Unreadable,
malformed or missing journals with an exclusion file fail closed.
Studio integration can read `helper.native_update_state()`; app and standalone
voice integration must honor the same bounded, fail-closed guard semantics.

The helper holds its operation lock, service-management lock and the updater's
exclusive motion lease. It queries app/voice readiness before creating its own
busy state. It records known controller load/active/substates and refuses
transitional or unknown states before mutation. Optional absent units are
recorded separately. It prevents controller startup with owned fixed unit
conditions, stops active services, confirms terminal inactive/failed states,
and repeats that exclusion check during the physical and runtime gates. A persistent
commissioning override is fsynced before replacing the binary atomically.
Verification uses the newly running process and actual deployment-info, then
confirms unchanged user files and unloaded feedback before normal mode.

Controller exclusion survives reboot even while normal robotd is being
verified. A boot-local restore permit allows only the verified transaction to
restore its recorded controller states while persistent exclusion stays in
place. Completion and the owned protected-service baseline are durable before
the exclusion is removed. Cleanup remains journaled as pending through every
unlink, directory fsync and service reload; the final record clears it only
after all exclusion files are gone. Recovery also recognizes owned leftovers
from an earlier cleanup without an active marker, verifies real process,
unloaded state and unchanged user files, then finishes cleanup idempotently.
A reboot loses the permit and requires a fresh
process/unload verification. Missing `/run` health baseline is admitted only
after a changed boot identity and owned lifecycle proof; same-boot foreign
identity/baseline changes refuse.

On failure, recovery restores and verifies the old binary in commissioning
before any normal-mode/controller restoration. Otherwise the journal and
guards stay recoverable. `recover` is idempotent and validates persisted backup
hashes and ownership; it never infers completion from a version file. Existing
webpage/voice rollback covers that software layer only.

Failure protection also handles a partial controller restore. It independently
attempts the busy journal, owned controller exclusions and commissioning guard,
then stops robotd before waiting on individual controller stops. A failed or
unknown controller query never skips its fixed stop command or the remaining
services. Every controller and robotd must subsequently be confirmed terminal
before rollback can continue. Stop/query/guard errors retain recovery state;
they never allow restoration to normal mode. A later recovery verifies a new
actual process and unloaded feedback before restoring the recorded controllers.

Qinmo hardware remains experimental. These tests exercise simulated services,
real temporary files/permissions and publisher input. They do not constitute
device deployment, standing, walking or physical-board acceptance.
