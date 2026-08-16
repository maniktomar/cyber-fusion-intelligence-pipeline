from __future__ import annotations

import os

import snowflake.connector
from fastapi import FastAPI

from utils.config import load_config


app = FastAPI(title="Real-Time Security Metrics API")


def _connect():
    config = load_config()["snowflake"]
    return snowflake.connector.connect(
        account=config["account"],
        user=config["user"],
        password=config["password"],
        role=config["role"],
        warehouse=config["warehouse"],
        database=config["database"],
        schema="MARTS",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics/realtime")
def realtime_metrics() -> dict:
    sql = "SELECT * FROM VW_REALTIME_KPIS"
    with _connect() as conn:
        cursor = conn.cursor(snowflake.connector.DictCursor)
        try:
            cursor.execute(sql)
            row = cursor.fetchone()
            return row or {}
        finally:
            cursor.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("API_HOST", "0.0.0.0"), port=int(os.getenv("API_PORT", "8000")))

