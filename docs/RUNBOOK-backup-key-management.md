# Runbook — backup encryption key custody

**Story:** ST-10.2.3 · **Applies from:** the first encrypted backup

Backups are encrypted with `age` to a single recipient public key. The matching
private key is the only thing that can read them. This document exists because
that fact has one consequence people underestimate:

> **If the private key is lost, every backup encrypted to it is permanently
> unrecoverable.** Not "hard to recover". There is no recovery. The archives
> become 500 MB of noise.

---

## Custody — fill this in

**This table is part of the acceptance criteria. A deployment with blanks here
has not completed ST-10.2.3.** It is left unfilled deliberately: guessing who
holds a hospital's backup key would be worse than an obvious gap.

| | |
|---|---|
| **Primary holder** | `FILL IN — named individual, with role` |
| **Secondary holder** | `FILL IN — a second person, so one holiday is not an outage` |
| **Where the primary copy lives** | `FILL IN — e.g. hospital safe, password manager vault` |
| **Where the offsite copy lives** | `FILL IN — must not be the same building as the backups` |
| **Who may authorise a restore** | `FILL IN` |
| **Reviewed on** | `FILL IN — re-confirm every 6 months` |

Two rules that are not negotiable, whatever you fill in above:

1. **The private key never lives only on the machine that takes the backups.**
   If it does, the fire that takes the server takes the key, and the offsite
   backups you were so careful about are unreadable.
2. **The private key never goes in `.env`, in the repository, or in the same
   store as the backups.** An attacker with the archives and the key has the
   patient database in plaintext; the encryption bought nothing.

---

## Generating the keypair

```bash
./scripts/volume-backup.sh keygen ~/nidan-backup-key.txt
```

Expected: the file is written mode 0600, and the command prints the two values.
Put **only the recipient** in `.env`:

```
BACKUP_AGE_RECIPIENT=age1...        # public — safe in .env
BACKUP_AGE_IDENTITY=/path/to/key    # path only; the key file itself lives elsewhere
```

Then move the key file to its custody locations and remove the working copy from
any machine that does not need it. Backups need only the **public** key; the
private key is required only for a restore.

---

## Rotation

**Rotating the key does not re-encrypt existing backups.** Archives written
before the rotation are still readable only by the old key. This is the part
people get wrong.

1. Generate a new keypair to a new file. Do not overwrite the old one — the
   script refuses to, for this reason.
2. Update `BACKUP_AGE_RECIPIENT` in `.env`.
3. Take a fresh full backup. From here, new archives use the new key.
4. **Keep the old private key** for as long as you keep archives encrypted to
   it. Retire it only when the last of those archives is deleted.
5. Record the rotation date and which archives belong to which key. Without
   that, a restore two years from now is guesswork.

A practical convention: name the backup directory after the key epoch, e.g.
`~/nidan-backups/k2/20260828-0300`, so the archive tells you which key it needs.

---

## If the key is lost

There is no procedure. The archives are unrecoverable.

What you do instead is limit how far back that reaches:

- Take a fresh backup with a new key **today**.
- Treat every older archive as gone and say so, rather than carrying an
  imaginary restore point on a risk register.
- If a restore was your only copy of something, this is a data-loss incident
  and should be handled as one.

The verification job in ST-10.2.4 exists to catch a broken key **before** you
need it, rather than during an outage.

---

## If the key is exposed

Assume every backup encrypted to it is readable by whoever has it.

1. Rotate immediately, per above.
2. Treat the archives as disclosed: they contain the full patient database.
3. This is a notifiable event under the GDPR-equivalent posture the portal is
   held to. Do not quietly re-key and move on.
