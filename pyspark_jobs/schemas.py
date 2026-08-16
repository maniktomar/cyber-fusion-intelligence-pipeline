from pyspark.sql.types import BooleanType, IntegerType, LongType, StringType, StructField, StructType


security_event_schema = StructType(
    [
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
    ]
)
