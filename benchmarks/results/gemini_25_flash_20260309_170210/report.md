# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_170210  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 76 | 40 | 43.1 |
| project_auth_system | ✓ | 128 | 85 | 88.81 |
| project_data_pipeline | ✓ | 84 | 83 | 63.2 |

**Total nodes:** 288  
**Total extraction time:** 195.1s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **92%** | Avg tokens: **600** | ≥80% recall: **19/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 19 | 565 |  |
| q_api_02 | 100% | 25 | 719 |  |
| q_api_03 | 100% | 19 | 546 |  |
| q_api_04 | 67% | 12 | 368 | guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 100% | 8 | 269 |  |
| q_api_06 | 100% | 17 | 505 |  |
| q_api_07 | 100% | 15 | 468 |  |
| q_api_08 | 100% | 13 | 421 |  |
| q_pipe_01 | 75% | 25 | 854 | moved away from JSON |
| q_pipe_02 | 100% | 25 | 826 |  |
| q_pipe_03 | 60% | 25 | 788 | ML team already has Spark clusters configured for Delta Lake; switching would take 6+ weeks |
| q_pipe_04 | 100% | 25 | 845 |  |
| q_pipe_05 | 100% | 25 | 782 |  |
| q_pipe_06 | 100% | 25 | 822 |  |
| q_pipe_07 | 100% | 25 | 813 |  |
| q_auth_01 | 75% | 17 | 521 | non-starter for enterprise customers |
| q_auth_02 | 83% | 20 | 571 | access tokens: 15 minutes |
| q_auth_03 | 100% | 24 | 677 |  |
| q_auth_04 | 100% | 23 | 708 |  |
| q_auth_05 | 80% | 14 | 453 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 429 |  |
| q_auth_07 | 83% | 17 | 534 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 321 |  |

### default

Mean recall: **88%** | Avg tokens: **597** | ≥80% recall: **20/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 18 | 546 |  |
| q_api_02 | 100% | 24 | 700 |  |
| q_api_03 | 80% | 21 | 648 | not localStorage due to XSS risk |
| q_api_04 | 33% | 10 | 324 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 6 | 222 | offset pagination breaks when rows are inserted |
| q_api_06 | 100% | 13 | 403 |  |
| q_api_07 | 80% | 13 | 430 | header versioning made caching impossible |
| q_api_08 | 100% | 13 | 430 |  |
| q_pipe_01 | 75% | 25 | 863 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 836 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 804 |  |
| q_pipe_04 | 100% | 25 | 853 |  |
| q_pipe_05 | 100% | 25 | 805 |  |
| q_pipe_06 | 100% | 25 | 831 |  |
| q_pipe_07 | 100% | 25 | 841 |  |
| q_auth_01 | 75% | 17 | 530 | non-starter for enterprise customers |
| q_auth_02 | 83% | 20 | 580 | access tokens: 15 minutes |
| q_auth_03 | 100% | 24 | 686 |  |
| q_auth_04 | 80% | 20 | 626 | originally considered simple role-based system |
| q_auth_05 | 80% | 14 | 462 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 438 |  |
| q_auth_07 | 83% | 17 | 543 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 329 |  |

### filtered

Mean recall: **88%** | Avg tokens: **604** | ≥80% recall: **20/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 18 | 553 |  |
| q_api_02 | 100% | 24 | 707 |  |
| q_api_03 | 80% | 21 | 655 | not localStorage due to XSS risk |
| q_api_04 | 33% | 10 | 331 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 6 | 229 | offset pagination breaks when rows are inserted |
| q_api_06 | 100% | 13 | 410 |  |
| q_api_07 | 80% | 13 | 437 | header versioning made caching impossible |
| q_api_08 | 100% | 13 | 436 |  |
| q_pipe_01 | 75% | 25 | 870 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 843 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 811 |  |
| q_pipe_04 | 100% | 25 | 860 |  |
| q_pipe_05 | 100% | 25 | 812 |  |
| q_pipe_06 | 100% | 25 | 837 |  |
| q_pipe_07 | 100% | 25 | 848 |  |
| q_auth_01 | 75% | 17 | 537 | non-starter for enterprise customers |
| q_auth_02 | 83% | 20 | 587 | access tokens: 15 minutes |
| q_auth_03 | 100% | 24 | 692 |  |
| q_auth_04 | 80% | 20 | 632 | originally considered simple role-based system |
| q_auth_05 | 80% | 14 | 468 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 444 |  |
| q_auth_07 | 83% | 17 | 550 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 336 |  |

### tight

Mean recall: **81%** | Avg tokens: **502** | ≥80% recall: **15/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 100% | 18 | 558 |  |
| q_api_02 | 75% | 18 | 547 | asymmetric — secret is never shared |
| q_api_03 | 40% | 17 | 557 | 7-day refresh tokens; SameSite=Strict (+1 more) |
| q_api_04 | 33% | 10 | 336 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 83% | 6 | 234 | offset pagination breaks when rows are inserted |
| q_api_06 | 100% | 13 | 415 |  |
| q_api_07 | 80% | 13 | 442 | header versioning made caching impossible |
| q_api_08 | 100% | 13 | 441 |  |
| q_pipe_01 | 75% | 16 | 584 | moved away from JSON |
| q_pipe_02 | 60% | 16 | 562 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 17 | 571 |  |
| q_pipe_04 | 75% | 16 | 577 | event deduplication (30-second window) |
| q_pipe_05 | 100% | 16 | 571 |  |
| q_pipe_06 | 80% | 16 | 561 | three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 16 | 566 |  |
| q_auth_01 | 75% | 17 | 542 | non-starter for enterprise customers |
| q_auth_02 | 83% | 19 | 563 | access tokens: 15 minutes |
| q_auth_03 | 60% | 19 | 561 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 80% | 17 | 541 | originally considered simple role-based system |
| q_auth_05 | 80% | 14 | 473 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 449 |  |
| q_auth_07 | 83% | 17 | 554 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 341 |  |
