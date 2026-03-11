# Project: Data Pipeline Architecture Session
# Participants: Riley (data engineering lead), Morgan (ML engineer), Casey (infrastructure)

---

**Riley**: We need to design the new event ingestion pipeline. We're getting around 50k events per second at peak and our current Kafka setup is struggling.

**Morgan**: What's the actual bottleneck? Is it Kafka itself or our consumers?

**Riley**: Consumers. We have 12 consumer instances and they can't keep up with deserialization. Events are JSON right now.

**Casey**: Have we considered switching to a binary format? Protobuf or Avro would cut deserialization time significantly.

**Riley**: Yes, and we're going to do it. Decision: we're moving to Avro with a schema registry. Confluent Schema Registry. That gives us schema evolution without breaking changes.

**Morgan**: Which serialization library? fastavro or the official confluent-kafka Python client?

**Riley**: fastavro. Benchmarks show it's 3x faster than the official confluent library for our event shapes.

**Casey**: What's our retention policy on Kafka topics?

**Riley**: 7 days for raw events. 30 days for the processed events topic. After that, everything lands in cold storage.

**Morgan**: What cold storage are we using?

**Riley**: Originally we planned S3, but we're switching to GCS. The ML team's training infrastructure is already on GCP and the egress costs for cross-cloud are too high.

**Casey**: So GCS for cold storage. What format there?

**Riley**: Parquet. Partitioned by date and event_type. Column-oriented makes the ML queries much cheaper.

**Morgan**: How are we handling schema evolution in Parquet? If we add fields later?

**Riley**: Backward-compatible only changes. New optional fields with defaults. We use Delta Lake on top of GCS so we get ACID transactions and schema enforcement.

**Casey**: That's a significant dependency. Have we evaluated Delta Lake alternatives?

**Riley**: We looked at Apache Iceberg and Hudi. Decision: Delta Lake wins because the ML team already has Spark clusters configured for it. Switching would be 6+ weeks of work.

**Morgan**: What's the processing framework for the transformation layer?

**Riley**: Apache Flink for streaming transforms. We need exactly-once semantics and Flink handles that better than Spark Streaming for our latency requirements.

**Casey**: What's the target latency from event ingestion to processed output?

**Riley**: Under 500ms for the hot path. The cold path (batch aggregations) can be up to 5 minutes.

**Morgan**: What are the hot path transforms we need?

**Riley**: Three things: (1) PII scrubbing — we have to mask emails and phone numbers before anything leaves the ingestion layer. (2) Event deduplication — 30-second window, we see about 2% duplicate events. (3) Schema validation against the Avro schemas.

**Casey**: Where does PII scrubbing happen? In the Kafka consumer or a Flink job?

**Riley**: In the Kafka consumer, before the event hits any downstream topic. PII must never appear in downstream topics. That's a compliance requirement from legal.

**Morgan**: How are we handling the deduplication state? That's a lot of state to maintain at 50k events/sec.

**Riley**: RocksDB state backend in Flink. It's embedded, so no external service dependency. We evaluated Redis but the network overhead was adding 20ms per event at scale.

**Casey**: What's our disaster recovery strategy?

**Riley**: Kafka replication factor of 3, minimum in-sync replicas of 2. GCS cross-region replication for cold storage. Flink savepoints every 10 minutes for checkpoint-based recovery.

**Morgan**: One thing I need for ML training — we need a feature store. Where does that fit in?

**Riley**: Feast on top of the processed events in GCS. Redis for the online feature store (low-latency serving), GCS/BigQuery for offline features.

**Casey**: Wait, I thought we decided against Redis earlier?

**Riley**: For deduplication state inside Flink, yes. For the online feature store serving ML features, Redis is the right tool — different latency profile and the access pattern is point lookups, not streaming state.

**Morgan**: What's the SLA on the feature store?

**Riley**: Online features: 10ms p99. Offline features: no real-time SLA, batch jobs can take up to 2 hours.

**Casey**: Monitoring stack?

**Riley**: Prometheus + Grafana for metrics. We'll emit Kafka consumer lag, Flink checkpoint duration, and end-to-end latency as the three golden signals. Alerts go to PagerDuty for anything breaching the 500ms latency SLA.

**Morgan**: What about data quality checks?

**Riley**: Great Expectations for data quality assertions. We'll validate at the output of each Flink transform. Failed assertions trigger a DLQ (dead letter queue) — events go to a separate Kafka topic for manual review.

**Casey**: Last question: deployment. Are we containerizing Flink?

**Riley**: Yes, on Kubernetes. We're using the Flink Kubernetes Operator. Auto-scaling based on Kafka consumer lag metric exposed to the HPA.
