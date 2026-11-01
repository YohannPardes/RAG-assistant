# RB-416: High API Error Rate (5xx)

| Field         | Value                                  |
|---------------|----------------------------------------|
| Service       | public-api (k8s namespace: api)        |
| Severity      | SEV-2 (SEV-1 above 10% 5xx)            |
| Owner         | API Platform team                      |
| Last reviewed | 2026-10-19                             |

## 1. Symptoms

- Alert **ApiErrorRateHigh** fires when the 5xx ratio of public-api is above **2% for 10 minutes**.
- Alert **ApiErrorRateCritical** fires above **10% for 5 minutes** and opens a SEV-1.
- The status page component "Public API" turns to degraded and the "api-availability" SLO burns faster than 6x.

## 2. What each 5xx code means

| Code | Meaning here                                         | Look first at                         |
|------|------------------------------------------------------|---------------------------------------|
| 500  | Unhandled exception in public-api                    | Sentry project `public-api`, last deploy |
| 502  | ingress-nginx got no valid response from a pod       | Pod restarts, `OOMKilled`             |
| 503  | No ready endpoints, or rate limiter rejected request | Header `x-api-reject-reason`          |
| 504  | Upstream exceeded the 30 s ingress timeout           | Slow dependency, see RB-415           |

## 3. Diagnosis

### 3.1 Break errors down by status code and route

```promql
sum by (status) (rate(http_requests_total{namespace="api", status=~"5.."}[5m]))
topk(5, sum by (route) (rate(http_requests_total{namespace="api", status=~"5.."}[5m])))
```

One route with errors points to a feature or dependency; all routes points to the platform.

### 3.2 Check for a recent change

```bash
argocd app history public-api-prod | tail -5
kubectl -n api get pods -l app=public-api --sort-by=.status.containerStatuses[0].restartCount
```

Also check the feature flag audit log in Unleash (project `public-api`) for changes in the last 30 minutes.

### 3.3 Check upstream services

public-api calls `orders-svc` and `payments-gateway` with a 3 s timeout. Its circuit breaker opens when 50% of the last 20 calls fail and then returns 503 for 30 s. Check the "public-api / upstreams" Grafana dashboard for an open breaker.

## 4. Remediation

### 4.1 Use the feature flag kill switch

If the errors come from one feature, turn its flag off rather than rolling back:

```bash
unleash-cli toggle off --project public-api --env production checkout-v3
```

Every new feature in public-api must ship behind a flag, so this is the fastest fix (under 1 minute).

### 4.2 Roll back the deploy with Argo CD

If a deploy happened in the last 2 hours and no flag covers it:

```bash
argocd app rollback public-api-prod <PREVIOUS_REVISION>
```

Auto-sync is paused by the rollback; re-enable it after the fix is merged.

### 4.3 Rate-limit an abusive client

If 503s come from one API key flooding the service, apply the emergency limit of 120 requests per minute per key (default is 600):

```bash
kubectl -n api apply -f ops/ratelimit/emergency-120rpm.yaml
```

> **Warning:** do not restart all public-api pods at once (`kubectl delete pods --all` or scaling to 0).
> Each pod needs about 90 seconds to warm its connection pools, so a mass restart turns partial
> errors into a full outage. Restart at most 3 pods at a time.

## 5. Verification

- The 5xx ratio stays below **0.5%** for 15 minutes and ApiErrorRateHigh resolves.
- `kubectl -n api get pods -l app=public-api` shows no restarts in the last 15 minutes.

## 6. Escalation

| Level | Who                                   | Contact                    |
|-------|---------------------------------------|----------------------------|
| L1    | On-call SRE                           | #ops-oncall                |
| L2    | API Platform on-call                  | PagerDuty: api-platform    |
| L3    | Incident commander (SEV-1 only)       | #inc-bridge                |

See also: RB-415 High API Latency.
