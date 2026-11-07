---
name: solar-local-recovery
description: Refresh the latest complete SolarController backup and restore an isolated local Firestore/dashboard environment. Use for copying current remote data locally or validating recovery; not for production deployment or device control.
---

# Solar local recovery

Use the checkout containing `scripts/refresh_local_recovery_from_env.ps1`. The current implementation checkout is `C:/VSC/SolerControler-local-recovery-20261108`; the original `C:/VSC/SolerControler` may contain unrelated uncommitted work. Preserve it.

The user chose **one latest complete backup**, with no retained generations. Capture into `artifacts/backups/local-recovery/refresh.pending`, verify, then replace `latest`. The previous directory is only a temporary replacement safeguard and is removed after success. An interrupted/failed capture never replaces a verified latest. Do not remove unrelated legacy backups.

## Refresh and restore

Read `docs/current/ops/COMPLETE_LOCAL_RECOVERY_BACKUP_JA.md` and the current repository instructions. Inspect current processes/receipts before resuming an interrupted operation. A read-only acquisition and local recovery request does not authorize production restore, deployment, scheduler changes, device writes, or cached-image cloud export.

Run from the implementation checkout:

```powershell
pwsh -NoProfile -File scripts/refresh_local_recovery_from_env.ps1
```

This calls the canonical `backup_operational_state_from_env.ps1 -Complete`, checks every captured hash and offline document recovery, publishes `latest`, and restores its source revision and typed Firestore documents locally. It does not reconstruct credential-bearing cloud commands. Credentials are loaded by the canonical wrapper from the ignored `.env`.

- Backup only: add `-BackupOnly`.
- Recreate the local environment from the already verified latest without accessing production: add `-RestoreOnly`.
- An unfinished `refresh.pending` requires examination of its manifest and actual process first, then explicit `-Resume` with the same root. Resume means finishing that capture's snapshot, not a fresh current capture. Do not start a second capture or edit failed/running states to success.
- If the full capture is incomplete but the source/Firestore/configuration/secret stages are verified, `python scripts/local_recovery.py preview-pending --root artifacts/backups/local-recovery` can demonstrate the available local data without publishing latest. This is explicitly PARTIAL, not a full backup or recovery PASS. Run `scripts/check_local_recovery.py` separately and keep missing originals/Drive/image evidence visible. Never use preview as a way to satisfy the complete-backup gate.
- Concurrent refreshes are excluded by an OS file lock. Never delete a live operation's lock.
- The default local page is `http://127.0.0.1:8780/`; Firestore uses loopback port 8781. A conflicting unowned listener is an error, not permission to terminate it. `-Port` and `-EmulatorPort` may choose other free ports.

## Local prerequisites and boundaries

Use existing project Python dependencies, Git, PowerShell 7, and Java 21 or newer. The pinned official Firestore emulator is `~/.cache/firebase/emulators/cloud-firestore-emulator-v1.21.0.jar`. If missing, use the official Firebase CLI setup (`npx --yes firebase-tools setup:emulators:firestore`), verify its actual filename/version, and pass `--jar` to the Python restore command if the official version differs. Do not add dependencies to the application's requirements just for emulator setup.

The restored source comes from the captured Git bundle at its recorded source revision. Do not copy production `.env`, passwords, service credentials or live scheduler configuration into the runtime. It uses a demo project and a local Firestore emulator; referenceValue project prefixes are mapped, while the immutable backup retains original typed values/paths. All document fields are read back and compared using canonical protobuf values. Server-generated createTime/updateTime will be new in the emulator.

The local dashboard uses the recovered application's Firestore backend. Its HTTP writes are disabled; its launcher strips production environment variables and credentials and blocks external Python network connections. No control/planner scheduler is installed or started. Emulator limitations still apply; local success is not proof of cloud IAM/index/concurrency behavior or real-device operation.

Stop only processes whose recorded PID and unique instance argument match this recovery instance:

```powershell
python scripts/local_recovery.py stop --root artifacts/backups/local-recovery
```

The complete backup contains private originals and recovery secrets. Its root must remain Git-ignored and restricted to the current Windows user and SYSTEM. Do not attach it to chat, push it, or publish raw identifiers. A failed API read is not authority to enable APIs or change IAM.

## Acceptance

Inspect `latest/summary.safe.json` and `latest/manifest.private.json`: require `status=complete`, `gaps=[]`, all mandatory stages successful, and every hash matching. Require `runtime/receipt.safe.json` and full typed-document readback. `scripts/check_local_recovery.py` is run automatically after recovery: it creates the secondary SQLite model from the local emulator, compares the six dashboard datasets, checks saved forecast/SOC calculations against frozen GCS originals, and checks desktop/mobile browser rendering and rejected HTTP writes. Read `runtime/acceptance/summary.safe.json` and the private details/PNGs. An acceptance failure leaves the local page available but is not PASS.

Report snapshot read time, capture status, document counts, source revision, local URL, browser result, and remaining gaps separately. Saved-input forecast verification is distinct from a new forecast using live weather; live weather is NOT_RUN. Never claim that a backup capture alone proves production restoration.

The source revision saved by `local` is the capture checkout's revision. The checker hashes the recovered app/static/entry files against the captured deployed dashboard image's OCI layers before claiming an application-code match. This does not prove identical OS/Python dependencies or cloud IAM/index behavior. The backup retains cloud runtime images/configuration for a later approved production recovery.
