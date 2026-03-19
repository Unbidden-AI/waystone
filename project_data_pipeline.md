The event ingestion pipeline originally planned S3 for cold storage but switched to GCS due to high cross-cloud egress costs and existing ML team infrastructure on GCP.
All events will be moved to cold storage after their Kafka retention period.
Parquet will be the format for data in GCS cold storage, partitioned by date and event_type. Using a column-oriented format like Parquet makes ML queries much cheaper.
Delta Lake will be used on top of GCS for cold storage. Delta Lake provides ACID transactions and schema enforcement.
PII scrubbing happens in the Kafka consumer. PII must never appear in downstream topics.
GCS cross-region replication is used for cold storage disaster recovery.