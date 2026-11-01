# RB-702: Object Storage Access Denied

| Field         | Value                                                   |
|---------------|---------------------------------------------------------|
| Service       | S3-compatible object storage (`s3.internal.example.com`) |
| Severity      | SEV-3 (SEV-2 if customer uploads fail)                  |
| Owner         | Storage team                                            |
| Last reviewed | 2026-05-27                                              |

## 1. Symptoms

- Alert **ObjectStorage403Rate** fires when more than **5%** of requests to a bucket return `403` for 10 minutes.
- Application logs show `An error occurred (AccessDenied) when calling the PutObject operation: Access Denied`.
- Most affected buckets: `app-uploads-prod` (customer uploads) and `invoices-archive` (billing PDFs, write-once).

## 2. Diagnosis

### 2.1 Who is calling?

Run the check from inside the failing pod so it uses the same identity:

```bash
kubectl -n shop exec deploy/upload-service -- \
  aws sts get-caller-identity --endpoint-url https://sts.internal.example.com
```

The ARN must be the role the bucket policy expects, for example `arn:aws:iam::412000000001:role/upload-service`. A node role in the ARN means IRSA is not working (see 3.2).

### 2.2 What does the bucket allow?

```bash
aws s3api get-bucket-policy --bucket app-uploads-prod \
  --endpoint-url https://s3.internal.example.com --output text
aws s3api head-object --bucket app-uploads-prod --key healthcheck.txt \
  --endpoint-url https://s3.internal.example.com
```

Look for an explicit `"Effect": "Deny"`; it always wins over an Allow. `invoices-archive` denies `s3:DeleteObject` to every principal on purpose.

### 2.3 Common causes

| Cause                    | Signal                                  | Fix          |
|--------------------------|-----------------------------------------|--------------|
| Expired static keys      | `InvalidAccessKeyId` or 403 on all calls | section 3.1 |
| IRSA annotation missing  | caller ARN is the node role             | section 3.2  |
| Clock skew over 15 min   | `RequestTimeTooSkewed`                  | fix NTP on the node |
| Policy changed recently  | 403 started right after a Terraform apply | revert the policy change |

## 3. Remediation

### 3.1 Renew expired static credentials

Static access keys expire after **90 days**. Request a new key pair with `storagectl keys create --user <SERVICE> --ttl 90d`, store it in the secret `s3-credentials` of the service's namespace, then restart the deployment with `kubectl rollout restart`.

### 3.2 Repair IRSA

The service account needs the annotation `eks.amazonaws.com/role-arn` with the role ARN. Projected tokens are valid for **86400 seconds**; pods older than that with an old SDK fail to refresh. Add the annotation through the Helm values and restart the pods.

> **Warning:** do not add `"Principal": "*"` to a bucket policy to unblock a service. It makes the bucket readable by every identity on the platform. Fix the role or the policy statement for that one principal instead.

## 4. Escalation

| Level | Who                | Contact                              |
|-------|--------------------|--------------------------------------|
| L1    | On-call SRE        | #ops-oncall                          |
| L2    | Storage on-call    | PagerDuty: storage                   |
| L3    | Security on-call   | PagerDuty: security-oncall (unexpected caller ARNs) |
