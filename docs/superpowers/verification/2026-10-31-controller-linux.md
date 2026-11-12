# Controller Linux, real systemd and original-27 rollback verification

Runtime source: `26150eb61407d3498dc2e2c5d1f9dc3ea130acde` (reviewed `11a31e06`
behavior plus the three real padd pins). Later `36f12c3` merges documentation;
the software/studio, duck-echo and updater bytes remain identical to 26150eb.
No production source, deployment file or existing artifact was changed by these
tests. The candidate remained local and unpublished during validation.

Local candidate: `2026.10.31.software-update.tar`, 9,287,680 bytes, SHA256
`5f6a597abe27d9c4af8e137841c9dcab4b6c4b378f44c8f88f4c7b4f6ae09204`.
Padd source `665613ac628fdc9b1b71f324809f0c16ce2795f2`, ARM size 3,522,648,
SHA256 `dc1c7043a108964583fc09a11e3d6f87a25e259c346f7ce9154e77500a0881ec`.
The native owner separately recorded identical a/b builds, 113 tests, ELF/GLIBC
and actual QEMU main/status evidence. The checks here consume those frozen inputs.

## Evidence index

Local artifact root:
`F:/chengshenzhilu/Robot/Microduck/artifacts/software-features-20261030/linux-controller-evidence/`

| Evidence | Result | Actual boundary and limitation |
| --- | --- | --- |
| `controller-linux-165-public.log` | 165 executed, no failures or skips, 25.385s | Real Linux permissions, directory fsync, nobody, root-owned public/private files and UNIX sockets; hardware/service transaction backend remains isolated fixtures. |
| `voice-mapping-linux.log` | 13 executed, no failures or skips, 0.031s | Python3.11 including genuine different-UID signal0 EPERM and live mapping reader; no physical evdev. |
| `native-contract-final.json` | 10 categories and 322 bundle socket probes passed | Frozen real Rust production socket handler with synthetic events and shared actual-serializer stopped proof, plus real Python learner and `controller_bundle.mapping_status`. |
| `final-legacy-guard-{deny,restored}-process.json` | Both fresh-root cases passed | Original control service and actual ARM engine, non-root real child processes/guard. Explicitly not systemd. |
| `systemd/final-legacy-guard-{deny,restored}-systemd.json` | Both fresh-root cases passed | Actual systemd PID1, ExecStartPre, UID/groups, StateDirectory, restart generations and kernel address-family enforcement. Daemon bodies and native restoration remain fixtures. |
| `systemd/root-app-unix-readiness.json` | All assertions passed | Root production recovery probe to real build_app under UID65534 over its owner-only 0600 UNIX socket, with actual SO_PEERCRED. Robot/audio data are fixtures. |

The respective `.log`, `.engine.log`, `.debug.json` and child log are retained.
The earlier `controller-linux-165.log` records a test setup failure: nobody could
not import the test source through `/root`0700. The unchanged frozen archive was
copied to root-owned public `/var/tmp/...` and rerun successfully. `/root`
permissions were never loosened. The core suite used Python3.13; voice used
public Python3.11. The extra different-UID whole-app probe used public Noble
Python3.12 because the app's f-string syntax is unsupported by Python3.11.

## Genuine systemd environment

The existing Ubuntu instance uses init as PID1. No global WSL setting or existing
distro configuration was changed. Root provisioned the official, hash-checked
Noble image as disposable `Microduck-Systemd-Test-20261031` on F:. Only that root
enabled systemd. QEMU user mode was installed without binfmt registration.

The script requires PID1=systemd and the exact root-created
`/etc/microduck-proof-distro` marker before it creates anything. It creates only
`microduck-proof-*` units in `/run/systemd/system`, plus test-only guest users
padd/radxa/mediad and robot group. All services use sentinel Python bodies rather
than hardware drivers. No port8000, camera, GPIO, UART, input device or live robot
was accessed. Owned production drop-in bodies are copied without changing their
StateDirectory, group, ExecStartPre or address-family directives.

The kernel/process assertions establish:

- Effective robot supplementary group 989 reached the restarted Studio/voice
  processes while the existing nogroup membership remained.
- Padd StateDirectory was service-owned UID999/GID988 with mode0750. Its profile
  survived unit restoration. These are allocated test-distro IDs, not board IDs.
- All four affected services acquired new PIDs; the fixture media executable's
  SHA remained unchanged. The robotd fixture PID stayed fixed.
- A real AF_NETLINK socket failed under the old media whitelist and succeeded
  under the owned whitelist after restart. Merely reading systemctl properties
  was not accepted as evidence of seccomp effectiveness.
- The frozen external guard was a real ExecStartPre under radxa UID997. A
  root0700 compatibility parent denied even a compatible consumer, proving that
  the gate was not accidentally running with root access.

The engine runs in its own mount namespace with fixed service proxies; its
absolute systemctl/systemd-run calls cannot affect unrelated guest units.
The proxy reaches the real manager for only the dedicated units. The external
guard itself runs without arguments at its production absolute path, with a
controlled bind of the temporary software tree. These mounts were removed after
the checks. Final list-units returned no dedicated units; all fixture processes
were stopped, unit files removed and daemon-reload completed. Root owns final
registry verification and destruction of the single disposable distro/download.

## Original-27 direct rollback combination

The frozen original updaterd SHA is
`d5f9086bae5c2ec10ba75fc2d41c27578236a12d9a6364bffa8d9315fda97fa6`.
The original signed 27 envelope SHA is
`b6543c9f3a2ad6477106410dbed9753be9fa37c61b79ea0350767a18229d0e55`.
The actual original native bytes were extracted from payload SHA
`370f64af5c690eeebab429f4c59d9c5d00883b5cf1e7f0d0e2c18d4afe45998d`:
robotd SHA `edf182edb7bf11aeeeedfdab98170ab2ec48a9ec18417f9b68c744b0a60c6d16`,
padd SHA `85fc8051df2ce496abf32bbb5eba3c94c44d875365a8f33a2f53f90facba90db`.

There was no robotctl member in that payload. A separate non-root CLI/API client
(UID65534) therefore sent `{"op":"rollback"}` to the actual original
`e382f0741d7dfe4e972605e2843474df7219cade` control.Server, which called the actual
ARM engine under QEMU. This bypassed the new Studio rollback interception.
The old preflight function ran with synthetic quiet robot/studio/audio sockets;
its readonly identity backend mapped robotd/padd labels to the dedicated fixture
processes/units. Its code was not modified to ignore the compatibility guard.

Two independent temporary histories were used:

1. With the native compatibility requirement present, the engine actually moved
   current to 27. The external guard refused old voice before its sentinel body
   could run, and the engine restored 31 with compatible voice active.
2. In a fresh history, the fixture first restored the exact old native bytes and
   owned unit preimages, verified retained profiles, then removed the requirement.
   The same old direct rollback reached 27 and old voice could start.

The second case is explicitly a **fixed-file/native-unit restore fixture**, not
execution of production `C.handle` against ARM hardware. The production vector
restore ordering and interruptions are exercised separately by the 165-test
Linux suite. Failed old-engine rollback marks the attempted old release known-bad;
the negative case's journal was not erased to make the positive case pass.
The two fresh histories preserve that existing engine behavior.

## Root/app readiness isolation

The additional probe used actual NativeHttp/build_app lifespan in a UID65534
child on an ephemeral loopback port. Root connected to its 0600 socket and
checked peer credentials. Ordinary readiness remained false for the prepared
guard; only the exact transaction's private recovery opcode returned ready.
Wrong transaction, software marker and real servo activity still refused. A
second unprivileged UID65533 was denied by socket/directory permissions. The
production root `owned`, `unpublished_readiness` and fresh gate functions ran;
only robot/audio inputs stayed synthetic.

## Reproduction and remaining boundary

The three assertion-driven harnesses are in
[scripts/controller-20261031](scripts/controller-20261031):

- `legacy_guard_combination.py`: use distinct fresh roots for normal and
  `--restore-first`; add `--systemd` only inside the marker-protected test distro.
- `root_app_unix_readiness.py`: real different-UID application/socket checks.
- `run_controller_native_contract.py`: the frozen fixture SHA plus learner/socket
  contract. Its fixture is the independently saved native test executable.

The final combination harness SHA is
`a6e458c66f5fecca0454b06626160d83e9386b9de52d11be5f4e8733d51e9e76`, recorded in
each final result. Release/native inputs have explicit hash assertions; test
source is exported from the stated frozen Git objects before execution. An
unavailable runtime or changed native fixture fails rather than silently skipping.

These checks establish the stated Linux, service and downgrade boundaries. They
do not establish physical-controller/voice/motion/camera acceptance, actual ARM
hardware deployment, or one end-to-end production controller transaction with
real UART/evdev devices. The local candidate was not published or deployed by
this validation task. The signed envelope's separate stage1 install, signature,
health rollback and crash-recovery acceptance remains recorded by root.


## Final public software release

[Software 2026.10.31](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.31)
was published after independent review with no remaining Critical/Important/Minor
findings. Its tag and runtime source are `26150eb`; later documentation commits do not
change the Studio, voice or updater trees.

- All 11 GitHub attachment sizes/SHA256 digests match the independently checked local
  inventory. Software is 9,287,680 bytes, SHA256
  `5f6a597abe27d9c4af8e137841c9dcab4b6c4b378f44c8f88f4c7b4f6ae09204`.
- Bootstrap is 61,425,062 bytes, SHA256
  `8a12c6ee378782d7441b0347c3bd15524ade9c5c69151dcd5121d01f4d4cb6ee`.
  Its 319 declared files were individually checked, including 304 resources, original
  ARM engine/public key and the exact same signed software envelope.
- The actual original 27 client anonymously discovered 31 from the public catalog,
  downloaded the software package and verified both minisign signatures with its
  original public key. The 27 image remains GitHub Latest.
- Native source export `27d7921dbcb280bd7d6ba3e9937624f1e2e61d23`, identified in the [release provenance](https://github.com/fanhao375/microduck-replica/releases/download/microduck-software-v2026.10.31/runtime-provenance.json),
  has program/build-input blobs and modes identical to the original frozen 665 source.
  Its sole parent is the existing public baseline. Protected local console changes
  and history were excluded; the unchanged pre-push hook passed normally. The new
  public recipe was syntax/gate checked, including `python -O` rejection, but not used
  to rebuild the frozen binary.

Public identities and scope are in the attached `runtime-provenance.json`. Local
publication evidence is `publication-assets.json`, `local-assets-verification.json`,
`draft-publication-verification.json` and `public-publication-verification.json`
under the task artifact root. Publication does not install or accept the new
controller bundle on a physical duck; that separate device gate remains pending.
