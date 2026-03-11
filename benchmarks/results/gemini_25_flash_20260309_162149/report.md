# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_162149  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 58 | 26 | 43.56 |
| project_auth_system | ✓ | 131 | 20 | 74.2 |
| project_data_pipeline | ✓ | 86 | 74 | 51.17 |

**Total nodes:** 275  
**Total extraction time:** 168.9s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **78%** | Avg tokens: **371** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 5 | 178 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 6 | 224 | asymmetric — secret is never shared |
| q_api_03 | 60% | 25 | 723 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 100% | 9 | 316 |  |
| q_api_05 | 17% | 2 | 93 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 7 | 277 |  |
| q_api_07 | 100% | 7 | 242 |  |
| q_api_08 | 100% | 10 | 345 |  |
| q_pipe_01 | 75% | 15 | 491 | moved away from JSON |
| q_pipe_02 | 100% | 16 | 498 |  |
| q_pipe_03 | 100% | 25 | 776 |  |
| q_pipe_04 | 50% | 7 | 262 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 67% | 8 | 288 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 25 | 738 |  |
| q_pipe_07 | 100% | 4 | 142 |  |
| q_auth_01 | 100% | 25 | 741 |  |
| q_auth_02 | 83% | 17 | 482 | access tokens: 15 minutes |
| q_auth_03 | 100% | 8 | 261 |  |
| q_auth_04 | 60% | 9 | 310 | Open Policy Agent (OPA) as policy engine; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 60% | 5 | 176 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 13 | 401 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 8 | 285 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 285 | separate read-only account for auditors |

### default

Mean recall: **75%** | Avg tokens: **374** | ≥80% recall: **12/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 5 | 187 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 6 | 233 | asymmetric — secret is never shared |
| q_api_03 | 60% | 25 | 731 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 67% | 8 | 293 | originally three roles: admin, member, viewer |
| q_api_05 | 17% | 2 | 102 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 7 | 286 |  |
| q_api_07 | 100% | 7 | 250 |  |
| q_api_08 | 100% | 10 | 354 |  |
| q_pipe_01 | 75% | 14 | 477 | moved away from JSON |
| q_pipe_02 | 80% | 15 | 481 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 785 |  |
| q_pipe_04 | 50% | 7 | 271 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 67% | 7 | 265 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 25 | 747 |  |
| q_pipe_07 | 100% | 4 | 151 |  |
| q_auth_01 | 100% | 25 | 750 |  |
| q_auth_02 | 83% | 17 | 490 | access tokens: 15 minutes |
| q_auth_03 | 100% | 8 | 270 |  |
| q_auth_04 | 40% | 8 | 288 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+1 more) |
| q_auth_05 | 60% | 5 | 185 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 13 | 410 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 8 | 294 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 294 | separate read-only account for auditors |

### filtered

Mean recall: **75%** | Avg tokens: **380** | ≥80% recall: **12/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 5 | 193 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 6 | 240 | asymmetric — secret is never shared |
| q_api_03 | 60% | 25 | 738 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 67% | 8 | 300 | originally three roles: admin, member, viewer |
| q_api_05 | 17% | 2 | 109 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 7 | 293 |  |
| q_api_07 | 100% | 7 | 257 |  |
| q_api_08 | 100% | 10 | 361 |  |
| q_pipe_01 | 75% | 14 | 484 | moved away from JSON |
| q_pipe_02 | 80% | 15 | 487 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 792 |  |
| q_pipe_04 | 50% | 7 | 278 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 67% | 7 | 272 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 25 | 754 |  |
| q_pipe_07 | 100% | 4 | 158 |  |
| q_auth_01 | 100% | 25 | 757 |  |
| q_auth_02 | 83% | 17 | 497 | access tokens: 15 minutes |
| q_auth_03 | 100% | 8 | 276 |  |
| q_auth_04 | 40% | 8 | 294 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+1 more) |
| q_auth_05 | 60% | 5 | 192 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 13 | 416 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 8 | 300 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 301 | separate read-only account for auditors |

### tight

Mean recall: **73%** | Avg tokens: **349** | ≥80% recall: **11/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 5 | 198 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 6 | 244 | asymmetric — secret is never shared |
| q_api_03 | 60% | 19 | 553 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 67% | 8 | 304 | originally three roles: admin, member, viewer |
| q_api_05 | 17% | 2 | 114 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 7 | 297 |  |
| q_api_07 | 100% | 7 | 262 |  |
| q_api_08 | 100% | 10 | 366 |  |
| q_pipe_01 | 75% | 14 | 489 | moved away from JSON |
| q_pipe_02 | 80% | 15 | 492 | originally planned S3 |
| q_pipe_03 | 100% | 17 | 562 |  |
| q_pipe_04 | 50% | 7 | 282 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 67% | 7 | 276 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 18 | 567 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 162 |  |
| q_auth_01 | 100% | 18 | 558 |  |
| q_auth_02 | 83% | 17 | 502 | access tokens: 15 minutes |
| q_auth_03 | 100% | 8 | 281 |  |
| q_auth_04 | 40% | 8 | 299 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+1 more) |
| q_auth_05 | 60% | 5 | 197 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 13 | 421 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 8 | 305 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 305 | separate read-only account for auditors |
