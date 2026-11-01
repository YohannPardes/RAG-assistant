# RB-502: Kafka Under-Replicated Partitions

| Field         | Value                                        |
|---------------|----------------------------------------------|
| Service       | kafka-events (namespace `streaming`)         |
| Severity      | SEV-3 (SEV-1 if any partition is offline)    |
| Owner         | Streaming team                               |
| Last reviewed | 2026-09-03                                   |

## 1. Symptoms

- Alert **KafkaUnderReplicatedPartitions** fires when `kafka_server_replicamanager_underreplicatedpartitions` is above **0** for 5 minutes on any broker.
- Alert **KafkaOfflinePartitions** fires as soon as one partition has no leader. This is a SEV-1.
- Producers using `acks=all` log `NotEnoughReplicasException` once a partition drops below `min.insync.replicas`.

## 2. Replication settings

Every topic on `kafka-events` uses replication factor **3** and `min.insync.replicas=2`. A follower leaves the ISR (in-sync replicas) when it is more than `replica.lag.time.max.ms` = **30000 ms** behind the leader. With one broker down, writes still succeed; with two replicas of the same partition down, `acks=all` producers are rejected.

## 3. Diagnosis

### 3.1 List the affected partitions

```bash
kafka-topics.sh --bootstrap-server kafka-events-0.kafka-events.streaming:9092 \
  --describe --under-replicated-partitions
```

If every affected partition is missing the same broker id from its `Isr` list, that broker is the problem. Partitions spread across several brokers point to network or disk throughput.

### 3.2 Check the broker pods

```bash
kubectl -n streaming get pods -l app=kafka-events -o wide
kubectl -n streaming logs kafka-events-2 --since=30m | grep -E "ERROR|Shrinking ISR"
kubectl -n streaming exec kafka-events-2 -- df -h /var/lib/kafka
```

Each broker has a **2 TiB** PVC. A data disk above 90% full stops replica fetching; ask the Storage team to expand the PVC.

## 4. Remediation

### 4.1 Restart a failed broker

Brokers run as the StatefulSet `kafka-events`. Delete the broken pod and let it come back with the same PVC:

```bash
kubectl -n streaming delete pod kafka-events-2
```

Catch-up usually takes 10 to 40 minutes. Wait for the URP count to reach 0 before touching any other broker.

### 4.2 Run a preferred leader election

After the broker is back in every ISR, leaders stay on the other brokers. Move them back to their preferred replica:

```bash
kafka-leader-election.sh --bootstrap-server kafka-events-0.kafka-events.streaming:9092 \
  --election-type PREFERRED --all-topic-partitions
```

> **Warning:** do not set `unclean.leader.election.enable=true` to bring an offline partition back. An out-of-sync replica becomes leader and every message it had not yet copied is lost for good. Page the Streaming on-call instead.

Restart at most one broker at a time; two brokers down at once makes partitions with both replicas on them unwritable.

## 5. Escalation

| Level | Who                 | Contact                     |
|-------|---------------------|-----------------------------|
| L1    | On-call SRE         | #ops-oncall                 |
| L2    | Streaming on-call   | PagerDuty: streaming        |
| L3    | Storage on-call     | PagerDuty: storage (PVC or disk failures) |

See also: [RB-501 Kafka consumer lag](runbook_kafka_consumer_lag.docx).
