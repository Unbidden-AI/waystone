# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_163343  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 72 | 37 | 64.72 |
| project_auth_system | ✓ | 122 | 22 | 58.87 |
| project_data_pipeline | ✓ | 87 | 82 | 98.14 |

**Total nodes:** 281  
**Total extraction time:** 221.7s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **76%** | Avg tokens: **578** | ≥80% recall: **12/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 165 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 25 | 810 | asymmetric — secret is never shared |
| q_api_03 | 100% | 25 | 765 |  |
| q_api_04 | 67% | 8 | 248 | guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 17% | 25 | 785 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 25 | 792 |  |
| q_api_07 | 100% | 25 | 803 |  |
| q_api_08 | 100% | 25 | 783 |  |
| q_pipe_01 | 75% | 15 | 501 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 760 | originally planned S3 |
| q_pipe_03 | 40% | 25 | 778 | evaluated Apache Iceberg and Hudi; ML team already has Spark clusters configured for Delta Lake (+1 more) |
| q_pipe_04 | 50% | 7 | 268 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 23 | 758 |  |
| q_pipe_06 | 100% | 25 | 771 |  |
| q_pipe_07 | 100% | 25 | 768 |  |
| q_auth_01 | 75% | 13 | 408 | non-starter for enterprise customers |
| q_auth_02 | 67% | 25 | 743 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 9 | 306 |  |
| q_auth_04 | 60% | 11 | 382 | Open Policy Agent (OPA) as policy engine; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 80% | 6 | 217 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 395 |  |
| q_auth_07 | 83% | 25 | 800 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 286 | separate read-only account for auditors |

### default

Mean recall: **75%** | Avg tokens: **583** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 174 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 25 | 819 | asymmetric — secret is never shared |
| q_api_03 | 100% | 25 | 778 |  |
| q_api_04 | 33% | 7 | 227 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 17% | 25 | 794 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 25 | 801 |  |
| q_api_07 | 100% | 25 | 812 |  |
| q_api_08 | 100% | 25 | 792 |  |
| q_pipe_01 | 75% | 14 | 486 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 767 | originally planned S3 |
| q_pipe_03 | 80% | 25 | 809 | evaluated Apache Iceberg and Hudi |
| q_pipe_04 | 50% | 7 | 277 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 22 | 735 |  |
| q_pipe_06 | 100% | 25 | 790 |  |
| q_pipe_07 | 100% | 25 | 777 |  |
| q_auth_01 | 75% | 13 | 416 | non-starter for enterprise customers |
| q_auth_02 | 67% | 25 | 752 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 9 | 315 |  |
| q_auth_04 | 40% | 10 | 354 | Open Policy Agent (OPA) as policy engine; originally considered simple role-based system (+1 more) |
| q_auth_05 | 80% | 6 | 225 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 404 |  |
| q_auth_07 | 83% | 25 | 808 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 295 | separate read-only account for auditors |

### filtered

Mean recall: **75%** | Avg tokens: **590** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 181 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 25 | 825 | asymmetric — secret is never shared |
| q_api_03 | 100% | 25 | 784 |  |
| q_api_04 | 33% | 7 | 234 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 17% | 25 | 801 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 25 | 807 |  |
| q_api_07 | 100% | 25 | 819 |  |
| q_api_08 | 100% | 25 | 799 |  |
| q_pipe_01 | 75% | 14 | 493 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 774 | originally planned S3 |
| q_pipe_03 | 80% | 25 | 816 | evaluated Apache Iceberg and Hudi |
| q_pipe_04 | 50% | 7 | 284 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 22 | 742 |  |
| q_pipe_06 | 100% | 25 | 797 |  |
| q_pipe_07 | 100% | 25 | 783 |  |
| q_auth_01 | 75% | 13 | 423 | non-starter for enterprise customers |
| q_auth_02 | 67% | 25 | 759 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 9 | 321 |  |
| q_auth_04 | 40% | 10 | 360 | Open Policy Agent (OPA) as policy engine; originally considered simple role-based system (+1 more) |
| q_auth_05 | 80% | 6 | 232 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 410 |  |
| q_auth_07 | 83% | 25 | 815 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 302 | separate read-only account for auditors |

### tight

Mean recall: **71%** | Avg tokens: **462** | ≥80% recall: **12/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 186 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 16 | 551 | asymmetric — secret is never shared |
| q_api_03 | 80% | 18 | 568 | SameSite=Strict |
| q_api_04 | 33% | 7 | 238 | originally three roles: admin, member, viewer; guest role was added for unauthenticated access to public endpoints |
| q_api_05 | 17% | 16 | 559 | cursor-based pagination; offset pagination breaks when rows are inserted (+3 more) |
| q_api_06 | 100% | 17 | 565 |  |
| q_api_07 | 100% | 17 | 572 |  |
| q_api_08 | 100% | 16 | 555 |  |
| q_pipe_01 | 75% | 14 | 498 | moved away from JSON |
| q_pipe_02 | 80% | 17 | 564 | originally planned S3 |
| q_pipe_03 | 20% | 17 | 574 | evaluated Apache Iceberg and Hudi; ML team already has Spark clusters configured for Delta Lake (+2 more) |
| q_pipe_04 | 50% | 7 | 289 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 16 | 572 |  |
| q_pipe_06 | 80% | 18 | 578 | three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 17 | 556 |  |
| q_auth_01 | 75% | 13 | 428 | non-starter for enterprise customers |
| q_auth_02 | 67% | 18 | 561 | access tokens: 15 minutes; stored in database |
| q_auth_03 | 100% | 9 | 326 |  |
| q_auth_04 | 40% | 10 | 365 | Open Policy Agent (OPA) as policy engine; originally considered simple role-based system (+1 more) |
| q_auth_05 | 80% | 6 | 237 | IP-level rate limiting also applied |
| q_auth_06 | 100% | 12 | 415 |  |
| q_auth_07 | 83% | 17 | 573 | mTLS between internal services where possible |
| q_auth_08 | 75% | 11 | 306 | separate read-only account for auditors |
