# RB-604: HPA Not Scaling

| Field         | Value                                              |
|---------------|----------------------------------------------------|
| Service       | Any HPA-managed deployment (example: checkout-web) |
| Severity      | SEV-3 (SEV-2 if the workload is saturated)         |
| Owner         | Kubernetes Platform team                           |
| Last reviewed | 2026-10-08                                         |

## 1. Symptoms

- Alert **HpaMetricsUnavailable** fires when an HPA reports `<unknown>` targets for **5 minutes**.
- Alert **HpaMaxedOut** fires when `currentReplicas == maxReplicas` for **15 minutes**.
- Pods of `checkout-web` (namespace `shop`) sit at 95% CPU but the replica count stays at 4.

This runbook covers the autoscaler itself. For API latency on `public-api`, use RB-415 instead.

## 2. Diagnosis

### 2.1 Read the HPA status

```bash
kubectl -n shop get hpa checkout-web
kubectl -n shop describe hpa checkout-web
```

`TARGETS` should read like `72%/65%`. Look at the `Conditions` block: `AbleToScale`, `ScalingActive` and `ScalingLimited`. The events at the bottom name the exact failure.

### 2.2 Map the condition to a cause

- `TARGETS` shows `<unknown>/65%`
  - `ScalingActive=False`, reason `FailedGetResourceMetric`: metrics-server is down, see 3.1.
  - `missing request for cpu`: a container has no CPU request, see 3.2.
- `ScalingLimited=True`, reason `TooManyReplicas`
  - The HPA hit `maxReplicas` (12 for checkout-web), see 3.3.
- `AbleToScale=False`, reason `FailedUpdateScale`
  - Check namespace quota: `kubectl -n shop describe resourcequota`.

### 2.3 Check metrics-server

```bash
kubectl -n kube-system get deploy metrics-server
kubectl top pods -n shop
kubectl get apiservice v1beta1.metrics.k8s.io
```

`error: Metrics API not available` or the APIService showing `Available=False` confirms metrics-server is the problem.

## 3. Remediation

### 3.1 Restore metrics-server

```bash
kubectl -n kube-system rollout restart deploy metrics-server
kubectl -n kube-system logs deploy/metrics-server --tail=30
```

Logs with `x509: cannot validate certificate` mean the kubelet serving certs rotated; the Kubernetes Platform team must re-issue them. HPAs resume within one sync period (**15 s**) after metrics return.

### 3.2 Add the missing resource requests

Utilization is a percentage of the request, so every container, including sidecars like `istio-proxy`, needs `resources.requests.cpu`. Set checkout-web to `cpu: 500m`, `memory: 512Mi` in the Helm values (`charts/checkout-web/values-prod.yaml`) and redeploy; do not hand-patch the deployment.

### 3.3 maxReplicas reached

Raise `maxReplicas` for checkout-web only up to **24**; the node pool `shop-pool` has room for about 30 pods. Beyond that, ask Kubernetes Platform to grow the pool first.

> **Warning:** do not delete and re-create the HPA to "reset" it. Between delete and create, the deployment keeps whatever replica count it had, and a later `kubectl apply` of the deployment resets it to `replicas: 2`.

## 4. Verification

- [ ] `kubectl -n shop get hpa checkout-web` shows a numeric target, not `<unknown>`.
- [ ] `ScalingActive=True` in `describe hpa`.
- [ ] Replica count changed within 2 minutes of load crossing the 65% target.
- [ ] **HpaMetricsUnavailable** and **HpaMaxedOut** have resolved.

## 5. Escalation

| Level | Who                          | Contact                  |
|-------|------------------------------|--------------------------|
| L1    | On-call SRE                  | #ops-oncall              |
| L2    | Kubernetes Platform on-call  | PagerDuty: k8s-platform  |
| L3    | Service owner (checkout-web) | #shop-dev                |

See also: [RB-415 High API latency](runbook_high_latency.txt).
