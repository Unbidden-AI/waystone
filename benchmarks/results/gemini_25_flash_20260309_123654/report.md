# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_123654  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 71 | 19 | 55.27 |
| project_auth_system | ✓ | 124 | 84 | 70.18 |
| project_data_pipeline | ✓ | 89 | 92 | 117.0 |

**Total nodes:** 284  
**Total extraction time:** 242.5s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **66%** | Avg tokens: **515** | ≥80% recall: **11/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 25 | 771 | scales horizontally |
| q_api_02 | 100% | 25 | 764 |  |
| q_api_03 | 20% | 2 | 91 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 100% | 6 | 241 |  |
| q_api_05 | 83% | 8 | 287 | hard cap at 100 items |
| q_api_06 | 100% | 9 | 354 |  |
| q_api_07 | 60% | 16 | 495 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 0% | 25 | 762 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 25 | 778 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 0% | 14 | 458 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 2 | 96 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 100% | 25 | 777 |  |
| q_pipe_05 | 100% | 25 | 815 |  |
| q_pipe_06 | 60% | 25 | 793 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 75% | 25 | 764 | GCS cross-region replication for cold storage |
| q_auth_01 | 100% | 22 | 675 |  |
| q_auth_02 | 83% | 25 | 757 | stored in database |
| q_auth_03 | 100% | 9 | 290 |  |
| q_auth_04 | 0% | 8 | 303 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 3 | 127 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 67% | 13 | 398 | top 10,000 common passwords blocked; rotation enforced only if breach detected via HIBP |
| q_auth_07 | 100% | 23 | 714 |  |
| q_auth_08 | 100% | 12 | 327 |  |

### default

Mean recall: **63%** | Avg tokens: **520** | ≥80% recall: **10/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 25 | 779 | scales horizontally |
| q_api_02 | 100% | 25 | 779 |  |
| q_api_03 | 20% | 2 | 100 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 4 | 183 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 8 | 296 | hard cap at 100 items |
| q_api_06 | 100% | 9 | 363 |  |
| q_api_07 | 60% | 16 | 504 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 0% | 25 | 771 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 25 | 787 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 0% | 14 | 467 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 2 | 105 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 100% | 25 | 785 |  |
| q_pipe_05 | 100% | 25 | 824 |  |
| q_pipe_06 | 60% | 25 | 802 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 75% | 25 | 773 | GCS cross-region replication for cold storage |
| q_auth_01 | 100% | 22 | 684 |  |
| q_auth_02 | 83% | 25 | 772 | stored in database |
| q_auth_03 | 100% | 9 | 299 |  |
| q_auth_04 | 0% | 7 | 278 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 3 | 136 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 67% | 13 | 406 | top 10,000 common passwords blocked; rotation enforced only if breach detected via HIBP |
| q_auth_07 | 100% | 23 | 722 |  |
| q_auth_08 | 100% | 12 | 335 |  |

### filtered

Mean recall: **63%** | Avg tokens: **515** | ≥80% recall: **10/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 25 | 786 | scales horizontally |
| q_api_02 | 100% | 25 | 786 |  |
| q_api_03 | 20% | 2 | 107 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 3 | 161 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 6 | 242 | hard cap at 100 items |
| q_api_06 | 100% | 7 | 314 |  |
| q_api_07 | 60% | 14 | 450 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 0% | 25 | 778 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 25 | 794 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 0% | 14 | 474 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 1 | 83 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 100% | 25 | 792 |  |
| q_pipe_05 | 100% | 25 | 831 |  |
| q_pipe_06 | 60% | 25 | 809 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 75% | 25 | 779 | GCS cross-region replication for cold storage |
| q_auth_01 | 100% | 22 | 691 |  |
| q_auth_02 | 83% | 25 | 779 | stored in database |
| q_auth_03 | 100% | 9 | 306 |  |
| q_auth_04 | 0% | 7 | 285 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 2 | 107 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 67% | 13 | 413 | top 10,000 common passwords blocked; rotation enforced only if breach detected via HIBP |
| q_auth_07 | 100% | 23 | 729 |  |
| q_auth_08 | 100% | 12 | 342 |  |

### tight

Mean recall: **55%** | Avg tokens: **418** | ≥80% recall: **6/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 17 | 572 | stateless; scales horizontally |
| q_api_02 | 75% | 17 | 576 | asymmetric — secret is never shared |
| q_api_03 | 20% | 2 | 111 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 33% | 3 | 166 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 6 | 246 | hard cap at 100 items |
| q_api_06 | 100% | 7 | 318 |  |
| q_api_07 | 60% | 14 | 454 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 0% | 18 | 572 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 50% | 18 | 568 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 0% | 14 | 479 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 1 | 88 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 50% | 17 | 572 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) |
| q_pipe_05 | 100% | 17 | 579 |  |
| q_pipe_06 | 60% | 17 | 575 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 50% | 17 | 579 | minimum in-sync replicas of 2; GCS cross-region replication for cold storage |
| q_auth_01 | 100% | 17 | 556 |  |
| q_auth_02 | 67% | 18 | 571 | opaque tokens can be instantly revoked; stored in database |
| q_auth_03 | 100% | 9 | 311 |  |
| q_auth_04 | 0% | 7 | 290 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 2 | 112 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 67% | 13 | 418 | top 10,000 common passwords blocked; rotation enforced only if breach detected via HIBP |
| q_auth_07 | 50% | 17 | 550 | API keys are not the preferred pattern (too easy to leak); external integrations: 32-byte random keys (+1 more) |
| q_auth_08 | 100% | 12 | 347 |  |
