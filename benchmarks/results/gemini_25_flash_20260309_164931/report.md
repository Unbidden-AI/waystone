# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_164931  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 78 | 16 | 57.87 |
| project_auth_system | ✓ | 132 | 75 | 69.79 |
| project_data_pipeline | ✓ | 94 | 94 | 65.39 |

**Total nodes:** 304  
**Total extraction time:** 193.1s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **83%** | Avg tokens: **571** | ≥80% recall: **16/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 16 | 486 | scales horizontally |
| q_api_02 | 100% | 13 | 410 |  |
| q_api_03 | 80% | 17 | 478 | SameSite=Strict |
| q_api_04 | 67% | 14 | 411 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 24 | 731 | hard cap at 100 items |
| q_api_06 | 67% | 12 | 357 | all five fields required |
| q_api_07 | 100% | 13 | 417 |  |
| q_api_08 | 100% | 12 | 388 |  |
| q_pipe_01 | 50% | 25 | 750 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 100% | 25 | 779 |  |
| q_pipe_03 | 40% | 25 | 797 | ML team already has Spark clusters configured for Delta Lake; switching would take 6+ weeks (+1 more) |
| q_pipe_04 | 100% | 25 | 782 |  |
| q_pipe_05 | 67% | 25 | 785 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 80% | 25 | 767 | three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 759 |  |
| q_auth_01 | 100% | 16 | 502 |  |
| q_auth_02 | 67% | 18 | 501 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 23 | 652 |  |
| q_auth_04 | 80% | 20 | 648 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 14 | 465 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 430 |  |
| q_auth_07 | 83% | 14 | 459 | mTLS between internal services where possible |
| q_auth_08 | 100% | 14 | 385 |  |

### default

Mean recall: **82%** | Avg tokens: **586** | ≥80% recall: **16/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 16 | 495 | scales horizontally |
| q_api_02 | 100% | 13 | 419 |  |
| q_api_03 | 80% | 25 | 722 | SameSite=Strict |
| q_api_04 | 67% | 13 | 391 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 24 | 739 | hard cap at 100 items |
| q_api_06 | 67% | 12 | 365 | all five fields required |
| q_api_07 | 100% | 13 | 426 |  |
| q_api_08 | 100% | 12 | 397 |  |
| q_pipe_01 | 50% | 25 | 761 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 80% | 25 | 794 | originally planned S3 |
| q_pipe_03 | 20% | 25 | 802 | evaluated Apache Iceberg and Hudi; ML team already has Spark clusters configured for Delta Lake (+2 more) |
| q_pipe_04 | 100% | 25 | 788 |  |
| q_pipe_05 | 67% | 25 | 797 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 25 | 766 |  |
| q_pipe_07 | 100% | 25 | 761 |  |
| q_auth_01 | 100% | 16 | 511 |  |
| q_auth_02 | 67% | 18 | 510 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 23 | 661 |  |
| q_auth_04 | 80% | 18 | 600 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 14 | 474 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 438 |  |
| q_auth_07 | 83% | 14 | 468 | mTLS between internal services where possible |
| q_auth_08 | 100% | 14 | 394 |  |

### filtered

Mean recall: **82%** | Avg tokens: **593** | ≥80% recall: **16/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 16 | 502 | scales horizontally |
| q_api_02 | 100% | 13 | 425 |  |
| q_api_03 | 80% | 25 | 728 | SameSite=Strict |
| q_api_04 | 67% | 13 | 398 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 24 | 746 | hard cap at 100 items |
| q_api_06 | 67% | 12 | 372 | all five fields required |
| q_api_07 | 100% | 13 | 433 |  |
| q_api_08 | 100% | 12 | 404 |  |
| q_pipe_01 | 50% | 25 | 767 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 80% | 25 | 801 | originally planned S3 |
| q_pipe_03 | 20% | 25 | 809 | evaluated Apache Iceberg and Hudi; ML team already has Spark clusters configured for Delta Lake (+2 more) |
| q_pipe_04 | 100% | 25 | 795 |  |
| q_pipe_05 | 67% | 25 | 804 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 25 | 773 |  |
| q_pipe_07 | 100% | 25 | 768 |  |
| q_auth_01 | 100% | 16 | 518 |  |
| q_auth_02 | 67% | 18 | 517 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 23 | 667 |  |
| q_auth_04 | 80% | 18 | 607 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 14 | 481 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 445 |  |
| q_auth_07 | 83% | 14 | 475 | mTLS between internal services where possible |
| q_auth_08 | 100% | 14 | 400 |  |

### tight

Mean recall: **72%** | Avg tokens: **505** | ≥80% recall: **12/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 16 | 506 | scales horizontally |
| q_api_02 | 100% | 13 | 430 |  |
| q_api_03 | 20% | 18 | 550 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 13 | 403 | originally three roles: admin, member, viewer |
| q_api_05 | 17% | 17 | 561 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 67% | 12 | 377 | all five fields required |
| q_api_07 | 100% | 13 | 437 |  |
| q_api_08 | 100% | 12 | 408 |  |
| q_pipe_01 | 25% | 18 | 550 | moved away from JSON; fastavro library (3x faster than official confluent library) (+1 more) |
| q_pipe_02 | 60% | 17 | 576 | originally planned S3; cross-cloud egress costs were too high |
| q_pipe_03 | 20% | 17 | 565 | evaluated Apache Iceberg and Hudi; ML team already has Spark clusters configured for Delta Lake (+2 more) |
| q_pipe_04 | 100% | 17 | 560 |  |
| q_pipe_05 | 33% | 17 | 581 | RocksDB used instead for embedded state; Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 60% | 18 | 571 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 17 | 550 |  |
| q_auth_01 | 100% | 16 | 522 |  |
| q_auth_02 | 67% | 18 | 522 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 19 | 571 |  |
| q_auth_04 | 80% | 16 | 558 | OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 14 | 486 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 450 |  |
| q_auth_07 | 83% | 14 | 479 | mTLS between internal services where possible |
| q_auth_08 | 100% | 14 | 405 |  |
