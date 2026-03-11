# Benchmark Report

**Model:** `gemini-3-flash-preview`  
**Config:** `gemini.yaml`  
**Run:** 20260309_095255  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 24 | 7 | 13.14 |
| project_auth_system | ✓ | 26 | 8 | 13.69 |
| project_data_pipeline | ✓ | 23 | 8 | 13.5 |

**Total nodes:** 73  
**Total extraction time:** 40.3s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **27%** | Avg tokens: **113** | ≥80% recall: **2/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 2 | 112 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 7 | 272 | asymmetric — secret is never shared |
| q_api_03 | 20% | 3 | 136 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 3 | 123 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 3 | 140 | next_cursor in response |
| q_api_06 | 0% | 1 | 66 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 4 | 169 | v1 and v2 can coexist; Deprecation header on sunset endpoints |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 7 | 269 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 80% | 6 | 229 | ML team infrastructure already on GCP |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 67 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 6 | 237 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 20% | 6 | 231 | cold path up to 5 minutes; Prometheus + Grafana (+2 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 25% | 3 | 126 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers (+1 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 3 | 136 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 1 | 63 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 6 | 231 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **26%** | Avg tokens: **116** | ≥80% recall: **1/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 2 | 121 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 7 | 281 | asymmetric — secret is never shared |
| q_api_03 | 20% | 3 | 145 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 2 | 107 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 3 | 149 | next_cursor in response |
| q_api_06 | 0% | 1 | 75 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 4 | 178 | v1 and v2 can coexist; Deprecation header on sunset endpoints |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 7 | 278 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 5 | 212 | originally planned S3; ML team infrastructure already on GCP |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 76 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 6 | 245 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 20% | 6 | 239 | cold path up to 5 minutes; Prometheus + Grafana (+2 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 25% | 3 | 134 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers (+1 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 3 | 144 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 1 | 72 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 5 | 213 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### filtered

Mean recall: **26%** | Avg tokens: **121** | ≥80% recall: **1/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 2 | 127 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 7 | 288 | asymmetric — secret is never shared |
| q_api_03 | 20% | 3 | 152 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 2 | 114 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 3 | 156 | next_cursor in response |
| q_api_06 | 0% | 1 | 82 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 4 | 184 | v1 and v2 can coexist; Deprecation header on sunset endpoints |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 7 | 284 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 5 | 218 | originally planned S3; ML team infrastructure already on GCP |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 83 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 6 | 252 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 20% | 6 | 246 | cold path up to 5 minutes; Prometheus + Grafana (+2 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 25% | 3 | 141 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers (+1 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 3 | 151 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 1 | 79 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 5 | 220 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### tight

Mean recall: **26%** | Avg tokens: **124** | ≥80% recall: **1/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 2 | 132 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 7 | 292 | asymmetric — secret is never shared |
| q_api_03 | 20% | 3 | 156 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 2 | 118 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 3 | 160 | next_cursor in response |
| q_api_06 | 0% | 1 | 86 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 4 | 189 | v1 and v2 can coexist; Deprecation header on sunset endpoints |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 7 | 289 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 5 | 223 | originally planned S3; ML team infrastructure already on GCP |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 88 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 6 | 257 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 20% | 6 | 251 | cold path up to 5 minutes; Prometheus + Grafana (+2 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 25% | 3 | 146 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers (+1 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 20% | 3 | 156 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+2 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 1 | 84 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 5 | 225 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
