# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_231810  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 74 | 50 | 107.58 |
| project_auth_system | ✓ | 129 | 108 | 153.78 |
| project_data_pipeline | ✓ | 92 | 100 | 134.81 |

**Total nodes:** 295  
**Total extraction time:** 396.2s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **94%** | Avg tokens: **635** | ≥80% recall: **20/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 20 | 607 | scales horizontally |
| q_api_02 | 100% | 25 | 701 |  |
| q_api_03 | 100% | 25 | 683 |  |
| q_api_04 | 100% | 12 | 396 |  |
| q_api_05 | 100% | 25 | 789 |  |
| q_api_06 | 100% | 17 | 529 |  |
| q_api_07 | 100% | 20 | 595 |  |
| q_api_08 | 100% | 8 | 284 |  |
| q_pipe_01 | 75% | 25 | 822 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 773 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 798 |  |
| q_pipe_04 | 100% | 25 | 825 |  |
| q_pipe_05 | 100% | 25 | 754 |  |
| q_pipe_06 | 60% | 25 | 787 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 792 |  |
| q_auth_01 | 100% | 25 | 726 |  |
| q_auth_02 | 83% | 25 | 668 | opaque tokens can be instantly revoked |
| q_auth_03 | 100% | 23 | 635 |  |
| q_auth_04 | 100% | 24 | 762 |  |
| q_auth_05 | 80% | 15 | 459 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 405 |  |
| q_auth_07 | 100% | 16 | 505 |  |
| q_auth_08 | 100% | 12 | 317 |  |

### default

Mean recall: **93%** | Avg tokens: **637** | ≥80% recall: **20/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 20 | 616 | scales horizontally |
| q_api_02 | 100% | 25 | 710 |  |
| q_api_03 | 100% | 25 | 690 |  |
| q_api_04 | 100% | 11 | 371 |  |
| q_api_05 | 100% | 25 | 798 |  |
| q_api_06 | 100% | 14 | 452 |  |
| q_api_07 | 100% | 20 | 603 |  |
| q_api_08 | 100% | 8 | 293 |  |
| q_pipe_01 | 75% | 25 | 831 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 782 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 826 |  |
| q_pipe_04 | 100% | 25 | 834 |  |
| q_pipe_05 | 100% | 25 | 763 |  |
| q_pipe_06 | 60% | 25 | 796 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 801 |  |
| q_auth_01 | 100% | 25 | 735 |  |
| q_auth_02 | 83% | 25 | 677 | opaque tokens can be instantly revoked |
| q_auth_03 | 100% | 23 | 644 |  |
| q_auth_04 | 80% | 22 | 701 | originally considered simple role-based system |
| q_auth_05 | 80% | 15 | 468 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 13 | 414 |  |
| q_auth_07 | 100% | 16 | 513 |  |
| q_auth_08 | 100% | 12 | 326 |  |
