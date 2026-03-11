# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** incremental  
**Run:** 20260309_113628  

## Extraction

| Transcript | Status | Nodes | Edges | LLM Calls | Turns | Time (s) |
|-----------|--------|-------|-------|-----------|-------|----------|
| project_api_design | ✓ | 94 | 75 | 18 | 18 | 134.71 |
| project_auth_system | ✗ `'fact'` | — | — | — | — | 125.84 |
| project_data_pipeline | ✓ | 101 | 99 | 21 | 21 | 162.17 |

**Total nodes:** 195  
**Total extraction time:** 422.7s  

## Retrieval Quality by Strategy

### default

Mean recall: **51%** | Avg tokens: **526** | ≥80% recall: **8/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 3 | 136 | JWT tokens; stateless (+2 more) |
| q_api_02 | 100% | 25 | 739 |  |
| q_api_03 | 100% | 14 | 446 |  |
| q_api_04 | 67% | 18 | 524 | originally three roles: admin, member, viewer |
| q_api_05 | 83% | 7 | 259 | hard cap at 100 items |
| q_api_06 | 0% | 25 | 732 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 40% | 3 | 134 | header versioning made caching impossible; minimum 12 months deprecation notice (+1 more) |
| q_api_08 | 25% | 25 | 758 | 100 per minute for unauthenticated requests; enforced at the gateway (Kong) (+1 more) |
| q_pipe_01 | 50% | 10 | 348 | moved away from JSON; schema evolution without breaking changes |
| q_pipe_02 | 80% | 25 | 728 | originally planned S3 |
| q_pipe_03 | 40% | 25 | 735 | evaluated Apache Iceberg and Hudi; ML team already has Spark clusters configured for Delta Lake (+1 more) |
| q_pipe_04 | 25% | 18 | 613 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+1 more) |
| q_pipe_05 | 100% | 9 | 336 |  |
| q_pipe_06 | 80% | 25 | 842 | Prometheus + Grafana |
| q_pipe_07 | 100% | 25 | 755 |  |
| q_auth_01 | 75% | 25 | 760 | non-starter for enterprise customers |
| q_auth_02 | 17% | 25 | 733 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+3 more) |
| q_auth_03 | 100% | 13 | 434 |  |
| q_auth_04 | 0% | 4 | 171 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 10 | 310 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 33% | 4 | 174 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+2 more) |
| q_auth_07 | 17% | 25 | 727 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+3 more) |
| q_auth_08 | 0% | 25 | 693 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
