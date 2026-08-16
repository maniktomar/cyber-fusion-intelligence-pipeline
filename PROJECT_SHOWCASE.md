# Project Showcase

## Real-Time Cyber Fusion Intelligence Pipeline

This project demonstrates a production-style streaming analytics platform for security-oriented operational events. It combines Kafka, PySpark Structured Streaming, Snowflake, Power BI, and an AI-style threat triage layer.

## Business Problem

Security and operations teams need timely, reliable visibility into event streams such as failed sign-ins, unusual access activity, privilege changes, and API anomalies. Raw event data is often incomplete, duplicated, late, or inconsistent, and batch dashboards miss fast-moving activity.

## Solution

The platform ingests security events in real time, validates and enriches them with Spark, applies deterministic detection rules with severity and MITRE ATT&CK mapping, stores curated facts and aggregates in Snowflake, and presents operational KPIs in Power BI. An AI insights layer converts threat indicators into risk scores, explanations, recommended actions, and triage queue routing.

## Architecture Highlights

- Event simulation with a Python Kafka producer covering authentication, data access, API, privilege, and config-change events.
- Kafka topic for streaming ingestion, keyed by actor for per-identity ordering.
- PySpark Structured Streaming for parsing, cleaning, deduplication, watermarking, threat tagging, and one-minute aggregation.
- Quarantine path so invalid records are captured with a failure reason rather than dropped.
- Snowflake curated tables and BI-friendly mart views, including a data-quality scorecard.
- Power BI dashboard pages for KPIs, threat activity, data quality, egress, and AI insights.
- Dockerized local Kafka stack.
- CI test workflow with GitHub Actions, including Spark batch tests of the detection rules.

## What Makes It Stand Out

- Real-time streaming design rather than static log analysis.
- End-to-end journey from event generation to BI dashboard.
- Detection rules mapped to MITRE ATT&CK tactics and driven by configuration, not hardcoded constants.
- Separate injection of threat patterns and data-quality defects, so both detection and quarantine coverage are demonstrable.
- Snowflake schema design with fact, quarantine, aggregate, and AI insight tables.
- Detection and scoring logic under automated test with a local Spark session.

## Interview Talking Points

- How Kafka decouples producers from streaming consumers, and why actor-keyed partitioning matters for identity analytics.
- Why Spark Structured Streaming checkpoints, watermarks, and deduplication matter for at-least-once sources.
- Why the session timezone is pinned to UTC, and how a local-timezone cluster would have silently shifted the off-hours detection rule.
- How invalid events are quarantined with a reason instead of dropped, and how that feeds the quality scorecard.
- How Snowflake mart views simplify Power BI reporting.
- How the AI insight layer can be swapped from rules-based explanations to an LLM provider.
- How this would scale with Schema Registry, threat intelligence enrichment, stateful session analytics, orchestration, and IaC.

## Resume Bullet

Developed a real-time security analytics platform using Kafka, PySpark Structured Streaming, Snowflake, Power BI, and AI-generated threat insights to ingest, validate, detect, and explain streaming security event data with MITRE ATT&CK-mapped indicators and automated triage routing.

## Scope Note

Events are synthetic and generated locally. Detection rules produce prioritised indicators for analyst review; the project is not a SIEM replacement or a confirmed-detection engine.
