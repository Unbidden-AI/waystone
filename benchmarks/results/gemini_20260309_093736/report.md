# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini.yaml`  
**Run:** 20260309_093736  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 58 | 41 | 53.75 |
| project_auth_system | ✓ | 100 | 73 | 99.0 |
| project_data_pipeline | ✓ | 86 | 77 | 85.93 |

**Total nodes:** 244  
**Total extraction time:** 238.7s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **49%** | Avg tokens: **256** | ≥80% recall: **9/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 298 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 299 | asymmetric — secret is never shared |
| q_api_03 | 80% | 10 | 335 | not localStorage due to XSS risk |
| q_api_04 | 100% | 10 | 338 |  |
| q_api_05 | 100% | 6 | 210 |  |
| q_api_06 | 100% | 10 | 355 |  |
| q_api_07 | 100% | 9 | 302 |  |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 330 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 306 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 74 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 10 | 375 |  |
| q_pipe_06 | 80% | 10 | 356 | PagerDuty alerts for 500ms SLA breach |
| q_pipe_07 | 0% | 10 | 337 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 10 | 343 |  |
| q_auth_02 | 17% | 10 | 320 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 337 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 1 | 63 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 6 | 211 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 3 | 154 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 33% | 10 | 330 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 6 | 211 |  |

### default

Mean recall: **48%** | Avg tokens: **262** | ≥80% recall: **8/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 307 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 308 | asymmetric — secret is never shared |
| q_api_03 | 80% | 10 | 344 | not localStorage due to XSS risk |
| q_api_04 | 67% | 9 | 315 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 6 | 219 |  |
| q_api_06 | 100% | 10 | 363 |  |
| q_api_07 | 100% | 9 | 311 |  |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 338 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 314 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 82 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 10 | 389 |  |
| q_pipe_06 | 80% | 10 | 365 | PagerDuty alerts for 500ms SLA breach |
| q_pipe_07 | 0% | 10 | 346 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 10 | 346 |  |
| q_auth_02 | 17% | 10 | 329 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 346 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 1 | 72 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 6 | 220 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 3 | 162 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 33% | 10 | 339 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 6 | 220 |  |

### filtered

Mean recall: **48%** | Avg tokens: **269** | ≥80% recall: **8/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 314 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 315 | asymmetric — secret is never shared |
| q_api_03 | 80% | 10 | 351 | not localStorage due to XSS risk |
| q_api_04 | 67% | 9 | 322 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 6 | 226 |  |
| q_api_06 | 100% | 10 | 370 |  |
| q_api_07 | 100% | 9 | 318 |  |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 345 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 321 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 89 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 10 | 395 |  |
| q_pipe_06 | 80% | 10 | 371 | PagerDuty alerts for 500ms SLA breach |
| q_pipe_07 | 0% | 10 | 352 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 10 | 352 |  |
| q_auth_02 | 17% | 10 | 336 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 353 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 1 | 78 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 6 | 227 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 3 | 169 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 33% | 10 | 346 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 6 | 227 |  |

### tight

Mean recall: **48%** | Avg tokens: **273** | ≥80% recall: **8/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 319 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 319 | asymmetric — secret is never shared |
| q_api_03 | 80% | 10 | 356 | not localStorage due to XSS risk |
| q_api_04 | 67% | 9 | 326 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 6 | 231 |  |
| q_api_06 | 100% | 10 | 375 |  |
| q_api_07 | 100% | 9 | 323 |  |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 350 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 326 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 1 | 94 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 10 | 400 |  |
| q_pipe_06 | 80% | 10 | 376 | PagerDuty alerts for 500ms SLA breach |
| q_pipe_07 | 0% | 10 | 357 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 10 | 357 |  |
| q_auth_02 | 17% | 10 | 340 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 357 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 1 | 83 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 6 | 231 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 3 | 174 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 33% | 10 | 350 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 6 | 231 |  |
