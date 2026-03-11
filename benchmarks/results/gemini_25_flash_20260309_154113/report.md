# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_154113  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 72 | 29 | 50.91 |
| project_auth_system | ✓ | 135 | 77 | 124.59 |
| project_data_pipeline | ✓ | 89 | 69 | 78.86 |

**Total nodes:** 296  
**Total extraction time:** 254.4s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **82%** | Avg tokens: **471** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 734 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 8 | 277 |  |
| q_api_03 | 60% | 25 | 728 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 100% | 9 | 291 |  |
| q_api_05 | 83% | 7 | 247 | hard cap at 100 items |
| q_api_06 | 100% | 8 | 279 |  |
| q_api_07 | 100% | 7 | 251 |  |
| q_api_08 | 100% | 8 | 277 |  |
| q_pipe_01 | 75% | 13 | 452 | moved away from JSON |
| q_pipe_02 | 100% | 20 | 668 |  |
| q_pipe_03 | 60% | 25 | 822 | ML team already has Spark clusters configured for Delta Lake; switching would take 6+ weeks |
| q_pipe_04 | 50% | 7 | 250 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 17 | 557 |  |
| q_pipe_06 | 60% | 25 | 796 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 150 |  |
| q_auth_01 | 50% | 25 | 742 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 83% | 25 | 683 | access tokens: 15 minutes |
| q_auth_03 | 100% | 21 | 577 |  |
| q_auth_04 | 80% | 24 | 680 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 7 | 243 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 16 | 486 |  |
| q_auth_07 | 67% | 10 | 341 | service accounts in Keycloak using client credentials flow; mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 313 |  |

### default

Mean recall: **81%** | Avg tokens: **477** | ≥80% recall: **14/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 743 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 8 | 286 |  |
| q_api_03 | 60% | 25 | 737 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 67% | 8 | 267 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 256 | hard cap at 100 items |
| q_api_06 | 100% | 8 | 288 |  |
| q_api_07 | 100% | 7 | 260 |  |
| q_api_08 | 100% | 8 | 285 |  |
| q_pipe_01 | 75% | 13 | 461 | moved away from JSON |
| q_pipe_02 | 80% | 19 | 649 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 829 |  |
| q_pipe_04 | 50% | 7 | 258 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 17 | 566 |  |
| q_pipe_06 | 60% | 25 | 804 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 159 |  |
| q_auth_01 | 50% | 25 | 758 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 83% | 25 | 692 | access tokens: 15 minutes |
| q_auth_03 | 100% | 21 | 586 |  |
| q_auth_04 | 60% | 23 | 662 | ABAC (Attribute-Based Access Control) — not just RBAC; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 7 | 252 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 16 | 495 |  |
| q_auth_07 | 67% | 10 | 350 | service accounts in Keycloak using client credentials flow; mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 322 |  |

### filtered

Mean recall: **81%** | Avg tokens: **484** | ≥80% recall: **14/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 750 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 8 | 293 |  |
| q_api_03 | 60% | 25 | 743 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 67% | 8 | 273 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 263 | hard cap at 100 items |
| q_api_06 | 100% | 8 | 295 |  |
| q_api_07 | 100% | 7 | 267 |  |
| q_api_08 | 100% | 8 | 292 |  |
| q_pipe_01 | 75% | 13 | 468 | moved away from JSON |
| q_pipe_02 | 80% | 19 | 656 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 836 |  |
| q_pipe_04 | 50% | 7 | 265 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 17 | 573 |  |
| q_pipe_06 | 60% | 25 | 811 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 166 |  |
| q_auth_01 | 50% | 25 | 765 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 83% | 25 | 698 | access tokens: 15 minutes |
| q_auth_03 | 100% | 21 | 593 |  |
| q_auth_04 | 60% | 23 | 669 | ABAC (Attribute-Based Access Control) — not just RBAC; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 7 | 259 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 16 | 502 |  |
| q_auth_07 | 67% | 10 | 356 | service accounts in Keycloak using client credentials flow; mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 328 |  |

### tight

Mean recall: **77%** | Avg tokens: **423** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 18 | 558 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 8 | 297 |  |
| q_api_03 | 60% | 19 | 569 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 67% | 8 | 278 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 268 | hard cap at 100 items |
| q_api_06 | 100% | 8 | 299 |  |
| q_api_07 | 100% | 7 | 271 |  |
| q_api_08 | 100% | 8 | 297 |  |
| q_pipe_01 | 75% | 13 | 472 | moved away from JSON |
| q_pipe_02 | 80% | 16 | 560 | originally planned S3 |
| q_pipe_03 | 60% | 17 | 590 | ML team already has Spark clusters configured for Delta Lake; switching would take 6+ weeks |
| q_pipe_04 | 50% | 7 | 270 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 17 | 578 |  |
| q_pipe_06 | 60% | 17 | 560 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 171 |  |
| q_auth_01 | 25% | 17 | 553 | compliance requires data residency in own infrastructure; non-starter for enterprise customers (+1 more) |
| q_auth_02 | 83% | 19 | 549 | access tokens: 15 minutes |
| q_auth_03 | 100% | 19 | 551 |  |
| q_auth_04 | 40% | 19 | 567 | ABAC (Attribute-Based Access Control) — not just RBAC; originally considered simple role-based system (+1 more) |
| q_auth_05 | 80% | 7 | 263 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 16 | 507 |  |
| q_auth_07 | 67% | 10 | 361 | service accounts in Keycloak using client credentials flow; mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 333 |  |
