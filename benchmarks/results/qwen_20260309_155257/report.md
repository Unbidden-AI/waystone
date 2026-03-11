# Benchmark Report

**Model:** `qwen/qwen3.5-35b-a3b`  
**Config:** `qwen.yaml`  
**Mode:** full  
**Run:** 20260309_155257  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 46 | 32 | 284.56 |
| project_auth_system | ✓ | 69 | 39 | 567.38 |
| project_data_pipeline | ✓ | 55 | 16 | 426.58 |

**Total nodes:** 170  
**Total extraction time:** 1278.5s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **71%** | Avg tokens: **242** | ≥80% recall: **11/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 317 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 7 | 228 |  |
| q_api_03 | 40% | 10 | 354 | HttpOnly cookies; SameSite=Strict (+1 more) |
| q_api_04 | 100% | 7 | 236 |  |
| q_api_05 | 100% | 6 | 204 |  |
| q_api_06 | 67% | 5 | 206 | all five fields required |
| q_api_07 | 40% | 5 | 175 | v1 and v2 can coexist; minimum 12 months deprecation notice (+1 more) |
| q_api_08 | 100% | 5 | 187 |  |
| q_pipe_01 | 0% | 5 | 185 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 80% | 9 | 305 | Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 8 | 303 |  |
| q_pipe_04 | 50% | 3 | 133 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 6 | 237 |  |
| q_pipe_06 | 60% | 4 | 147 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 140 |  |
| q_auth_01 | 25% | 10 | 327 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+1 more) |
| q_auth_02 | 67% | 10 | 365 | access tokens: 15 minutes; opaque tokens can be instantly revoked |
| q_auth_03 | 60% | 5 | 186 | hardware keys and passkeys supported; SIM swapping makes SMS too weak |
| q_auth_04 | 80% | 10 | 349 | originally considered simple role-based system |
| q_auth_05 | 60% | 4 | 158 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 335 |  |
| q_auth_07 | 83% | 10 | 324 | API keys are not the preferred pattern (too easy to leak) |
| q_auth_08 | 75% | 4 | 164 | every auth event logged: login success/failure, MFA events, token operations, permission denied |

### default

Mean recall: **69%** | Avg tokens: **245** | ≥80% recall: **9/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 326 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 7 | 236 |  |
| q_api_03 | 40% | 10 | 363 | HttpOnly cookies; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 6 | 208 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 6 | 213 |  |
| q_api_06 | 67% | 5 | 215 | all five fields required |
| q_api_07 | 40% | 5 | 184 | v1 and v2 can coexist; minimum 12 months deprecation notice (+1 more) |
| q_api_08 | 100% | 5 | 196 |  |
| q_pipe_01 | 0% | 5 | 194 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 60% | 7 | 252 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 8 | 311 |  |
| q_pipe_04 | 50% | 3 | 142 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 6 | 245 |  |
| q_pipe_06 | 60% | 4 | 155 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 149 |  |
| q_auth_01 | 25% | 10 | 335 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+1 more) |
| q_auth_02 | 67% | 10 | 379 | access tokens: 15 minutes; opaque tokens can be instantly revoked |
| q_auth_03 | 60% | 5 | 194 | hardware keys and passkeys supported; SIM swapping makes SMS too weak |
| q_auth_04 | 80% | 9 | 321 | originally considered simple role-based system |
| q_auth_05 | 60% | 4 | 167 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 344 |  |
| q_auth_07 | 83% | 10 | 333 | API keys are not the preferred pattern (too easy to leak) |
| q_auth_08 | 75% | 4 | 173 | every auth event logged: login success/failure, MFA events, token operations, permission denied |

### filtered

Mean recall: **69%** | Avg tokens: **252** | ≥80% recall: **9/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 332 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 7 | 243 |  |
| q_api_03 | 40% | 10 | 370 | HttpOnly cookies; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 6 | 215 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 6 | 220 |  |
| q_api_06 | 67% | 5 | 222 | all five fields required |
| q_api_07 | 40% | 5 | 191 | v1 and v2 can coexist; minimum 12 months deprecation notice (+1 more) |
| q_api_08 | 100% | 5 | 203 |  |
| q_pipe_01 | 0% | 5 | 201 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 60% | 7 | 259 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 8 | 318 |  |
| q_pipe_04 | 50% | 3 | 149 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 6 | 252 |  |
| q_pipe_06 | 60% | 4 | 162 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 155 |  |
| q_auth_01 | 25% | 10 | 342 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+1 more) |
| q_auth_02 | 67% | 10 | 385 | access tokens: 15 minutes; opaque tokens can be instantly revoked |
| q_auth_03 | 60% | 5 | 201 | hardware keys and passkeys supported; SIM swapping makes SMS too weak |
| q_auth_04 | 80% | 9 | 328 | originally considered simple role-based system |
| q_auth_05 | 60% | 4 | 174 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 351 |  |
| q_auth_07 | 83% | 10 | 340 | API keys are not the preferred pattern (too easy to leak) |
| q_auth_08 | 75% | 4 | 180 | every auth event logged: login success/failure, MFA events, token operations, permission denied |

### tight

Mean recall: **69%** | Avg tokens: **257** | ≥80% recall: **9/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 50% | 10 | 337 | scales horizontally; sessions were a problem when adding more instances |
| q_api_02 | 100% | 7 | 248 |  |
| q_api_03 | 40% | 10 | 375 | HttpOnly cookies; SameSite=Strict (+1 more) |
| q_api_04 | 67% | 6 | 220 | originally three roles: admin, member, viewer |
| q_api_05 | 100% | 6 | 224 |  |
| q_api_06 | 67% | 5 | 227 | all five fields required |
| q_api_07 | 40% | 5 | 196 | v1 and v2 can coexist; minimum 12 months deprecation notice (+1 more) |
| q_api_08 | 100% | 5 | 208 |  |
| q_pipe_01 | 0% | 5 | 205 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 60% | 7 | 264 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 8 | 323 |  |
| q_pipe_04 | 50% | 3 | 153 | event deduplication (30-second window); schema validation against Avro schemas |
| q_pipe_05 | 100% | 6 | 257 |  |
| q_pipe_06 | 60% | 4 | 167 | Prometheus + Grafana; three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 160 |  |
| q_auth_01 | 25% | 10 | 347 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+1 more) |
| q_auth_02 | 67% | 10 | 390 | access tokens: 15 minutes; opaque tokens can be instantly revoked |
| q_auth_03 | 60% | 5 | 206 | hardware keys and passkeys supported; SIM swapping makes SMS too weak |
| q_auth_04 | 80% | 9 | 333 | originally considered simple role-based system |
| q_auth_05 | 60% | 4 | 179 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 100% | 10 | 356 |  |
| q_auth_07 | 83% | 10 | 344 | API keys are not the preferred pattern (too easy to leak) |
| q_auth_08 | 75% | 4 | 185 | every auth event logged: login success/failure, MFA events, token operations, permission denied |
