# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_164015  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 76 | 58 | 56.03 |
| project_auth_system | ✓ | 127 | 39 | 64.76 |
| project_data_pipeline | ✓ | 74 | 44 | 74.0 |

**Total nodes:** 277  
**Total extraction time:** 194.8s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **80%** | Avg tokens: **563** | ≥80% recall: **17/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 20 | 620 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 14 | 435 |  |
| q_api_03 | 100% | 18 | 515 |  |
| q_api_04 | 100% | 20 | 602 |  |
| q_api_05 | 100% | 10 | 347 |  |
| q_api_06 | 100% | 18 | 533 |  |
| q_api_07 | 100% | 15 | 482 |  |
| q_api_08 | 100% | 12 | 398 |  |
| q_pipe_01 | 75% | 25 | 751 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 804 | Parquet format, partitioned by date and event_type |
| q_pipe_03 | 0% | 14 | 453 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 50% | 25 | 758 | PII scrubbing (mask emails and phone numbers); schema validation against Avro schemas |
| q_pipe_05 | 0% | 25 | 738 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 80% | 25 | 835 | Prometheus + Grafana |
| q_pipe_07 | 100% | 15 | 444 |  |
| q_auth_01 | 100% | 14 | 441 |  |
| q_auth_02 | 83% | 19 | 547 | access tokens: 15 minutes |
| q_auth_03 | 100% | 11 | 351 |  |
| q_auth_04 | 80% | 22 | 698 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 21 | 645 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 402 |  |
| q_auth_07 | 83% | 18 | 592 | mTLS between internal services where possible |
| q_auth_08 | 75% | 21 | 554 | separate read-only account for auditors |

### default

Mean recall: **82%** | Avg tokens: **548** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 17 | 550 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 12 | 391 |  |
| q_api_03 | 100% | 17 | 498 |  |
| q_api_04 | 67% | 19 | 580 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 9 | 334 |  |
| q_api_06 | 100% | 18 | 541 |  |
| q_api_07 | 100% | 13 | 440 |  |
| q_api_08 | 100% | 11 | 382 |  |
| q_pipe_01 | 75% | 25 | 747 | moved away from JSON |
| q_pipe_02 | 60% | 21 | 699 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 80% | 14 | 479 | evaluated Apache Iceberg and Hudi |
| q_pipe_04 | 50% | 25 | 767 | PII scrubbing (mask emails and phone numbers); schema validation against Avro schemas |
| q_pipe_05 | 33% | 25 | 774 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 80% | 24 | 813 | Prometheus + Grafana |
| q_pipe_07 | 100% | 14 | 430 |  |
| q_auth_01 | 100% | 14 | 450 |  |
| q_auth_02 | 83% | 19 | 556 | access tokens: 15 minutes |
| q_auth_03 | 100% | 11 | 360 |  |
| q_auth_04 | 60% | 20 | 644 | originally considered simple role-based system; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 21 | 654 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 411 |  |
| q_auth_07 | 83% | 16 | 549 | mTLS between internal services where possible |
| q_auth_08 | 75% | 21 | 563 | separate read-only account for auditors |

### filtered

Mean recall: **82%** | Avg tokens: **555** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 17 | 557 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 12 | 398 |  |
| q_api_03 | 100% | 17 | 505 |  |
| q_api_04 | 67% | 19 | 586 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 9 | 340 |  |
| q_api_06 | 100% | 18 | 548 |  |
| q_api_07 | 100% | 13 | 446 |  |
| q_api_08 | 100% | 11 | 389 |  |
| q_pipe_01 | 75% | 25 | 754 | moved away from JSON |
| q_pipe_02 | 60% | 21 | 706 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 80% | 14 | 486 | evaluated Apache Iceberg and Hudi |
| q_pipe_04 | 50% | 25 | 773 | PII scrubbing (mask emails and phone numbers); schema validation against Avro schemas |
| q_pipe_05 | 33% | 25 | 781 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 80% | 24 | 820 | Prometheus + Grafana |
| q_pipe_07 | 100% | 14 | 436 |  |
| q_auth_01 | 100% | 14 | 457 |  |
| q_auth_02 | 83% | 19 | 562 | access tokens: 15 minutes |
| q_auth_03 | 100% | 11 | 367 |  |
| q_auth_04 | 60% | 20 | 650 | originally considered simple role-based system; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 21 | 661 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 418 |  |
| q_auth_07 | 83% | 16 | 556 | mTLS between internal services where possible |
| q_auth_08 | 75% | 21 | 570 | separate read-only account for auditors |

### tight

Mean recall: **77%** | Avg tokens: **503** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 17 | 562 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 12 | 402 |  |
| q_api_03 | 100% | 17 | 509 |  |
| q_api_04 | 67% | 18 | 563 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 9 | 345 |  |
| q_api_06 | 100% | 18 | 553 |  |
| q_api_07 | 100% | 13 | 451 |  |
| q_api_08 | 100% | 11 | 394 |  |
| q_pipe_01 | 50% | 19 | 568 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 17 | 573 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 80% | 14 | 490 | evaluated Apache Iceberg and Hudi |
| q_pipe_04 | 0% | 17 | 556 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 0% | 18 | 560 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 80% | 16 | 559 | Prometheus + Grafana |
| q_pipe_07 | 100% | 14 | 441 |  |
| q_auth_01 | 100% | 14 | 462 |  |
| q_auth_02 | 83% | 19 | 567 | access tokens: 15 minutes |
| q_auth_03 | 100% | 11 | 372 |  |
| q_auth_04 | 60% | 17 | 563 | originally considered simple role-based system; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 17 | 560 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 423 |  |
| q_auth_07 | 83% | 16 | 560 | mTLS between internal services where possible |
| q_auth_08 | 75% | 20 | 546 | separate read-only account for auditors |
