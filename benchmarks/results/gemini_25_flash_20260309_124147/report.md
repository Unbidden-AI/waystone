# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_124147  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 75 | 33 | 78.85 |
| project_auth_system | ✓ | 122 | 105 | 72.07 |
| project_data_pipeline | ✓ | 104 | 32 | 72.13 |

**Total nodes:** 301  
**Total extraction time:** 223.0s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **57%** | Avg tokens: **430** | ≥80% recall: **10/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 759 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 4 | 154 | asymmetric — secret is never shared |
| q_api_03 | 20% | 25 | 746 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 100% | 9 | 282 |  |
| q_api_05 | 83% | 8 | 271 | hard cap at 100 items |
| q_api_06 | 100% | 25 | 720 |  |
| q_api_07 | 60% | 13 | 418 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 100% | 25 | 775 |  |
| q_pipe_01 | 0% | 13 | 420 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 16 | 503 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 80% | 4 | 164 | ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 1 | 66 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 6 | 244 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state |
| q_pipe_06 | 60% | 5 | 205 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 2 | 96 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 24 | 731 |  |
| q_auth_02 | 67% | 25 | 741 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 10 | 336 |  |
| q_auth_04 | 0% | 5 | 204 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 25 | 753 | IP-level rate limiting also applied |
| q_auth_06 | 0% | 6 | 195 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 83% | 25 | 763 | prefixed with service identifier (e.g., cb_live_) |
| q_auth_08 | 100% | 12 | 354 |  |

### default

Mean recall: **56%** | Avg tokens: **436** | ≥80% recall: **9/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 768 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 4 | 162 | asymmetric — secret is never shared |
| q_api_03 | 20% | 25 | 755 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 8 | 259 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 8 | 280 | hard cap at 100 items |
| q_api_06 | 100% | 25 | 728 |  |
| q_api_07 | 60% | 13 | 427 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 100% | 25 | 783 |  |
| q_pipe_01 | 0% | 13 | 428 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 16 | 512 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 80% | 4 | 172 | ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 1 | 74 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 6 | 253 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state |
| q_pipe_06 | 60% | 5 | 214 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 2 | 105 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 22 | 687 |  |
| q_auth_02 | 67% | 25 | 750 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 10 | 345 |  |
| q_auth_04 | 0% | 5 | 213 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 25 | 766 | IP-level rate limiting also applied |
| q_auth_06 | 0% | 6 | 204 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 83% | 25 | 772 | prefixed with service identifier (e.g., cb_live_) |
| q_auth_08 | 100% | 12 | 363 |  |

### filtered

Mean recall: **56%** | Avg tokens: **433** | ≥80% recall: **9/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 775 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 4 | 169 | asymmetric — secret is never shared |
| q_api_03 | 20% | 25 | 762 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 8 | 266 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 8 | 287 | hard cap at 100 items |
| q_api_06 | 100% | 25 | 735 |  |
| q_api_07 | 60% | 13 | 433 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 100% | 25 | 790 |  |
| q_pipe_01 | 0% | 12 | 409 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 16 | 519 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 80% | 3 | 149 | ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 0 | 6 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 5 | 218 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state |
| q_pipe_06 | 60% | 4 | 185 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 2 | 111 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 22 | 694 |  |
| q_auth_02 | 67% | 25 | 756 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 10 | 352 |  |
| q_auth_04 | 0% | 5 | 220 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 25 | 773 | IP-level rate limiting also applied |
| q_auth_06 | 0% | 6 | 210 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 83% | 25 | 779 | prefixed with service identifier (e.g., cb_live_) |
| q_auth_08 | 100% | 12 | 369 |  |

### tight

Mean recall: **55%** | Avg tokens: **366** | ≥80% recall: **9/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 18 | 564 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 4 | 174 | asymmetric — secret is never shared |
| q_api_03 | 20% | 18 | 568 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 8 | 271 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 8 | 291 | hard cap at 100 items |
| q_api_06 | 100% | 19 | 558 |  |
| q_api_07 | 60% | 13 | 438 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 100% | 16 | 540 |  |
| q_pipe_01 | 0% | 12 | 414 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 16 | 524 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 80% | 3 | 154 | ACID transactions and schema enforcement |
| q_pipe_04 | 0% | 0 | 6 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 33% | 5 | 223 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state |
| q_pipe_06 | 60% | 4 | 190 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 25% | 2 | 116 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 17 | 547 |  |
| q_auth_02 | 50% | 18 | 553 | access tokens: 15 minutes; opaque tokens can be instantly revoked (+1 more) |
| q_auth_03 | 100% | 10 | 357 |  |
| q_auth_04 | 0% | 5 | 224 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 17 | 552 | IP-level rate limiting also applied |
| q_auth_06 | 0% | 6 | 215 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 83% | 17 | 558 | prefixed with service identifier (e.g., cb_live_) |
| q_auth_08 | 100% | 12 | 374 |  |
