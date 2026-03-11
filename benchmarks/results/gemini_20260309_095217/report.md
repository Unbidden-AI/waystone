# Benchmark Report

**Model:** `gemini-3-flash-preview`  
**Config:** `gemini.yaml`  
**Run:** 20260309_095217  

## Extraction

| Transcript | Status | Nodes | Edges | Time (s) |
|-----------|--------|-------|-------|----------|
| project_api_design | ✗ `Client error '400 Bad Request' for url '` | — | — | 0.3 |
| project_auth_system | ✗ `Client error '400 Bad Request' for url '` | — | — | 0.23 |
| project_data_pipeline | ✗ `Client error '400 Bad Request' for url '` | — | — | 0.26 |

**Total nodes:** 0  
**Total extraction time:** 0.8s  

## Retrieval Quality by Strategy

### baseline

Mean recall: **0%** | Avg tokens: **0** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 0% | 0 | 0 | RS256; private key on the auth service (+2 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 0 | 0 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 0 | 0 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 0% | 0 | 0 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 0% | 0 | 0 | under 500ms for the hot path; cold path up to 5 minutes (+3 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 0% | 0 | 0 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 0% | 0 | 0 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 0 | 0 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### default

Mean recall: **0%** | Avg tokens: **0** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 0% | 0 | 0 | RS256; private key on the auth service (+2 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 0 | 0 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 0 | 0 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 0% | 0 | 0 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 0% | 0 | 0 | under 500ms for the hot path; cold path up to 5 minutes (+3 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 0% | 0 | 0 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 0% | 0 | 0 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 0 | 0 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### filtered

Mean recall: **0%** | Avg tokens: **0** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 0% | 0 | 0 | RS256; private key on the auth service (+2 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 0 | 0 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 0 | 0 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 0% | 0 | 0 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 0% | 0 | 0 | under 500ms for the hot path; cold path up to 5 minutes (+3 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 0% | 0 | 0 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 0% | 0 | 0 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 0 | 0 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |

### tight

Mean recall: **0%** | Avg tokens: **0** | ≥80% recall: **0/23**

| ID | Recall | Nodes | Tokens | Missed elements |
|----|--------|-------|--------|-----------------|
| q_api_01 | 0% | 0 | 0 | JWT tokens; stateless (+2 more) |
| q_api_02 | 0% | 0 | 0 | RS256; private key on the auth service (+2 more) |
| q_api_03 | 0% | 0 | 0 | 15-minute expiry for access tokens; 7-day refresh tokens (+3 more) |
| q_api_04 | 0% | 0 | 0 | admin, member, viewer, guest; originally three roles: admin, member, viewer (+1 more) |
| q_api_05 | 0% | 0 | 0 | cursor-based pagination; offset pagination breaks when rows are inserted (+4 more) |
| q_api_06 | 0% | 0 | 0 | RFC 7807 Problem Details; type, title, status, detail, instance (+1 more) |
| q_api_07 | 0% | 0 | 0 | URL versioning (not header versioning); header versioning made caching impossible (+3 more) |
| q_api_08 | 0% | 0 | 0 | 1000 requests per minute per user token; 100 per minute for unauthenticated requests (+2 more) |
| q_pipe_01 | 0% | 0 | 0 | Avro with Confluent Schema Registry; moved away from JSON (+2 more) |
| q_pipe_02 | 0% | 0 | 0 | GCS (Google Cloud Storage); originally planned S3 (+3 more) |
| q_pipe_03 | 0% | 0 | 0 | Delta Lake; evaluated Apache Iceberg and Hudi (+3 more) |
| q_pipe_04 | 0% | 0 | 0 | PII scrubbing (mask emails and phone numbers); event deduplication (30-second window) (+2 more) |
| q_pipe_05 | 0% | 0 | 0 | Redis rejected for Flink deduplication state — network overhead added 20ms per event; RocksDB used instead for embedded state (+1 more) |
| q_pipe_06 | 0% | 0 | 0 | under 500ms for the hot path; cold path up to 5 minutes (+3 more) |
| q_pipe_07 | 0% | 0 | 0 | Kafka replication factor of 3; minimum in-sync replicas of 2 (+2 more) |
| q_auth_01 | 0% | 0 | 0 | compliance requires data residency in own infrastructure; Auth0 and Okta store user data outside company control (+2 more) |
| q_auth_02 | 0% | 0 | 0 | access tokens: 15 minutes; refresh tokens: 24 hours sliding window (+4 more) |
| q_auth_03 | 0% | 0 | 0 | TOTP as primary second factor; WebAuthn/FIDO2 as preferred option (+3 more) |
| q_auth_04 | 0% | 0 | 0 | ABAC (Attribute-Based Access Control) — not just RBAC; Open Policy Agent (OPA) as policy engine (+3 more) |
| q_auth_05 | 0% | 0 | 0 | 5 failed attempts: 5-minute lockout; 10 failed attempts: 1-hour lockout (+3 more) |
| q_auth_06 | 0% | 0 | 0 | minimum 12 characters; must include uppercase, lowercase, digit, special character (+4 more) |
| q_auth_07 | 0% | 0 | 0 | service accounts in Keycloak using client credentials flow; API keys are not the preferred pattern (too easy to leak) (+4 more) |
| q_auth_08 | 0% | 0 | 0 | every auth event logged: login success/failure, MFA events, token operations, permission denied; logs are immutable, append-only (+2 more) |
