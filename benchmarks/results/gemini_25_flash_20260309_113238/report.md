# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_113238  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 58 | 22 | 49.4 |
| project_auth_system | ✓ | 120 | 99 | 114.62 |
| project_data_pipeline | ✓ | 90 | 75 | 65.69 |

**Total nodes:** 268  
**Total extraction time:** 229.7s  

## Retrieval Quality by Strategy

### default

Mean recall: **60%** | Avg tokens: **566** | ≥80% recall: **10/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 779 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 713 | asymmetric — secret is never shared |
| q_api_03 | 20% | 25 | 733 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 3 | 155 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 6 | 224 | hard cap at 100 items |
| q_api_06 | 100% | 7 | 291 |  |
| q_api_07 | 80% | 9 | 326 | Deprecation header on sunset endpoints |
| q_api_08 | 100% | 25 | 821 |  |
| q_pipe_01 | 50% | 25 | 798 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 0% | 25 | 732 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 20% | 25 | 795 | Delta Lake; evaluated Apache Iceberg and Hudi (+2 more) |
| q_pipe_04 | 50% | 25 | 807 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) |
| q_pipe_05 | 100% | 25 | 797 |  |
| q_pipe_06 | 60% | 15 | 515 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 0% | 11 | 393 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 23 | 706 |  |
| q_auth_02 | 67% | 19 | 549 | opaque tokens can be instantly revoked; stored in database |
| q_auth_03 | 100% | 10 | 351 |  |
| q_auth_04 | 0% | 8 | 282 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 80% | 25 | 796 | IP-level rate limiting also applied |
| q_auth_06 | 0% | 10 | 322 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 83% | 25 | 777 | prefixed with service identifier (e.g., cb_live_) |
| q_auth_08 | 100% | 12 | 364 |  |
