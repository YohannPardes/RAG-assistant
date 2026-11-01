# RB-102: Disk Space Exhaustion on Database Hosts

| Field         | Value                                                     |
|---------------|-----------------------------------------------------------|
| Service       | orders-db (PostgreSQL 16 on db-primary-01, db-replica-02)  |
| Severity      | SEV-2 (SEV-1 above 97% on the primary)                    |
| Owner         | Data Platform team                                        |
| Last reviewed | 2026-10-14                                                |

## 1. Symptoms

- Alert **PgDataDiskUsageHigh** fires when `/var/lib/postgresql` is above **80%** for 10 minutes (warning) and above **90%** (critical).
- Alert **PgWalArchiveFailing** fires when `pg_stat_archiver.failed_count` keeps growing for 15 minutes.
- PostgreSQL logs `could not write to file "pg_wal/xlogtemp": No space left on device` and the primary enters a PANIC restart loop.

## 2. Disk layout on database hosts

The data volume is a 2 TB XFS logical volume `vg_pg/lv_data` mounted on `/var/lib/postgresql`. WAL lives in `/var/lib/postgresql/16/main/pg_wal` on the same volume. `max_wal_size` is 64 GB and pg_wal normally holds 8 to 20 GB. More than 100 GB in pg_wal means WAL is being retained, not just written: look at archiving and replication slots first.

## 3. Diagnosis

### 3.1 Find what is growing

```bash
sudo du -sh /var/lib/postgresql/16/main/{base,pg_wal,log}
sudo -u postgres psql -c "SELECT pg_size_pretty(sum(size)) FROM pg_ls_waldir();"
```

If `base` is growing, it is table bloat or data growth (ticket for Data Platform, not an incident).

### 3.2 Check WAL archiving to object storage

```bash
sudo -u postgres psql -xc "SELECT archived_count, failed_count, last_failed_wal, last_failed_time FROM pg_stat_archiver;"
journalctl -u postgresql@16-main --since "1 hour ago" | grep wal-g
```

WAL is shipped by `wal-g wal-push` to the bucket `s3://acme-orders-db-wal/`. A `403` or `SignatureDoesNotMatch` error means the credentials in `/etc/wal-g/env` have expired.

### 3.3 Check replication slots retaining WAL

```sql
SELECT slot_name, active,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
FROM pg_replication_slots;
```

An inactive slot retaining more than **50 GB** is the usual culprit, typically left by a decommissioned replica or a stopped CDC connector (`debezium_orders`).

## 4. Remediation

### 4.1 Repair WAL archiving

1. Refresh the credentials from Vault path `secret/data-platform/wal-g`:
   ```bash
   sudo systemctl restart wal-g-creds.service
   ```
2. PostgreSQL retries the archive by itself. Once `failed_count` stops growing, run `CHECKPOINT;` so old segments are recycled.

### 4.2 Drop an abandoned replication slot

Confirm with Data Platform on-call that the slot's consumer is gone for good, then:

```sql
SELECT pg_drop_replication_slot('debezium_orders');
CHECKPOINT;
```

Space is freed within a few minutes of the checkpoint.

### 4.3 Emergency headroom

If usage is above 95% and the primary is still up, extend the volume. Each database host keeps 200 GB unallocated in `vg_pg`:

```bash
sudo lvextend -r -L +200G /dev/vg_pg/lv_data
```

> **Warning:** never delete files from `pg_wal` by hand, and never run `pg_resetwal` on the primary.
> Removing WAL segments corrupts the cluster and breaks every replica; recovery then means a full
> restore from the nightly base backup.

## 5. Escalation

| Level | Who                                   | Contact                  |
|-------|---------------------------------------|--------------------------|
| L1    | On-call SRE                           | #ops-oncall              |
| L2    | Data Platform on-call                 | PagerDuty: data-platform |
| L3    | Storage team (LVM or SAN problems)    | PagerDuty: storage       |

See also: RB-101 Disk Space Exhaustion on Log Servers, RB-204 PostgreSQL Primary Failover.
