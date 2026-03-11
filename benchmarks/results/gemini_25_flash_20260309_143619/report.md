# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_143619  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 75 | 26 | 76.0 |
| project_auth_system | ✓ | 129 | 27 | 61.18 |
| project_data_pipeline | ✓ | 91 | 86 | 61.78 |

**Total nodes:** 295  
**Total extraction time:** 199.0s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **50%** | Avg tokens: **475** | ≥80% recall: **5/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 725 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 723 | asymmetric — secret is never shared |
| q_api_03 | 40% | 21 | 600 | HttpOnly cookies; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 11 | 320 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 8 | 266 | hard cap at 100 items |
| q_api_06 | 67% | 7 | 231 | all five fields required |
| q_api_07 | 60% | 2 | 107 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 25 | 772 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 25 | 752 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 3 | 119 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 7 | 237 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 50% | 25 | 756 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) |
| q_pipe_05 | 100% | 25 | 805 |  |
| q_pipe_06 | 60% | 25 | 784 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 75% | 25 | 774 | minimum in-sync replicas of 2 |
| q_auth_01 | 100% | 14 | 450 |  |
| q_auth_02 | 17% | 21 | 596 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 100% | 7 | 246 |  |
| q_auth_04 | 0% | 7 | 263 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 25 | 758 | IP-level rate limiting also applied |
| q_auth_06 | 0% | 1 | 62 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 50% | 8 | 283 | service accounts in Keycloak using client credentials flow; external integrations: 32-byte random keys (+1 more) |
| q_auth_08 | 75% | 11 | 285 | separate read-only account for auditors |

### default

Mean recall: **50%** | Avg tokens: **481** | ≥80% recall: **5/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 734 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 732 | asymmetric — secret is never shared |
| q_api_03 | 40% | 21 | 608 | HttpOnly cookies; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 8 | 264 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 8 | 275 | hard cap at 100 items |
| q_api_06 | 67% | 7 | 240 | all five fields required |
| q_api_07 | 60% | 2 | 115 | header versioning made caching impossible; v1 and v2 can coexist |
| q_api_08 | 0% | 25 | 780 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 25 | 767 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 3 | 128 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 7 | 245 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 50% | 25 | 767 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) |
| q_pipe_05 | 100% | 25 | 814 |  |
| q_pipe_06 | 60% | 25 | 792 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 75% | 25 | 782 | minimum in-sync replicas of 2 |
| q_auth_01 | 100% | 14 | 459 |  |
| q_auth_02 | 17% | 21 | 605 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 100% | 7 | 254 |  |
| q_auth_04 | 0% | 7 | 272 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 25 | 766 | IP-level rate limiting also applied |
| q_auth_06 | 0% | 1 | 71 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 50% | 8 | 292 | service accounts in Keycloak using client credentials flow; external integrations: 32-byte random keys (+1 more) |
| q_auth_08 | 75% | 11 | 294 | separate read-only account for auditors |
