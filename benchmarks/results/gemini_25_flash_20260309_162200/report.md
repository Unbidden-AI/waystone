# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_162200  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✓ | 72 | 35 | 75.28 |
| project_auth_system | ✓ | 133 | 63 | 65.55 |
| project_data_pipeline | ✓ | 114 | 89 | 83.01 |

**Total nodes:** 319  
**Total extraction time:** 223.8s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **75%** | Avg tokens: **468** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 152 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 7 | 228 | asymmetric — secret is never shared |
| q_api_03 | 100% | 25 | 719 |  |
| q_api_04 | 100% | 8 | 259 |  |
| q_api_05 | 33% | 2 | 101 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 16 | 462 | all five fields required |
| q_api_07 | 80% | 9 | 312 | v1 and v2 can coexist |
| q_api_08 | 100% | 8 | 267 |  |
| q_pipe_01 | 75% | 22 | 691 | moved away from JSON |
| q_pipe_02 | 100% | 25 | 762 |  |
| q_pipe_03 | 60% | 25 | 771 | ML team already has Spark clusters configured for Delta Lake; switching would take 6+ weeks |
| q_pipe_04 | 25% | 5 | 198 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 23 | 746 |  |
| q_pipe_06 | 100% | 23 | 719 |  |
| q_pipe_07 | 100% | 5 | 178 |  |
| q_auth_01 | 50% | 25 | 721 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 83% | 25 | 674 | access tokens: 15 minutes |
| q_auth_03 | 100% | 22 | 632 |  |
| q_auth_04 | 60% | 25 | 736 | originally considered simple role-based system; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 60% | 5 | 180 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 394 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 18 | 543 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 315 |  |

### default

Mean recall: **75%** | Avg tokens: **449** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 161 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 6 | 215 | asymmetric — secret is never shared |
| q_api_03 | 100% | 25 | 720 |  |
| q_api_04 | 67% | 7 | 236 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 109 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 16 | 471 | all five fields required |
| q_api_07 | 80% | 8 | 295 | v1 and v2 can coexist |
| q_api_08 | 100% | 8 | 276 |  |
| q_pipe_01 | 75% | 19 | 607 | moved away from JSON |
| q_pipe_02 | 80% | 22 | 675 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 785 |  |
| q_pipe_04 | 25% | 4 | 167 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 20 | 647 |  |
| q_pipe_06 | 100% | 19 | 602 |  |
| q_pipe_07 | 100% | 4 | 155 |  |
| q_auth_01 | 50% | 25 | 730 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 83% | 25 | 683 | access tokens: 15 minutes |
| q_auth_03 | 100% | 22 | 641 |  |
| q_auth_04 | 60% | 25 | 729 | originally considered simple role-based system; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 60% | 5 | 189 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 403 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 16 | 503 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 324 |  |

### filtered

Mean recall: **75%** | Avg tokens: **456** | ≥80% recall: **13/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 168 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 6 | 221 | asymmetric — secret is never shared |
| q_api_03 | 100% | 25 | 727 |  |
| q_api_04 | 67% | 7 | 243 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 116 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 16 | 478 | all five fields required |
| q_api_07 | 80% | 8 | 301 | v1 and v2 can coexist |
| q_api_08 | 100% | 8 | 283 |  |
| q_pipe_01 | 75% | 19 | 613 | moved away from JSON |
| q_pipe_02 | 80% | 22 | 681 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 792 |  |
| q_pipe_04 | 25% | 4 | 174 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 20 | 654 |  |
| q_pipe_06 | 100% | 19 | 609 |  |
| q_pipe_07 | 100% | 4 | 162 |  |
| q_auth_01 | 50% | 25 | 737 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 83% | 25 | 689 | access tokens: 15 minutes |
| q_auth_03 | 100% | 22 | 648 |  |
| q_auth_04 | 60% | 25 | 736 | originally considered simple role-based system; OPA policies in Git, deployed as sidecar containers |
| q_auth_05 | 60% | 5 | 196 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 410 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 16 | 509 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 330 |  |

### tight

Mean recall: **68%** | Avg tokens: **402** | ≥80% recall: **10/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 4 | 172 | JWT tokens; stateless (+2 more) |
| q_api_02 | 75% | 6 | 226 | asymmetric — secret is never shared |
| q_api_03 | 60% | 19 | 563 | SameSite=Strict; not localStorage due to XSS risk |
| q_api_04 | 67% | 7 | 247 | originally three roles: admin, member, viewer |
| q_api_05 | 33% | 2 | 121 | cursor-based pagination; offset pagination breaks when rows are inserted (+2 more) |
| q_api_06 | 67% | 16 | 483 | all five fields required |
| q_api_07 | 80% | 8 | 306 | v1 and v2 can coexist |
| q_api_08 | 100% | 8 | 287 |  |
| q_pipe_01 | 75% | 17 | 567 | moved away from JSON |
| q_pipe_02 | 80% | 18 | 575 | originally planned S3 |
| q_pipe_03 | 60% | 18 | 583 | ML team already has Spark clusters configured for Delta Lake; switching would take 6+ weeks |
| q_pipe_04 | 25% | 4 | 179 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 17 | 583 |  |
| q_pipe_06 | 80% | 17 | 555 | three golden signals: Kafka consumer lag, Flink checkpoint duration, end-to-end latency |
| q_pipe_07 | 100% | 4 | 167 |  |
| q_auth_01 | 50% | 18 | 533 | compliance requires data residency in own infrastructure; non-starter for enterprise customers |
| q_auth_02 | 83% | 19 | 546 | access tokens: 15 minutes |
| q_auth_03 | 60% | 18 | 549 | WebAuthn/FIDO2 as preferred option; hardware keys and passkeys supported |
| q_auth_04 | 40% | 17 | 550 | Open Policy Agent (OPA) as policy engine; originally considered simple role-based system (+1 more) |
| q_auth_05 | 60% | 5 | 201 | requires admin or email verification to unlock; IP-level rate limiting also applied |
| q_auth_06 | 83% | 12 | 414 | rotation enforced only if breach detected via HIBP |
| q_auth_07 | 83% | 16 | 514 | mTLS between internal services where possible |
| q_auth_08 | 100% | 12 | 335 |  |
