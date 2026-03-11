# Benchmark Report

**Model:** `models/gemini-2.5-pro`  
**Config:** `gemini_25_pro.yaml`  
**Run:** 20260309_100625  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 59 | 26 | 104.76 |
| project_auth_system | ✓ | 81 | 45 | 108.03 |
| project_data_pipeline | ✓ | 57 | 52 | 98.62 |

**Total nodes:** 197  
**Total extraction time:** 311.4s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **36%** | Avg tokens: **311** | ≥80% recall: **3/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 402 | JWT tokens; stateless (+2 more) |
| q_api_02 | 50% | 10 | 376 | public keys distributed to other services; asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 370 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 67% | 7 | 257 | offset pagination breaks when rows are inserted; hard cap at 100 items |
| q_api_06 | 33% | 10 | 403 | RFC 7807 Problem Details; all five fields required |
| q_api_07 | 20% | 10 | 383 | URL versioning (not header versioning); header versioning made caching impossible (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 376 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 369 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 60% | 10 | 393 | switching would take 6+ weeks; ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 5 | 212 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 10 | 385 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 40% | 10 | 381 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 25% | 10 | 367 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 50% | 10 | 385 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 67% | 10 | 369 | opaque tokens can be instantly revoked; stored in database |
| q_auth_03 | 80% | 8 | 311 | SIM swapping makes SMS too weak |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 60% | 10 | 378 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 381 |  |
| q_auth_07 | 33% | 10 | 388 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 7 | 276 |  |

### default

Mean recall: **38%** | Avg tokens: **319** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 411 | JWT tokens; stateless (+2 more) |
| q_api_02 | 50% | 10 | 385 | public keys distributed to other services; asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 379 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 67% | 7 | 266 | offset pagination breaks when rows are inserted; hard cap at 100 items |
| q_api_06 | 33% | 10 | 411 | RFC 7807 Problem Details; all five fields required |
| q_api_07 | 20% | 10 | 392 | URL versioning (not header versioning); header versioning made caching impossible (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 401 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 378 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 60% | 10 | 402 | switching would take 6+ weeks; ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 5 | 220 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 10 | 394 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 10 | 397 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 10 | 375 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 50% | 9 | 377 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 67% | 10 | 377 | opaque tokens can be instantly revoked; stored in database |
| q_auth_03 | 80% | 8 | 320 | SIM swapping makes SMS too weak |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 10 | 385 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 390 |  |
| q_auth_07 | 33% | 10 | 397 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 7 | 285 |  |

### filtered

Mean recall: **38%** | Avg tokens: **325** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 418 | JWT tokens; stateless (+2 more) |
| q_api_02 | 50% | 10 | 392 | public keys distributed to other services; asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 386 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 67% | 7 | 273 | offset pagination breaks when rows are inserted; hard cap at 100 items |
| q_api_06 | 33% | 10 | 418 | RFC 7807 Problem Details; all five fields required |
| q_api_07 | 20% | 10 | 399 | URL versioning (not header versioning); header versioning made caching impossible (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 408 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 385 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 60% | 10 | 408 | switching would take 6+ weeks; ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 5 | 227 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 10 | 400 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 10 | 404 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 10 | 382 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 50% | 9 | 384 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 67% | 10 | 384 | opaque tokens can be instantly revoked; stored in database |
| q_auth_03 | 80% | 8 | 327 | SIM swapping makes SMS too weak |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 10 | 392 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 397 |  |
| q_auth_07 | 33% | 10 | 404 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 7 | 292 |  |

### tight

Mean recall: **38%** | Avg tokens: **329** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 423 | JWT tokens; stateless (+2 more) |
| q_api_02 | 50% | 10 | 397 | public keys distributed to other services; asymmetric — secret is never shared |
| q_api_03 | 20% | 10 | 391 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 67% | 7 | 278 | offset pagination breaks when rows are inserted; hard cap at 100 items |
| q_api_06 | 33% | 10 | 423 | RFC 7807 Problem Details; all five fields required |
| q_api_07 | 20% | 10 | 404 | URL versioning (not header versioning); header versioning made caching impossible (+2 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 10 | 413 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 10 | 389 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 60% | 10 | 413 | switching would take 6+ weeks; ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 5 | 232 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 10 | 405 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 10 | 409 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 10 | 387 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 50% | 9 | 388 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 67% | 10 | 389 | opaque tokens can be instantly revoked; stored in database |
| q_auth_03 | 80% | 8 | 332 | SIM swapping makes SMS too weak |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 10 | 397 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 401 |  |
| q_auth_07 | 33% | 10 | 409 | API keys are not the preferred pattern (too easy to leak); mTLS between internal services where possible (+2 more) |
| q_auth_08 | 100% | 7 | 297 |  |
