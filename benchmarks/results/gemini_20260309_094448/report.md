# Benchmark Report

**Model:** `gemini-3-flash-preview`  
**Config:** `gemini.yaml`  
**Run:** 20260309_094448  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✗ `Could not parse JSON from LLM response.
` | — | — | 319.84 |
| project_auth_system | ✓ | 30 | 10 | 33.25 |
| project_data_pipeline | ✓ | 27 | 10 | 27.26 |

**Total nodes:** 57  
**Total extraction time:** 380.3s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **18%** | Avg tokens: **73** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 25% | 3 | 131 | private key on the auth service; public keys distributed to other services (+1 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 2 | 108 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 7 | 286 | moved away from JSON |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 7 | 299 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 40% | 6 | 250 | cold path up to 5 minutes; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 25% | 2 | 113 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 3 | 143 | non-starter for enterprise customers |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 1 | 61 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 2 | 103 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 2 | 106 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 17% | 1 | 76 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+3 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **18%** | Avg tokens: **77** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 25% | 3 | 140 | private key on the auth service; public keys distributed to other services (+1 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 2 | 117 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 7 | 295 | moved away from JSON |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 7 | 308 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 40% | 6 | 259 | cold path up to 5 minutes; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 25% | 2 | 122 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 3 | 152 | non-starter for enterprise customers |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 1 | 70 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 2 | 112 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 2 | 115 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 17% | 1 | 85 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+3 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### filtered

Mean recall: **18%** | Avg tokens: **80** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 25% | 3 | 147 | private key on the auth service; public keys distributed to other services (+1 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 2 | 124 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 7 | 301 | moved away from JSON |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 7 | 314 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 40% | 6 | 266 | cold path up to 5 minutes; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 25% | 2 | 129 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 3 | 158 | non-starter for enterprise customers |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 1 | 77 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 2 | 118 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 2 | 121 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 17% | 1 | 91 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+3 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### tight

Mean recall: **18%** | Avg tokens: **83** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 25% | 3 | 152 | private key on the auth service; public keys distributed to other services (+1 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 2 | 129 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 7 | 306 | moved away from JSON |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 7 | 319 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 40% | 6 | 271 | cold path up to 5 minutes; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 25% | 2 | 133 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 3 | 163 | non-starter for enterprise customers |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 1 | 82 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 2 | 123 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 2 | 126 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 17% | 1 | 96 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+3 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
