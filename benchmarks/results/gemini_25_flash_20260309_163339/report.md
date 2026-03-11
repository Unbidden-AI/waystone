# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_163339  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 70 | 52 | 54.47 |
| project_auth_system | ✓ | 133 | 51 | 67.76 |
| project_data_pipeline | ✓ | 86 | 84 | 55.98 |

**Total nodes:** 289  
**Total extraction time:** 178.2s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **81%** | Avg tokens: **495** | ≥80% recall: **17/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 2 | 89 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 7 | 250 |  |
| q_api_03 | 100% | 25 | 694 |  |
| q_api_04 | 100% | 11 | 385 |  |
| q_api_05 | 100% | 8 | 263 |  |
| q_api_06 | 100% | 4 | 173 |  |
| q_api_07 | 100% | 11 | 379 |  |
| q_api_08 | 100% | 16 | 493 |  |
| q_pipe_01 | 0% | 25 | 829 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 80% | 25 | 780 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 820 |  |
| q_pipe_04 | 50% | 7 | 259 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 25 | 820 |  |
| q_pipe_06 | 60% | 25 | 794 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 799 |  |
| q_auth_01 | 100% | 14 | 420 |  |
| q_auth_02 | 83% | 25 | 685 | access tokens: 15 minutes |
| q_auth_03 | 100% | 16 | 458 |  |
| q_auth_04 | 60% | 12 | 398 | Open Policy Agent (OPA) as policy engine; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 219 | IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 396 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 22 | 694 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 286 | separate read-only account for auditors |

### default

Mean recall: **81%** | Avg tokens: **502** | ≥80% recall: **16/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 2 | 98 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 7 | 259 |  |
| q_api_03 | 100% | 25 | 704 |  |
| q_api_04 | 67% | 10 | 362 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 8 | 272 |  |
| q_api_06 | 100% | 4 | 182 |  |
| q_api_07 | 100% | 11 | 388 |  |
| q_api_08 | 100% | 16 | 502 |  |
| q_pipe_01 | 50% | 25 | 852 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 80% | 25 | 788 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 830 |  |
| q_pipe_04 | 50% | 7 | 267 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 25 | 828 |  |
| q_pipe_06 | 60% | 25 | 803 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 808 |  |
| q_auth_01 | 100% | 14 | 428 |  |
| q_auth_02 | 83% | 25 | 694 | access tokens: 15 minutes |
| q_auth_03 | 100% | 16 | 467 |  |
| q_auth_04 | 60% | 11 | 377 | Open Policy Agent (OPA) as policy engine; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 228 | IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 404 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 22 | 703 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 295 | separate read-only account for auditors |

### filtered

Mean recall: **81%** | Avg tokens: **508** | ≥80% recall: **16/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 2 | 105 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 7 | 266 |  |
| q_api_03 | 100% | 25 | 710 |  |
| q_api_04 | 67% | 10 | 369 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 8 | 279 |  |
| q_api_06 | 100% | 4 | 188 |  |
| q_api_07 | 100% | 11 | 394 |  |
| q_api_08 | 100% | 16 | 509 |  |
| q_pipe_01 | 50% | 25 | 859 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 80% | 25 | 795 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 836 |  |
| q_pipe_04 | 50% | 7 | 274 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 25 | 835 |  |
| q_pipe_06 | 60% | 25 | 810 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 25 | 815 |  |
| q_auth_01 | 100% | 14 | 435 |  |
| q_auth_02 | 83% | 25 | 701 | access tokens: 15 minutes |
| q_auth_03 | 100% | 16 | 474 |  |
| q_auth_04 | 60% | 11 | 384 | Open Policy Agent (OPA) as policy engine; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 235 | IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 411 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 22 | 709 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 302 | separate read-only account for auditors |

### tight

Mean recall: **79%** | Avg tokens: **425** | ≥80% recall: **16/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 2 | 110 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 7 | 270 |  |
| q_api_03 | 100% | 19 | 552 |  |
| q_api_04 | 67% | 10 | 373 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 8 | 284 |  |
| q_api_06 | 100% | 4 | 193 |  |
| q_api_07 | 100% | 11 | 399 |  |
| q_api_08 | 100% | 16 | 514 |  |
| q_pipe_01 | 0% | 16 | 570 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 80% | 17 | 566 | originally planned S3 |
| q_pipe_03 | 100% | 17 | 585 |  |
| q_pipe_04 | 50% | 7 | 279 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 17 | 586 |  |
| q_pipe_06 | 60% | 17 | 556 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 16 | 557 |  |
| q_auth_01 | 100% | 14 | 440 |  |
| q_auth_02 | 83% | 20 | 555 | access tokens: 15 minutes |
| q_auth_03 | 100% | 16 | 478 |  |
| q_auth_04 | 60% | 11 | 388 | Open Policy Agent (OPA) as policy engine; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 240 | IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 416 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 17 | 563 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 306 | separate read-only account for auditors |
