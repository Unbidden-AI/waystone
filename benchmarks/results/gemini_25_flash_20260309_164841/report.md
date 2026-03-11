# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_164841  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 72 | 37 | 79.48 |
| project_auth_system | ✓ | 126 | 97 | 101.94 |
| project_data_pipeline | ✓ | 89 | 81 | 57.85 |

**Total nodes:** 287  
**Total extraction time:** 239.3s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **75%** | Avg tokens: **661** | ≥80% recall: **16/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 773 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 709 | asymmetric — secret is never shared |
| q_api_03 | 40% | 25 | 705 | 7-day refresh tokens; SameSite=Strict (+1 more) |
| q_api_04 | 100% | 13 | 411 |  |
| q_api_05 | 83% | 19 | 605 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 368 |  |
| q_api_07 | 80% | 25 | 807 | v1 and v2 can coexist |
| q_api_08 | 25% | 25 | 722 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+1 more) |
| q_pipe_01 | 0% | 25 | 707 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 100% | 25 | 772 |  |
| q_pipe_03 | 100% | 25 | 800 |  |
| q_pipe_04 | 100% | 25 | 816 |  |
| q_pipe_05 | 33% | 25 | 765 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 80% | 25 | 824 | Prometheus + Grafana |
| q_pipe_07 | 0% | 25 | 696 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 21 | 603 |  |
| q_auth_02 | 100% | 25 | 703 |  |
| q_auth_03 | 100% | 19 | 590 |  |
| q_auth_04 | 80% | 25 | 787 | originally considered simple role-based system |
| q_auth_05 | 80% | 12 | 381 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 408 |  |
| q_auth_07 | 100% | 25 | 832 |  |
| q_auth_08 | 100% | 14 | 413 |  |

### default

Mean recall: **73%** | Avg tokens: **667** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 782 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 718 | asymmetric — secret is never shared |
| q_api_03 | 40% | 25 | 744 | 7-day refresh tokens; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 12 | 390 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 19 | 614 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 376 |  |
| q_api_07 | 80% | 25 | 815 | v1 and v2 can coexist |
| q_api_08 | 25% | 25 | 731 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+1 more) |
| q_pipe_01 | 0% | 25 | 715 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 80% | 25 | 786 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 809 |  |
| q_pipe_04 | 100% | 25 | 825 |  |
| q_pipe_05 | 33% | 25 | 774 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 80% | 25 | 833 | Prometheus + Grafana |
| q_pipe_07 | 0% | 25 | 705 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 19 | 559 |  |
| q_auth_02 | 100% | 25 | 714 |  |
| q_auth_03 | 100% | 19 | 599 |  |
| q_auth_04 | 80% | 25 | 795 | originally considered simple role-based system |
| q_auth_05 | 80% | 12 | 390 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 417 |  |
| q_auth_07 | 100% | 25 | 840 |  |
| q_auth_08 | 100% | 14 | 421 |  |

### filtered

Mean recall: **73%** | Avg tokens: **674** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 25 | 789 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 75% | 25 | 725 | asymmetric — secret is never shared |
| q_api_03 | 40% | 25 | 750 | 7-day refresh tokens; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 12 | 396 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 19 | 620 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 383 |  |
| q_api_07 | 80% | 25 | 822 | v1 and v2 can coexist |
| q_api_08 | 25% | 25 | 738 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+1 more) |
| q_pipe_01 | 0% | 25 | 722 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 80% | 25 | 793 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 816 |  |
| q_pipe_04 | 100% | 25 | 832 |  |
| q_pipe_05 | 33% | 25 | 781 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 80% | 25 | 839 | Prometheus + Grafana |
| q_pipe_07 | 0% | 25 | 711 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 19 | 566 |  |
| q_auth_02 | 100% | 25 | 720 |  |
| q_auth_03 | 100% | 19 | 606 |  |
| q_auth_04 | 80% | 25 | 802 | originally considered simple role-based system |
| q_auth_05 | 80% | 12 | 397 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 424 |  |
| q_auth_07 | 100% | 25 | 847 |  |
| q_auth_08 | 100% | 14 | 428 |  |

### tight

Mean recall: **64%** | Avg tokens: **527** | ≥80% recall: **11/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 17 | 546 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 50% | 19 | 561 | public keys distributed to other services; asymmetric — secret is never shared |
| q_api_03 | 40% | 18 | 576 | 7-day refresh tokens; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 12 | 401 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 17 | 559 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 388 |  |
| q_api_07 | 80% | 16 | 555 | v1 and v2 can coexist |
| q_api_08 | 0% | 18 | 540 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 18 | 545 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 60% | 17 | 565 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 80% | 17 | 580 | switching would take 6+ weeks |
| q_pipe_04 | 75% | 16 | 586 | PII scrubbing happens in the Kafka consumer before any downstream topic |
| q_pipe_05 | 33% | 18 | 566 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 16 | 570 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 0% | 18 | 542 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 100% | 18 | 537 |  |
| q_auth_02 | 83% | 19 | 562 | access tokens: 15 minutes |
| q_auth_03 | 60% | 17 | 551 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 80% | 16 | 566 | originally considered simple role-based system |
| q_auth_05 | 80% | 12 | 401 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 429 |  |
| q_auth_07 | 100% | 16 | 571 |  |
| q_auth_08 | 100% | 14 | 433 |  |
