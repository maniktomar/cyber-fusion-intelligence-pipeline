from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.streaming import StreamingQueryException
from pyspark.sql import functions as F

from pyspark_jobs.schemas import security_event_schema
from pyspark_jobs.transformations import (
    add_threat_flags,
    aggregate_security_metrics,
    clean_security_events,
    deduplicate_events,
)
from utils.config import load_config


FACT_SECURITY_COLUMNS = [
    "event_id",
    "event_time",
    "event_timestamp",
    "ingestion_timestamp",
    "event_type",
    "action",
    "actor_id",
    "actor_email",
    "actor_role",
    "actor_department",
    "is_privileged_actor",
    "session_id",
    "source_ip",
    "source_country",
    "device_type",
    "user_agent",
    "target_system",
    "target_resource",
    "auth_method",
    "mfa_used",
    "outcome",
    "http_status",
    "bytes_out",
    "failed_attempts_1h",
    "request_rate_1m",
    "detection_source",
    "is_test_event",
    "source_system",
    "event_hour",
    "is_valid_event_type",
    "is_valid_outcome",
    "is_valid_actor",
    "is_valid_target",
    "is_valid_bytes",
    "is_valid_status",
    "quality_status",
    "quality_failure_reason",
    "threat_reason",
    "is_anomaly",
    "severity",
    "severity_rank",
    "mitre_tactic",
    "topic",
    "kafka_partition",
    "kafka_offset",
    "kafka_timestamp",
    "batch_id",
]

AGG_SECURITY_COLUMNS = [
    "window_start",
    "window_end",
    "event_type",
    "target_system",
    "source_country",
    "event_count",
    "failure_count",
    "blocked_count",
    "failure_rate",
    "unique_actors",
    "unique_source_ips",
    "total_bytes_out",
    "anomaly_count",
    "max_severity_rank",
    "batch_id",
]

AGG_TABLE = "AGG_SECURITY_METRICS_1M"


def create_spark(app_name: str) -> SparkSession:
    spark = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.streaming.schemaInference", "false")
        # Events carry UTC offsets; pinning the session keeps event_hour (and the
        # off-hours detection rule) independent of the cluster's local timezone.
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark


def read_kafka_stream(spark: SparkSession, bootstrap_servers: str, topic: str) -> DataFrame:
    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap_servers)
        .option("subscribe", topic)
        .option("startingOffsets", "latest")
        .option("failOnDataLoss", "false")
        .load()
    )
    return raw.select(
        F.from_json(F.col("value").cast("string"), security_event_schema).alias("event"),
        F.col("topic"),
        F.col("partition").alias("kafka_partition"),
        F.col("offset").alias("kafka_offset"),
        F.col("timestamp").alias("kafka_timestamp"),
    ).select("event.*", "topic", "kafka_partition", "kafka_offset", "kafka_timestamp")


def snowflake_options(config: dict) -> dict[str, str]:
    sf = config["snowflake"]
    return {
        "sfURL": sf["url"],
        "sfUser": sf["user"],
        "sfPassword": sf["password"],
        "sfDatabase": sf["database"],
        "sfSchema": sf["schema"],
        "sfWarehouse": sf["warehouse"],
        "sfRole": sf["role"],
    }


def write_batch_to_snowflake(table_name: str, sf_options: dict[str, str]):
    def _writer(batch_df: DataFrame, batch_id: int) -> None:
        if batch_df.rdd.isEmpty():
            return
        columns = AGG_SECURITY_COLUMNS if table_name == AGG_TABLE else FACT_SECURITY_COLUMNS
        (
            batch_df.withColumn("batch_id", F.lit(batch_id))
            .select(*columns)
            .write.format("snowflake")
            .options(**sf_options)
            .option("dbtable", table_name)
            .mode("append")
            .save()
        )

    return _writer


def start_pipeline(sink: str = "console") -> None:
    config = load_config()
    spark = create_spark(config["spark"]["app_name"])
    watermark_delay = config["spark"]["watermark_delay"]
    events = read_kafka_stream(
        spark,
        config["kafka"]["bootstrap_servers"],
        config["kafka"]["security_events_topic"],
    )
    cleaned = clean_security_events(events, float(config["quality_rules"]["max_bytes_out"]))
    deduplicated = deduplicate_events(cleaned, watermark_delay)
    enriched = add_threat_flags(deduplicated)
    valid = enriched.filter(F.col("quality_status") == "valid")
    invalid = enriched.filter(F.col("quality_status") == "invalid")
    metrics = aggregate_security_metrics(valid, watermark_delay)

    checkpoint_base = config["spark"]["checkpoint_base"]
    trigger = config["spark"]["trigger_processing_time"]
    queries = []

    if sink == "snowflake":
        sf_options = snowflake_options(config)
        queries.extend(
            [
                valid.writeStream.foreachBatch(write_batch_to_snowflake("FACT_SECURITY_EVENTS", sf_options))
                .option("checkpointLocation", f"{checkpoint_base}/fact_security_events")
                .trigger(processingTime=trigger)
                .start(),
                invalid.writeStream.foreachBatch(write_batch_to_snowflake("ERROR_SECURITY_EVENTS", sf_options))
                .option("checkpointLocation", f"{checkpoint_base}/error_security_events")
                .trigger(processingTime=trigger)
                .start(),
                metrics.writeStream.foreachBatch(write_batch_to_snowflake(AGG_TABLE, sf_options))
                .option("checkpointLocation", f"{checkpoint_base}/agg_security_metrics_1m")
                .outputMode("update")
                .trigger(processingTime=trigger)
                .start(),
            ]
        )
    else:
        queries.append(
            metrics.writeStream.format("console")
            .outputMode("update")
            .option("truncate", "false")
            .trigger(processingTime=trigger)
            .start()
        )

    print(f"Started {len(queries)} streaming query/query group. Waiting for data...", flush=True)
    try:
        while True:
            active_queries = [query for query in queries if query.isActive]
            if not active_queries:
                for query in queries:
                    print(f"Query stopped: name={query.name}, id={query.id}, status={query.status}", flush=True)
                    if query.exception() is not None:
                        print("Streaming query failed. Check the exception below:", flush=True)
                        print(query.exception(), flush=True)
                        raise query.exception()
                print("No active streaming queries remain.", flush=True)
                break

            for query in active_queries:
                print(f"Query active: id={query.id}, status={query.status.get('message', 'running')}", flush=True)
            time.sleep(15)
    except StreamingQueryException as exc:
        print("Streaming query failed. Check the exception below:", flush=True)
        print(exc, flush=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the real-time cyber fusion streaming pipeline.")
    parser.add_argument("--sink", choices=["console", "snowflake"], default="console")
    args = parser.parse_args()
    start_pipeline(args.sink)
