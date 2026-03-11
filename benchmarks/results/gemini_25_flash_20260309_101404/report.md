# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Run:** 20260309_101404  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 70 | 35 | 44.98 |
| project_auth_system | ✓ | 120 | 20 | 91.92 |
| project_data_pipeline | ✓ | 92 | 79 | 62.98 |

**Total nodes:** 282  
**Total extraction time:** 199.9s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **38%** | Avg tokens: **268** | ≥80% recall: **6/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 21 | 643 |  |
| q_api_02 | 75% | 23 | 688 | asymmetric — secret is never shared |
| q_api_03 | 20% | 8 | 270 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 4 | 158 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 248 | hard cap at 100 items |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 136 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 25 | 757 | moved away from JSON |
| q_pipe_02 | 0% | 2 | 99 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 25 | 810 |  |
| q_pipe_06 | 100% | 25 | 794 |  |
| q_pipe_07 | 25% | 10 | 340 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 11 | 372 |  |
| q_auth_02 | 17% | 5 | 170 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 80% | 4 | 154 | hardware keys and passkeys supported |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 1 | 62 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 13 | 455 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **38%** | Avg tokens: **273** | ≥80% recall: **6/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 21 | 651 |  |
| q_api_02 | 75% | 23 | 697 | asymmetric — secret is never shared |
| q_api_03 | 20% | 8 | 279 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 3 | 139 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 257 | hard cap at 100 items |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 144 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 25 | 772 | moved away from JSON |
| q_pipe_02 | 0% | 2 | 107 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 25 | 816 |  |
| q_pipe_06 | 100% | 25 | 802 |  |
| q_pipe_07 | 25% | 10 | 349 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 11 | 381 |  |
| q_auth_02 | 17% | 5 | 179 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 80% | 4 | 163 | hardware keys and passkeys supported |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 1 | 71 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 13 | 464 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### filtered

Mean recall: **38%** | Avg tokens: **277** | ≥80% recall: **6/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 21 | 658 |  |
| q_api_02 | 75% | 23 | 704 | asymmetric — secret is never shared |
| q_api_03 | 20% | 8 | 285 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 3 | 146 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 264 | hard cap at 100 items |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 151 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 25 | 778 | moved away from JSON |
| q_pipe_02 | 0% | 2 | 114 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 25 | 823 |  |
| q_pipe_06 | 100% | 25 | 809 |  |
| q_pipe_07 | 25% | 10 | 356 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 11 | 388 |  |
| q_auth_02 | 17% | 5 | 186 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 80% | 4 | 170 | hardware keys and passkeys supported |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 1 | 78 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 13 | 471 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### tight

Mean recall: **37%** | Avg tokens: **240** | ≥80% recall: **6/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 18 | 559 |  |
| q_api_02 | 75% | 18 | 562 | asymmetric — secret is never shared |
| q_api_03 | 20% | 8 | 290 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 3 | 151 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 269 | hard cap at 100 items |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 156 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 18 | 577 | moved away from JSON |
| q_pipe_02 | 0% | 2 | 119 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 17 | 588 |  |
| q_pipe_06 | 80% | 16 | 565 | Prometheus + Grafana |
| q_pipe_07 | 25% | 10 | 360 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 11 | 393 |  |
| q_auth_02 | 17% | 5 | 190 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 80% | 4 | 175 | hardware keys and passkeys supported |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 1 | 82 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 13 | 475 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
