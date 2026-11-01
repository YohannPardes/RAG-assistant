# RB-313: CDN Serving Stale Content

| Field         | Value                                          |
|---------------|------------------------------------------------|
| Service       | cdn-edge (static.example.com, www.example.com) |
| Severity      | SEV-3 (SEV-2 if pricing or legal pages)        |
| Owner         | Edge Networking team                           |
| Last reviewed | 2026-08-27                                     |

## 1. Symptoms

- Alert **CdnStaleObjectAge** fires when the `Age` header on the synthetic probe object is above **3600 s** for 15 minutes.
- Alert **CdnOriginMismatch** fires when the ETag served at the edge differs from origin for more than **10 minutes** after a deploy.
- Users still see the previous release banner, or `main.<hash>.js` returns 404 because the HTML is cached but the old bundle is gone.

## 2. Caching Policy Reference

| Path pattern          | Cache-Control                          | Surrogate key        |
|-----------------------|----------------------------------------|----------------------|
| `/assets/*` (hashed)  | `public, max-age=31536000, immutable`  | `assets`             |
| `/*.html`             | `public, s-maxage=300, max-age=0`      | `html`, `page-<id>`  |
| `/api/catalog/*`      | `public, s-maxage=60`                  | `catalog`            |
| `/pricing`, `/legal/*`| `public, s-maxage=120`                 | `pricing`, `legal`   |

Requests go edge POP → origin shield (`shield-fra`) → origin bucket `web-static-prod`.

## 3. Diagnosis

### 3.1 Compare edge and origin headers

```bash
curl -sI https://www.example.com/pricing | grep -iE 'age|etag|x-cache|surrogate-key'
curl -sI -H 'Host: www.example.com' https://origin.web-static-prod.internal/pricing | grep -i etag
```

A high `Age`, `X-Cache: HIT` and a different ETag mean the edge holds an old object. If the origin ETag is also old, the deploy itself did not upload the file: stop here and check the release pipeline.

### 3.2 Check the origin shield

```bash
curl -sI -H 'Fastly-Debug: 1' https://www.example.com/pricing | grep -i x-served-by
```

If `X-Served-By` lists `shield-fra` with `HIT`, the shield is stale too; a purge at the edge alone will refill from the stale shield. Purges by surrogate key clear both tiers.

## 4. Remediation

### 4.1 Purge by surrogate key (preferred)

```bash
cdnctl purge key pricing --service www-prod --soft
```

`--soft` marks objects stale instead of deleting them, so the edge can still serve them if origin is down. Purge completes globally in about **150 ms**; allow 30 s before re-checking.

### 4.2 Purge a single URL

```bash
cdnctl purge url https://www.example.com/legal/terms --service www-prod
```

The purge API allows **100 URL purges per minute** and **10 key purges per minute** per service token. A `429 Too Many Requests` means wait 60 s; do not loop the command.

> **Warning:** do not run `cdnctl purge all` between **08:00 and 22:00 UTC**. A full-site purge sends every request to `web-static-prod` at once and has overloaded the origin shield (incident 2026-03-14). Outside those hours it still needs approval from Edge Networking on-call.

## 5. Escalation

| Level | Who                      | Contact                 |
|-------|--------------------------|-------------------------|
| L1    | On-call SRE              | #ops-oncall             |
| L2    | Edge Networking on-call  | PagerDuty: edge-net     |
| L3    | CDN vendor support       | Enterprise ticket, P1 for global staleness |

See also: [RB-310 TLS Certificate Expiry](runbook_cert_expiry.md), [RB-314 Load Balancer Health Check Failures](runbook_lb_health_checks.txt).
