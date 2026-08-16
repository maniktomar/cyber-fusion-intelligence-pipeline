# Databricks notebook source
# MAGIC %md
# MAGIC # Real-Time Cyber Fusion Streaming Pipeline
# MAGIC Attach this notebook to a Databricks cluster with the Kafka and Snowflake Spark connector libraries installed.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Widgets

# COMMAND ----------

dbutils.widgets.text("kafka_bootstrap_servers", "localhost:9092")
dbutils.widgets.text("security_events_topic", "security-events")
dbutils.widgets.text("checkpoint_base", "dbfs:/checkpoints/cyber-fusion")
dbutils.widgets.text("snowflake_database", "REALTIME_ANALYTICS")
dbutils.widgets.text("snowflake_schema", "CURATED")

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import BooleanType, IntegerType, LongType, StringType, StructField, StructType

# Events carry UTC offsets; pin the session so hour-of-day rules do not drift with cluster locale.
spark.conf.set("spark.sql.session.timeZone", "UTC")

security_event_schema = StructType([
    StructField("event_id", StringType(), False),
    StructField("event_time", StringType(), False),
    StructField("event_type", StringType(), True),
    StructField("action", StringType(), True),
    StructField("actor_id", StringType(), True),
    StructField("actor_email", StringType(), True),
    StructField("actor_role", StringType(), True),
    StructField("actor_department", StringType(), True),
    StructField("is_privileged_actor", BooleanType(), True),
    StructField("session_id", StringType(), True),
    StructField("source_ip", StringType(), True),
    StructField("source_country", StringType(), True),
    StructField("device_type", StringType(), True),
    StructField("user_agent", StringType(), True),
    StructField("target_system", StringType(), True),
    StructField("target_resource", StringType(), True),
    StructField("auth_method", StringType(), True),
    StructField("mfa_used", BooleanType(), True),
    StructField("outcome", StringType(), True),
    StructField("http_status", IntegerType(), True),
    StructField("bytes_out", LongType(), True),
    StructField("failed_attempts_1h", IntegerType(), True),
    StructField("request_rate_1m", IntegerType(), True),
    StructField("detection_source", StringType(), True),
    StructField("is_test_event", BooleanType(), True),
    StructField("source_system", StringType(), True),
])

# COMMAND ----------

raw = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", dbutils.widgets.get("kafka_bootstrap_servers"))
    .option("subscribe", dbutils.widgets.get("security_events_topic"))
    .option("startingOffsets", "latest")
    .option("failOnDataLoss", "false")
    .load()
)

events = raw.select(F.from_json(F.col("value").cast("string"), security_event_schema).alias("event")).select("event.*")

accepted_event_types = ["authentication", "data_access", "api_call", "privilege_change", "config_change"]
high_risk_countries = ["RU", "KP", "IR", "BY", "NG"]

clean = (
    events.withColumn("event_timestamp", F.to_timestamp("event_time"))
    .withColumn("ingestion_timestamp", F.current_timestamp())
    .withColumn("actor_email", F.lower(F.trim("actor_email")))
    .withColumn("event_type", F.lower(F.trim("event_type")))
    .withColumn("outcome", F.lower(F.trim("outcome")))
    .withColumn("target_system", F.lower(F.trim("target_system")))
    .withColumn("source_country", F.upper(F.trim("source_country")))
    .withColumn("event_hour", F.hour("event_timestamp"))
    .withColumn("quality_status", F.when(
        F.col("event_type").isin(accepted_event_types)
        & F.col("outcome").isin("success", "failure", "blocked")
        & (F.length(F.trim(F.coalesce(F.col("actor_id"), F.lit("")))) > 0)
        & F.col("target_system").isNotNull()
        & (F.col("bytes_out") >= 0)
        & F.col("event_timestamp").isNotNull(),
        "valid",
    ).otherwise("invalid"))
    .withColumn("threat_reason",
        F.when((F.col("event_type") == "authentication") & (F.col("outcome") == "failure") & (F.col("failed_attempts_1h") >= 10), "credential_brute_force")
        .when((F.col("event_type") == "privilege_change") & (F.col("outcome") == "success") & (~F.coalesce(F.col("is_privileged_actor"), F.lit(False))), "unauthorized_privilege_escalation")
        .when((F.col("event_type") == "data_access") & (F.col("bytes_out") >= 500000000), "large_data_egress")
        .when((F.col("event_type") == "api_call") & (F.col("request_rate_1m") >= 300), "api_abuse_burst")
        .when(F.col("source_country").isin(high_risk_countries) & (F.col("outcome") == "success"), "unusual_geo_access")
        .when((F.col("event_type") == "config_change") & (F.col("action") == "logging_disabled"), "audit_logging_disabled"))
    .withColumn("is_anomaly", F.col("threat_reason").isNotNull())
    .withColumn("severity",
        F.when(F.col("threat_reason").isin("unauthorized_privilege_escalation", "large_data_egress", "audit_logging_disabled"), "CRITICAL")
        .when(F.col("threat_reason").isin("credential_brute_force", "unusual_geo_access"), "HIGH")
        .when(F.col("threat_reason").isNotNull(), "MEDIUM")
        .otherwise("NONE"))
    .withColumn("severity_rank",
        F.when(F.col("severity") == "CRITICAL", 4)
        .when(F.col("severity") == "HIGH", 3)
        .when(F.col("severity") == "MEDIUM", 2)
        .otherwise(0))
)

metrics_1m = (
    clean.filter("quality_status = 'valid'")
    .withWatermark("event_timestamp", "10 minutes")
    .groupBy(F.window("event_timestamp", "1 minute"), "event_type", "target_system", "source_country")
    .agg(
        F.count("*").alias("event_count"),
        F.sum(F.when(F.col("outcome") == "failure", 1).otherwise(0)).alias("failure_count"),
        F.sum(F.when(F.col("outcome") == "blocked", 1).otherwise(0)).alias("blocked_count"),
        F.approx_count_distinct("actor_id").alias("unique_actors"),
        F.approx_count_distinct("source_ip").alias("unique_source_ips"),
        F.sum("bytes_out").alias("total_bytes_out"),
        F.sum(F.when(F.col("is_anomaly"), 1).otherwise(0)).alias("anomaly_count"),
        F.max("severity_rank").alias("max_severity_rank"),
    )
    .select(
        F.col("window.start").alias("window_start"),
        F.col("window.end").alias("window_end"),
        "event_type",
        "target_system",
        "source_country",
        "event_count",
        "failure_count",
        "blocked_count",
        F.round(F.col("failure_count") / F.col("event_count"), 4).alias("failure_rate"),
        "unique_actors",
        "unique_source_ips",
        "total_bytes_out",
        "anomaly_count",
        "max_severity_rank",
    )
)

# COMMAND ----------

sf_options = {
    "sfURL": dbutils.secrets.get("snowflake", "url"),
    "sfUser": dbutils.secrets.get("snowflake", "user"),
    "sfPassword": dbutils.secrets.get("snowflake", "password"),
    "sfDatabase": dbutils.widgets.get("snowflake_database"),
    "sfSchema": dbutils.widgets.get("snowflake_schema"),
    "sfWarehouse": dbutils.secrets.get("snowflake", "warehouse"),
    "sfRole": dbutils.secrets.get("snowflake", "role"),
}

def write_to_snowflake(table_name):
    def writer(batch_df, batch_id):
        if not batch_df.isEmpty():
            batch_df.withColumn("batch_id", F.lit(batch_id)).write.format("snowflake").options(**sf_options).option("dbtable", table_name).mode("append").save()
    return writer

# COMMAND ----------

clean.filter("quality_status = 'valid'").writeStream.foreachBatch(write_to_snowflake("FACT_SECURITY_EVENTS")).option("checkpointLocation", dbutils.widgets.get("checkpoint_base") + "/fact_security_events").trigger(processingTime="15 seconds").start()

clean.filter("quality_status = 'invalid'").writeStream.foreachBatch(write_to_snowflake("ERROR_SECURITY_EVENTS")).option("checkpointLocation", dbutils.widgets.get("checkpoint_base") + "/error_security_events").trigger(processingTime="15 seconds").start()

metrics_1m.writeStream.foreachBatch(write_to_snowflake("AGG_SECURITY_METRICS_1M")).option("checkpointLocation", dbutils.widgets.get("checkpoint_base") + "/agg_security_metrics_1m").outputMode("update").trigger(processingTime="15 seconds").start()
