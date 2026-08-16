Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:JAVA_HOME = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot" }
$env:PATH = "$env:JAVA_HOME\bin;C:\hadoop\bin;$env:PATH"
$env:SPARK_HOME = "$ProjectRoot\.venv\Lib\site-packages\pyspark"
$env:HADOOP_HOME = "C:\hadoop"
$env:PYSPARK_PYTHON = "$ProjectRoot\.venv\Scripts\python.exe"
$env:PYSPARK_DRIVER_PYTHON = "$ProjectRoot\.venv\Scripts\python.exe"

& ".\.venv\Lib\site-packages\pyspark\bin\spark-submit.cmd" `
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3,net.snowflake:spark-snowflake_2.12:2.16.0-spark_3.4,net.snowflake:snowflake-jdbc:3.16.1 `
  pyspark_jobs/streaming_security_pipeline.py --sink snowflake

