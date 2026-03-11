# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_122425  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 70 | 21 | 58.43 |
| project_auth_system | ✓ | 123 | 47 | 73.04 |
| project_data_pipeline | ✓ | 93 | 60 | 70.59 |

**Total nodes:** 286  
**Total extraction time:** 202.1s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **48%** | Avg tokens: **399** | ≥80% recall: **7/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 777 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 715 | asymmetric — secret is never shared |
| q_api_03 | 80% | 20 | 606 | SameSite=Strict |
| q_api_04 | 0% | 1 | 62 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 83% | 7 | 242 | hard cap at 100 items |
| q_api_06 | 0% | 10 | 307 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 124 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 100% | 25 | 779 |  |
| q_pipe_01 | 50% | 16 | 511 | moved away from JSON; fastavro library (3x faster than official confluent library) |
| q_pipe_02 | 0% | 18 | 563 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 1 | 62 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 25% | 25 | 761 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 11 | 410 |  |
| q_pipe_06 | 60% | 16 | 531 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 5 | 185 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 10 | 347 | compliance requires data residency in own infrastructure |
| q_auth_02 | 17% | 14 | 411 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 100% | 10 | 343 |  |
| q_auth_04 | 0% | 4 | 179 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 3 | 118 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 4 | 153 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 100% | 20 | 640 |  |
| q_auth_08 | 100% | 12 | 350 |  |

### default

Mean recall: **48%** | Avg tokens: **404** | ≥80% recall: **7/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 786 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 724 | asymmetric — secret is never shared |
| q_api_03 | 80% | 20 | 615 | SameSite=Strict |
| q_api_04 | 0% | 1 | 71 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 83% | 7 | 250 | hard cap at 100 items |
| q_api_06 | 0% | 10 | 316 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 133 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 100% | 25 | 787 |  |
| q_pipe_01 | 50% | 15 | 488 | moved away from JSON; fastavro library (3x faster than official confluent library) |
| q_pipe_02 | 0% | 17 | 538 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 1 | 71 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 25% | 25 | 769 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 10 | 388 |  |
| q_pipe_06 | 60% | 16 | 540 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 5 | 194 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 10 | 356 | compliance requires data residency in own infrastructure |
| q_auth_02 | 17% | 14 | 420 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 100% | 10 | 352 |  |
| q_auth_04 | 0% | 4 | 188 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 3 | 127 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 4 | 162 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 100% | 20 | 649 |  |
| q_auth_08 | 100% | 12 | 359 |  |

### filtered

Mean recall: **48%** | Avg tokens: **410** | ≥80% recall: **7/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 792 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 731 | asymmetric — secret is never shared |
| q_api_03 | 80% | 20 | 621 | SameSite=Strict |
| q_api_04 | 0% | 1 | 78 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 83% | 7 | 257 | hard cap at 100 items |
| q_api_06 | 0% | 10 | 323 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 139 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 100% | 25 | 794 |  |
| q_pipe_01 | 50% | 15 | 494 | moved away from JSON; fastavro library (3x faster than official confluent library) |
| q_pipe_02 | 0% | 17 | 545 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 1 | 78 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 25% | 25 | 775 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 10 | 395 |  |
| q_pipe_06 | 60% | 16 | 547 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 5 | 201 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 10 | 362 | compliance requires data residency in own infrastructure |
| q_auth_02 | 17% | 14 | 427 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 100% | 10 | 359 |  |
| q_auth_04 | 0% | 4 | 195 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 3 | 134 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 4 | 168 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 100% | 20 | 656 |  |
| q_auth_08 | 100% | 12 | 366 |  |

### tight

Mean recall: **45%** | Avg tokens: **372** | ≥80% recall: **5/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 16 | 563 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 18 | 568 | asymmetric — secret is never shared |
| q_api_03 | 60% | 18 | 572 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 0% | 1 | 83 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 83% | 7 | 262 | hard cap at 100 items |
| q_api_06 | 0% | 10 | 328 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 20% | 3 | 144 | header versioning made caching impossible; v1 and v2 can coexist (+2 more) |
| q_api_08 | 50% | 17 | 565 | enforced at the gateway (Kong); X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset headers |
| q_pipe_01 | 50% | 15 | 499 | moved away from JSON; fastavro library (3x faster than official confluent library) |
| q_pipe_02 | 0% | 17 | 550 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 1 | 83 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 25% | 18 | 559 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 10 | 400 |  |
| q_pipe_06 | 60% | 16 | 551 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 5 | 205 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 75% | 10 | 367 | compliance requires data residency in own infrastructure |
| q_auth_02 | 17% | 14 | 432 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 100% | 10 | 364 |  |
| q_auth_04 | 0% | 4 | 199 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 3 | 139 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 4 | 173 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 100% | 17 | 574 |  |
| q_auth_08 | 100% | 12 | 370 |  |
