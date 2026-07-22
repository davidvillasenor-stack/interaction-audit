from __future__ import annotations
"""
shared/snowflake_client.py — minimal Snowflake query helper (env-configured).

No credentials or account identifiers are stored here. Configure via environment:
  SNOWFLAKE_ACCOUNT   (required)   e.g. ORG-ACCOUNT
  SNOWFLAKE_USER      (optional; defaults to $USER)
  SNOWFLAKE_WAREHOUSE (optional)
  SNOWFLAKE_DATABASE  (optional; default DWH)
  SNOWFLAKE_ROLE      (optional)
  SNOWFLAKE_AUTHENTICATOR (optional; default externalbrowser / Okta SSO)

Usage:
    from shared.snowflake_client import query, query_one
    rows = query("select 1 as x")
"""
import os


def get_connection():
    import snowflake.connector
    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    if not account:
        raise RuntimeError("Set SNOWFLAKE_ACCOUNT (and run on VPN with SSO).")
    return snowflake.connector.connect(
        account=account,
        user=os.environ.get("SNOWFLAKE_USER", os.environ.get("USER", "")),
        authenticator=os.environ.get("SNOWFLAKE_AUTHENTICATOR", "externalbrowser"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "DWH"),
        role=os.environ.get("SNOWFLAKE_ROLE"),
    )


def query(sql: str, params: dict | None = None) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(_dict_cursor(conn))
        wh = os.environ.get("SNOWFLAKE_WAREHOUSE")
        if wh:
            cur.execute(f"USE WAREHOUSE {wh}")
        cur.execute(sql, params or {})
        return cur.fetchall()
    finally:
        conn.close()


def query_one(sql: str, params: dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def _dict_cursor(conn):
    from snowflake.connector import DictCursor
    return DictCursor
