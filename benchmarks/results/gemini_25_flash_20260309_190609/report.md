# Benchmark Report

**Model:** `models/gemini-2.5-flash`  
**Config:** `gemini_25_flash.yaml`  
**Mode:** full  
**Run:** 20260309_190609  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✗ `` | — | — | 12698.11 |
| project_auth_system | ✗ `` | — | — | 1962.82 |
| project_data_pipeline | ✓ | 87 | 84 | 120.48 |

**Total nodes:** 87  
**Total extraction time:** 14781.4s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **31%** | Avg tokens: **571** | ≥80% recall: **6/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 0% | 21 | 676 | RS256; private key on the auth service (+2 more) |
| q_api_03 | 20% | 13 | 453 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 0% | 4 | 167 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 7 | 246 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 25 | 815 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 24 | 769 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 25 | 811 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 17 | 570 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 766 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 794 |  |
| q_pipe_04 | 100% | 25 | 796 |  |
| q_pipe_05 | 100% | 25 | 789 |  |
| q_pipe_06 | 100% | 25 | 804 |  |
| q_pipe_07 | 100% | 25 | 804 |  |
| q_auth_01 | 0% | 25 | 774 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 0% | 25 | 819 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 0% | 0 | 0 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 7 | 253 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 12 | 430 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 3 | 127 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 25 | 794 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 21 | 671 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **31%** | Avg tokens: **575** | ≥80% recall: **6/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 0% | 20 | 659 | RS256; private key on the auth service (+2 more) |
| q_api_03 | 20% | 12 | 439 | 7-day refresh tokens; HttpOnly cookies (+2 more) |
| q_api_04 | 0% | 4 | 176 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 7 | 255 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 25 | 834 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 23 | 752 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 25 | 820 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 75% | 16 | 556 | moved away from JSON |
| q_pipe_02 | 80% | 25 | 774 | originally planned S3 |
| q_pipe_03 | 100% | 25 | 810 |  |
| q_pipe_04 | 100% | 25 | 805 |  |
| q_pipe_05 | 100% | 25 | 798 |  |
| q_pipe_06 | 100% | 25 | 813 |  |
| q_pipe_07 | 100% | 25 | 804 |  |
| q_auth_01 | 0% | 25 | 803 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 0% | 25 | 830 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 0% | 0 | 0 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 7 | 262 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 40% | 12 | 439 | 20 failed attempts in 24 hours: account locked; requires admin or email verification to unlock (+1 more) |
| q_auth_06 | 0% | 3 | 136 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 25 | 803 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 20 | 654 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
