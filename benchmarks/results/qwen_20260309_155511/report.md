# Benchmark Report

**Model:** `qwen/qwen3.5-35b-a3b`  
**Config:** `qwen.yaml`  
**Mode:** full  
**Run:** 20260309_155511  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 42 | 24 | 349.11 |
| project_auth_system | ✗ `` | — | — | 600.03 |
| project_data_pipeline | ✓ | 50 | 17 | 281.67 |

**Total nodes:** 92  
**Total extraction time:** 1230.8s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **46%** | Avg tokens: **218** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 10 | 324 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 3 | 130 | asymmetric — secret is never shared |
| q_api_03 | 20% | 2 | 99 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 10 | 348 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 93 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 5 | 212 | all five fields required |
| q_api_07 | 60% | 4 | 158 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 100% | 5 | 204 |  |
| q_pipe_01 | 50% | 8 | 283 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 6 | 215 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 7 | 270 |  |
| q_pipe_04 | 75% | 10 | 319 | schema validation against Avro schemas |
| q_pipe_05 | 67% | 3 | 128 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 7 | 236 |  |
| q_pipe_07 | 100% | 4 | 142 |  |
| q_auth_01 | 0% | 7 | 231 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 17% | 2 | 94 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+3 more) |
| q_auth_03 | 0% | 10 | 362 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 6 | 207 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 7 | 251 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 5 | 195 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 361 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 3 | 143 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **46%** | Avg tokens: **223** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 10 | 332 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 3 | 139 | asymmetric — secret is never shared |
| q_api_03 | 20% | 2 | 108 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 9 | 331 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 102 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 5 | 221 | all five fields required |
| q_api_07 | 60% | 4 | 167 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 100% | 5 | 213 |  |
| q_pipe_01 | 50% | 8 | 292 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 5 | 198 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 7 | 279 |  |
| q_pipe_04 | 75% | 10 | 328 | schema validation against Avro schemas |
| q_pipe_05 | 67% | 3 | 137 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 7 | 245 |  |
| q_pipe_07 | 100% | 4 | 151 |  |
| q_auth_01 | 0% | 7 | 240 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 17% | 2 | 103 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+3 more) |
| q_auth_03 | 0% | 10 | 370 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 5 | 191 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 7 | 260 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 5 | 204 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 370 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 3 | 152 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### filtered

Mean recall: **46%** | Avg tokens: **230** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 10 | 339 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 3 | 146 | asymmetric — secret is never shared |
| q_api_03 | 20% | 2 | 114 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 9 | 338 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 109 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 5 | 228 | all five fields required |
| q_api_07 | 60% | 4 | 174 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 100% | 5 | 219 |  |
| q_pipe_01 | 50% | 8 | 299 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 5 | 205 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 7 | 286 |  |
| q_pipe_04 | 75% | 10 | 334 | schema validation against Avro schemas |
| q_pipe_05 | 67% | 3 | 144 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 7 | 251 |  |
| q_pipe_07 | 100% | 4 | 158 |  |
| q_auth_01 | 0% | 7 | 247 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 17% | 2 | 110 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+3 more) |
| q_auth_03 | 0% | 10 | 377 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 5 | 197 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 7 | 267 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 5 | 211 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 377 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 3 | 158 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### tight

Mean recall: **46%** | Avg tokens: **235** | ≥80% recall: **4/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 25% | 10 | 344 | stateless; scales horizontally (+1 more) |
| q_api_02 | 75% | 3 | 151 | asymmetric — secret is never shared |
| q_api_03 | 20% | 2 | 119 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 67% | 9 | 343 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 114 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 5 | 233 | all five fields required |
| q_api_07 | 60% | 4 | 178 | v1 and v2 can coexist; minimum 12 months deprecation notice |
| q_api_08 | 100% | 5 | 224 |  |
| q_pipe_01 | 50% | 8 | 304 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 60% | 5 | 209 | originally planned S3; Parquet format, partitioned by date and event_type |
| q_pipe_03 | 100% | 7 | 290 |  |
| q_pipe_04 | 75% | 10 | 339 | schema validation against Avro schemas |
| q_pipe_05 | 67% | 3 | 148 | Redis IS used for the online feature store (different access pattern — point lookups) |
| q_pipe_06 | 100% | 7 | 256 |  |
| q_pipe_07 | 100% | 4 | 163 |  |
| q_auth_01 | 0% | 7 | 252 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 17% | 2 | 115 | refresh tokens: 24 hours sliding window; maximum 30 days of continuous activity (+3 more) |
| q_auth_03 | 0% | 10 | 382 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 5 | 202 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 7 | 272 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 5 | 215 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 10 | 382 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 3 | 163 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
