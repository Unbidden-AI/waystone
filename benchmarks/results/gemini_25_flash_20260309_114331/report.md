# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** buffered  
**Run:** 20260309_114331  

## Extraction

| Transcript | Status | Nodes | Edges | LLM Calls | Turns | Time (s) |
|-----------|--------|-------|-------|-----------|-------|----------|
| project_api_design | ✓ | 67 | 47 | 3 | 18 | 76.12 |
| project_auth_system | ✗ `'fact'` | — | — | — | — | 42.2 |
| project_data_pipeline | ✓ | 85 | 66 | 4 | 21 | 78.02 |

**Total nodes:** 152  
**Total extraction time:** 196.3s  

## Retrieval Quality by Strategy

### default

Mean recall: **47%** | Avg tokens: **508** | ≥80% recall: **7/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 75% | 25 | 782 | scales horizontally |
| q_api_02 | 100% | 20 | 571 |  |
| q_api_03 | 80% | 20 | 595 | 7-day refresh tokens |
| q_api_04 | 67% | 9 | 285 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 246 | hard cap at 100 items |
| q_api_06 | 100% | 25 | 751 |  |
| q_api_07 | 100% | 25 | 861 |  |
| q_api_08 | 75% | 25 | 814 | enforced at the gateway (Kong) |
| q_pipe_01 | 75% | 13 | 446 | moved away from JSON |
| q_pipe_02 | 0% | 3 | 123 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 23 | 717 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 11 | 363 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 100% | 25 | 825 |  |
| q_pipe_06 | 20% | 5 | 202 | under 500ms for the hot path; cold path up to 5 minutes (+2 more) |
| q_pipe_07 | 25% | 2 | 116 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+1 more) |
| q_auth_01 | 100% | 13 | 403 |  |
| q_auth_02 | 33% | 23 | 715 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+2 more) |
| q_auth_03 | 0% | 7 | 224 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 10 | 312 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 18 | 631 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 17% | 21 | 743 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+3 more) |
| q_auth_07 | 0% | 25 | 861 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 2 | 96 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
