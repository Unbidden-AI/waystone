# Benchmark Report

**Model:** `gemini-3.1-flash-lite-preview`  
**Config:** `gemini_31_flash_lite.yaml`  
**Mode:** full  
**Run:** 20260309_182614  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 20 | 5 | 6.62 |
| project_auth_system | ✓ | 18 | 5 | 5.92 |
| project_data_pipeline | ✓ | 29 | 7 | 9.32 |

**Total nodes:** 67  
**Total extraction time:** 21.9s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **42%** | Avg tokens: **265** | ≥80% recall: **2/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 8 | 265 | JWT tokens; scales horizontally (+1 more) |
| q_api_02 | 50% | 10 | 327 | public keys distributed to other services; asymmetric — secret is never shared |
| q_api_03 | 80% | 14 | 484 | not localStorage due to XSS risk |
| q_api_04 | 33% | 5 | 204 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 50% | 2 | 94 | offset pagination breaks when rows are inserted; next_cursor in response (+1 more) |
| q_api_06 | 33% | 7 | 246 | type, title, status, detail, instance; all five fields required |
| q_api_07 | 40% | 9 | 332 | header versioning made caching impossible; v1 and v2 can coexist (+1 more) |
| q_api_08 | 25% | 3 | 136 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+1 more) |
| q_pipe_01 | 50% | 12 | 405 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 7 | 244 | ML team infrastructure already on GCP; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 80% | 14 | 485 | switching would take 6+ weeks |
| q_pipe_04 | 0% | 4 | 158 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 11 | 370 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 6 | 196 | three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency; PagerDuty alerts for 500ms SLA breach |
| q_pipe_07 | 50% | 3 | 120 | minimum in-sync replicas of 2; GCS cross-region replication for cold storage |
| q_auth_01 | 25% | 5 | 189 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+1 more) |
| q_auth_02 | 67% | 15 | 491 | access tokens: 15 minutes; opaque tokens can be instantly revoked |
| q_auth_03 | 40% | 2 | 97 | TOTP as primary second factor; hardware keys and passkeys supported (+1 more) |
| q_auth_04 | 0% | 4 | 155 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 60% | 5 | 225 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 67% | 5 | 230 | no mandatory rotation for regular users; rotation enforced only if breach detected via HIBP |
| q_auth_07 | 17% | 15 | 492 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+3 more) |
| q_auth_08 | 0% | 4 | 160 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **42%** | Avg tokens: **268** | ≥80% recall: **2/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 8 | 274 | JWT tokens; scales horizontally (+1 more) |
| q_api_02 | 50% | 10 | 336 | public keys distributed to other services; asymmetric — secret is never shared |
| q_api_03 | 80% | 14 | 493 | not localStorage due to XSS risk |
| q_api_04 | 33% | 5 | 212 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 50% | 2 | 103 | offset pagination breaks when rows are inserted; next_cursor in response (+1 more) |
| q_api_06 | 33% | 7 | 255 | type, title, status, detail, instance; all five fields required |
| q_api_07 | 40% | 8 | 312 | header versioning made caching impossible; v1 and v2 can coexist (+1 more) |
| q_api_08 | 25% | 3 | 145 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+1 more) |
| q_pipe_01 | 50% | 12 | 414 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 40% | 6 | 227 | originally planned S3; ML team infrastructure already on GCP (+1 more) |
| q_pipe_03 | 80% | 14 | 493 | switching would take 6+ weeks |
| q_pipe_04 | 0% | 4 | 167 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 11 | 379 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 6 | 205 | three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency; PagerDuty alerts for 500ms SLA breach |
| q_pipe_07 | 50% | 3 | 129 | minimum in-sync replicas of 2; GCS cross-region replication for cold storage |
| q_auth_01 | 25% | 5 | 197 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+1 more) |
| q_auth_02 | 67% | 15 | 499 | access tokens: 15 minutes; opaque tokens can be instantly revoked |
| q_auth_03 | 40% | 2 | 106 | TOTP as primary second factor; hardware keys and passkeys supported (+1 more) |
| q_auth_04 | 0% | 4 | 164 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 60% | 4 | 204 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 67% | 4 | 209 | no mandatory rotation for regular users; rotation enforced only if breach detected via HIBP |
| q_auth_07 | 17% | 14 | 475 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+3 more) |
| q_auth_08 | 0% | 4 | 168 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
