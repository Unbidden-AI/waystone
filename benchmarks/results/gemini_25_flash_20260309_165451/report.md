# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_165451  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 75 | 40 | 42.8 |
| project_auth_system | ✓ | 127 | 104 | 69.81 |
| project_data_pipeline | ✓ | 87 | 86 | 94.8 |

**Total nodes:** 289  
**Total extraction time:** 207.4s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **85%** | Avg tokens: **640** | ≥80% recall: **19/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 25 | 746 | scales horizontally |
| q_api_02 | 100% | 25 | 726 |  |
| q_api_03 | 100% | 25 | 716 |  |
| q_api_04 | 100% | 12 | 370 |  |
| q_api_05 | 83% | 20 | 619 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 369 |  |
| q_api_07 | 100% | 21 | 621 |  |
| q_api_08 | 100% | 21 | 657 |  |
| q_pipe_01 | 0% | 25 | 638 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 100% | 25 | 768 |  |
| q_pipe_03 | 100% | 25 | 792 |  |
| q_pipe_04 | 0% | 25 | 696 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 25 | 777 |  |
| q_pipe_06 | 40% | 25 | 745 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency (+1 more) |
| q_pipe_07 | 100% | 25 | 759 |  |
| q_auth_01 | 100% | 23 | 652 |  |
| q_auth_02 | 100% | 25 | 689 |  |
| q_auth_03 | 100% | 23 | 632 |  |
| q_auth_04 | 80% | 25 | 758 | originally considered simple role-based system |
| q_auth_05 | 80% | 25 | 721 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 399 |  |
| q_auth_07 | 100% | 15 | 481 |  |
| q_auth_08 | 100% | 14 | 383 |  |

### default

Mean recall: **84%** | Avg tokens: **645** | ≥80% recall: **18/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 25 | 755 | scales horizontally |
| q_api_02 | 100% | 25 | 734 |  |
| q_api_03 | 100% | 25 | 729 |  |
| q_api_04 | 67% | 11 | 347 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 20 | 628 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 378 |  |
| q_api_07 | 100% | 21 | 630 |  |
| q_api_08 | 100% | 21 | 666 |  |
| q_pipe_01 | 0% | 25 | 646 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 80% | 25 | 783 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 799 |  |
| q_pipe_04 | 0% | 25 | 705 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 25 | 780 |  |
| q_pipe_06 | 60% | 25 | 755 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 769 |  |
| q_auth_01 | 100% | 21 | 608 |  |
| q_auth_02 | 100% | 25 | 697 |  |
| q_auth_03 | 100% | 23 | 641 |  |
| q_auth_04 | 80% | 25 | 767 | originally considered simple role-based system |
| q_auth_05 | 80% | 25 | 729 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 408 |  |
| q_auth_07 | 100% | 15 | 490 |  |
| q_auth_08 | 100% | 14 | 392 |  |

### filtered

Mean recall: **84%** | Avg tokens: **652** | ≥80% recall: **18/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 25 | 761 | scales horizontally |
| q_api_02 | 100% | 25 | 741 |  |
| q_api_03 | 100% | 25 | 736 |  |
| q_api_04 | 67% | 11 | 354 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 20 | 635 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 384 |  |
| q_api_07 | 100% | 21 | 637 |  |
| q_api_08 | 100% | 21 | 673 |  |
| q_pipe_01 | 0% | 25 | 653 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 80% | 25 | 789 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 806 |  |
| q_pipe_04 | 0% | 25 | 712 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 25 | 787 |  |
| q_pipe_06 | 60% | 25 | 761 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 775 |  |
| q_auth_01 | 100% | 21 | 615 |  |
| q_auth_02 | 100% | 25 | 704 |  |
| q_auth_03 | 100% | 23 | 648 |  |
| q_auth_04 | 80% | 25 | 774 | originally considered simple role-based system |
| q_auth_05 | 80% | 25 | 736 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 414 |  |
| q_auth_07 | 100% | 15 | 496 |  |
| q_auth_08 | 100% | 14 | 399 |  |

### tight

Mean recall: **74%** | Avg tokens: **527** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 18 | 566 | scales horizontally |
| q_api_02 | 75% | 18 | 547 | asymmetric — secret is never shared |
| q_api_03 | 100% | 18 | 562 |  |
| q_api_04 | 67% | 11 | 358 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 17 | 553 | hard cap at 100 items |
| q_api_06 | 100% | 12 | 389 |  |
| q_api_07 | 100% | 18 | 568 |  |
| q_api_08 | 100% | 17 | 551 |  |
| q_pipe_01 | 0% | 20 | 543 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 60% | 17 | 579 | originally planned S3; ML team infrastructure already on GCP |
| q_pipe_03 | 100% | 17 | 574 |  |
| q_pipe_04 | 0% | 18 | 542 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 67% | 18 | 579 | RocksDB used instead for embedded state |
| q_pipe_06 | 0% | 18 | 550 | under 500ms for the hot path; cold path up to 5 minutes (+3 more) |
| q_pipe_07 | 100% | 17 | 539 |  |
| q_auth_01 | 100% | 18 | 545 |  |
| q_auth_02 | 83% | 20 | 564 | access tokens: 15 minutes |
| q_auth_03 | 60% | 19 | 542 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 40% | 17 | 574 | originally considered simple role-based system; enterprise requirements changed it: tenant isolation, resource-level permissions, time-based access windows (+1 more) |
| q_auth_05 | 80% | 18 | 567 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 419 |  |
| q_auth_07 | 100% | 15 | 501 |  |
| q_auth_08 | 100% | 14 | 403 |  |
