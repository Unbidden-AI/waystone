# Benchmark Report

**Model:** `qwen/qwen3.5-35b-a3b`  
**Config:** `qwen_nothinking.yaml`  
**Run:** 20260309_090437  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 30 | 21 | 126.81 |
| project_auth_system | ✓ | 28 | 10 | 118.4 |
| project_data_pipeline | ✓ | 39 | 22 | 163.72 |

**Total nodes:** 97  
**Total extraction time:** 408.9s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **38%** | Avg tokens: **197** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 9 | 285 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 10 | 364 | asymmetric — secret is never shared |
| q_api_03 | 80% | 6 | 212 | not localStorage due to XSS risk |
| q_api_04 | 67% | 3 | 124 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 4 | 151 | offset pagination breaks when rows are inserted |
| q_api_06 | 0% | 7 | 250 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 6 | 217 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 335 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 40% | 10 | 334 | originally planned S3; ML team infrastructure already on GCP (+1 more) |
| q_pipe_03 | 0% | 3 | 143 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 5 | 197 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 10 | 371 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 10 | 336 |  |
| q_pipe_07 | 100% | 3 | 124 |  |
| q_auth_01 | 50% | 5 | 225 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers |
| q_auth_02 | 33% | 9 | 307 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+2 more) |
| q_auth_03 | 40% | 3 | 150 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+1 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 60% | 1 | 74 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 335 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **38%** | Avg tokens: **202** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 8 | 267 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 10 | 373 | asymmetric — secret is never shared |
| q_api_03 | 80% | 6 | 221 | not localStorage due to XSS risk |
| q_api_04 | 67% | 2 | 106 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 4 | 160 | offset pagination breaks when rows are inserted |
| q_api_06 | 0% | 7 | 258 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 6 | 226 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 344 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 40% | 10 | 343 | originally planned S3; ML team infrastructure already on GCP (+1 more) |
| q_pipe_03 | 0% | 3 | 152 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 5 | 206 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 10 | 380 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 10 | 345 |  |
| q_pipe_07 | 100% | 3 | 133 |  |
| q_auth_01 | 50% | 5 | 233 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers |
| q_auth_02 | 33% | 9 | 316 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+2 more) |
| q_auth_03 | 40% | 3 | 158 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+1 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 60% | 1 | 83 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 344 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### filtered

Mean recall: **38%** | Avg tokens: **207** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 8 | 274 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 10 | 379 | asymmetric — secret is never shared |
| q_api_03 | 80% | 6 | 227 | not localStorage due to XSS risk |
| q_api_04 | 67% | 2 | 113 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 4 | 167 | offset pagination breaks when rows are inserted |
| q_api_06 | 0% | 7 | 265 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 6 | 232 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 350 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 40% | 10 | 349 | originally planned S3; ML team infrastructure already on GCP (+1 more) |
| q_pipe_03 | 0% | 3 | 158 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 5 | 212 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 10 | 386 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 10 | 351 |  |
| q_pipe_07 | 100% | 3 | 140 |  |
| q_auth_01 | 50% | 5 | 240 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers |
| q_auth_02 | 33% | 9 | 323 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+2 more) |
| q_auth_03 | 40% | 3 | 165 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+1 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 60% | 1 | 89 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 351 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### tight

Mean recall: **38%** | Avg tokens: **212** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 8 | 279 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 10 | 384 | asymmetric — secret is never shared |
| q_api_03 | 80% | 6 | 232 | not localStorage due to XSS risk |
| q_api_04 | 67% | 2 | 118 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 4 | 172 | offset pagination breaks when rows are inserted |
| q_api_06 | 0% | 7 | 270 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 60% | 6 | 237 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 355 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 40% | 10 | 354 | originally planned S3; ML team infrastructure already on GCP (+1 more) |
| q_pipe_03 | 0% | 3 | 163 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 5 | 217 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 10 | 391 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 10 | 356 |  |
| q_pipe_07 | 100% | 3 | 144 |  |
| q_auth_01 | 50% | 5 | 245 | Auth0 and Okta store user data outside company control; non-starter for enterprise customers |
| q_auth_02 | 33% | 9 | 328 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+2 more) |
| q_auth_03 | 40% | 3 | 170 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+1 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 60% | 1 | 94 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 356 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
