# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_162644  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 56 | 20 | 69.32 |
| project_auth_system | ✓ | 125 | 94 | 148.0 |
| project_data_pipeline | ✓ | 109 | 89 | 114.82 |

**Total nodes:** 290  
**Total extraction time:** 332.1s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **82%** | Avg tokens: **487** | ≥80% recall: **18/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 357 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 16 | 489 |  |
| q_api_03 | 100% | 25 | 701 |  |
| q_api_04 | 100% | 8 | 284 |  |
| q_api_05 | 33% | 2 | 98 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 100% | 11 | 399 |  |
| q_api_07 | 100% | 15 | 483 |  |
| q_api_08 | 100% | 12 | 413 |  |
| q_pipe_01 | 75% | 18 | 601 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 790 | originally planned S3 |
| q_pipe_03 | 40% | 25 | 782 | evaluated Apache Iceberg and Hudi; ML team already has Spark clusters configured for Delta Lake (+1 more) |
| q_pipe_04 | 25% | 6 | 232 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 25 | 812 |  |
| q_pipe_06 | 100% | 25 | 776 |  |
| q_pipe_07 | 100% | 5 | 180 |  |
| q_auth_01 | 100% | 22 | 649 |  |
| q_auth_02 | 83% | 25 | 711 | access tokens: 15 minutes |
| q_auth_03 | 100% | 21 | 633 |  |
| q_auth_04 | 80% | 12 | 409 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 219 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 424 |  |
| q_auth_07 | 100% | 12 | 403 |  |
| q_auth_08 | 100% | 12 | 346 |  |

### default

Mean recall: **84%** | Avg tokens: **491** | ≥80% recall: **18/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 366 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 16 | 498 |  |
| q_api_03 | 100% | 25 | 724 |  |
| q_api_04 | 67% | 7 | 262 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 107 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 100% | 11 | 407 |  |
| q_api_07 | 100% | 15 | 491 |  |
| q_api_08 | 100% | 12 | 422 |  |
| q_pipe_01 | 75% | 17 | 585 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 798 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 793 |  |
| q_pipe_04 | 25% | 6 | 240 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 25 | 820 |  |
| q_pipe_06 | 100% | 25 | 785 |  |
| q_pipe_07 | 100% | 5 | 189 |  |
| q_auth_01 | 100% | 20 | 603 |  |
| q_auth_02 | 83% | 25 | 720 | access tokens: 15 minutes |
| q_auth_03 | 100% | 21 | 642 |  |
| q_auth_04 | 80% | 12 | 417 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 227 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 433 |  |
| q_auth_07 | 100% | 12 | 411 |  |
| q_auth_08 | 100% | 12 | 355 |  |

### filtered

Mean recall: **84%** | Avg tokens: **498** | ≥80% recall: **18/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 373 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 16 | 505 |  |
| q_api_03 | 100% | 25 | 731 |  |
| q_api_04 | 67% | 7 | 268 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 114 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 100% | 11 | 414 |  |
| q_api_07 | 100% | 15 | 498 |  |
| q_api_08 | 100% | 12 | 429 |  |
| q_pipe_01 | 75% | 17 | 591 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 805 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 800 |  |
| q_pipe_04 | 25% | 6 | 247 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 25 | 827 |  |
| q_pipe_06 | 100% | 25 | 791 |  |
| q_pipe_07 | 100% | 5 | 196 |  |
| q_auth_01 | 100% | 20 | 610 |  |
| q_auth_02 | 83% | 25 | 726 | access tokens: 15 minutes |
| q_auth_03 | 100% | 21 | 648 |  |
| q_auth_04 | 80% | 12 | 424 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 234 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 439 |  |
| q_auth_07 | 100% | 12 | 418 |  |
| q_auth_08 | 100% | 12 | 362 |  |

### tight

Mean recall: **78%** | Avg tokens: **440** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 10 | 377 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 16 | 510 |  |
| q_api_03 | 100% | 18 | 557 |  |
| q_api_04 | 67% | 7 | 273 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 119 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 100% | 11 | 419 |  |
| q_api_07 | 100% | 15 | 503 |  |
| q_api_08 | 100% | 12 | 434 |  |
| q_pipe_01 | 75% | 16 | 553 | moved away from JSON |
| q_pipe_02 | 80% | 17 | 579 | originally planned S3 |
| q_pipe_03 | 60% | 18 | 585 | ML team already has Spark clusters configured for Delta Lake; switching would take 6+ weeks |
| q_pipe_04 | 25% | 6 | 252 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 67% | 17 | 592 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 17 | 568 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 5 | 201 |  |
| q_auth_01 | 100% | 18 | 556 |  |
| q_auth_02 | 83% | 19 | 560 | access tokens: 15 minutes |
| q_auth_03 | 80% | 18 | 571 | WebAuthn/FIDO2 as preferred option |
| q_auth_04 | 80% | 12 | 429 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 239 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 444 |  |
| q_auth_07 | 100% | 12 | 423 |  |
| q_auth_08 | 100% | 12 | 366 |  |
