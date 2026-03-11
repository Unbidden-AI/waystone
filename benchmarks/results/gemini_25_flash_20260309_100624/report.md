# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Run:** 20260309_100624  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 77 | 29 | 43.48 |
| project_auth_system | ✓ | 133 | 104 | 104.16 |
| project_data_pipeline | ✓ | 109 | 83 | 114.02 |

**Total nodes:** 319  
**Total extraction time:** 261.7s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **42%** | Avg tokens: **283** | ≥80% recall: **2/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 301 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 301 | asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 292 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 3 | 130 | guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 100% | 10 | 327 |  |
| q_api_06 | 67% | 10 | 323 | RFC 7807 Problem Details |
| q_api_07 | 60% | 10 | 368 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 308 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 323 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 10 | 315 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 75% | 7 | 240 | PII scrubbing happens in the Kafka consumer before any downstream topic |
| q_pipe_05 | 0% | 10 | 328 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 40% | 10 | 379 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 100% | 8 | 252 |  |
| q_auth_01 | 75% | 10 | 334 | non-starter for enterprise customers |
| q_auth_02 | 17% | 10 | 295 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 347 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 10 | 357 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 4 | 168 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 5 | 203 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 50% | 10 | 349 | API keys are not the preferred pattern (too easy to leak); external integrations: 32-byte random keys (+1 more) |
| q_auth_08 | 50% | 10 | 261 | AWS CloudTrail for audit trail; separate read-only account for auditors |

### default

Mean recall: **41%** | Avg tokens: **290** | ≥80% recall: **2/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 310 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 310 | asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 301 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 2 | 107 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 100% | 10 | 335 |  |
| q_api_06 | 67% | 10 | 332 | RFC 7807 Problem Details |
| q_api_07 | 60% | 10 | 377 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 317 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 332 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 10 | 323 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 75% | 7 | 249 | PII scrubbing happens in the Kafka consumer before any downstream topic |
| q_pipe_05 | 0% | 10 | 337 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 40% | 10 | 388 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 100% | 8 | 261 |  |
| q_auth_01 | 75% | 10 | 343 | non-starter for enterprise customers |
| q_auth_02 | 17% | 10 | 303 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 355 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 10 | 366 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 4 | 177 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 5 | 211 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 50% | 10 | 358 | API keys are not the preferred pattern (too easy to leak); external integrations: 32-byte random keys (+1 more) |
| q_auth_08 | 50% | 10 | 270 | AWS CloudTrail for audit trail; separate read-only account for auditors |

### filtered

Mean recall: **41%** | Avg tokens: **294** | ≥80% recall: **2/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 317 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 316 | asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 308 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 2 | 114 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 100% | 10 | 342 |  |
| q_api_06 | 67% | 10 | 338 | RFC 7807 Problem Details |
| q_api_07 | 60% | 10 | 383 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 323 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 339 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 10 | 330 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 75% | 6 | 224 | PII scrubbing happens in the Kafka consumer before any downstream topic |
| q_pipe_05 | 0% | 10 | 344 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 40% | 10 | 395 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 100% | 7 | 240 |  |
| q_auth_01 | 75% | 10 | 350 | non-starter for enterprise customers |
| q_auth_02 | 17% | 10 | 310 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 362 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 10 | 372 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 4 | 184 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 5 | 218 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 50% | 10 | 365 | API keys are not the preferred pattern (too easy to leak); external integrations: 32-byte random keys (+1 more) |
| q_auth_08 | 50% | 10 | 277 | AWS CloudTrail for audit trail; separate read-only account for auditors |

### tight

Mean recall: **41%** | Avg tokens: **298** | ≥80% recall: **2/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 321 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 10 | 321 | asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 313 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 2 | 118 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 100% | 10 | 347 |  |
| q_api_06 | 67% | 10 | 343 | RFC 7807 Problem Details |
| q_api_07 | 60% | 10 | 388 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 328 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 343 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 10 | 335 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 75% | 6 | 229 | PII scrubbing happens in the Kafka consumer before any downstream topic |
| q_pipe_05 | 0% | 10 | 349 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 40% | 10 | 400 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 100% | 7 | 244 |  |
| q_auth_01 | 75% | 10 | 355 | non-starter for enterprise customers |
| q_auth_02 | 17% | 10 | 315 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 60% | 10 | 367 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 0% | 10 | 377 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 4 | 188 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 5 | 223 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 50% | 10 | 369 | API keys are not the preferred pattern (too easy to leak); external integrations: 32-byte random keys (+1 more) |
| q_auth_08 | 50% | 10 | 281 | AWS CloudTrail for audit trail; separate read-only account for auditors |
