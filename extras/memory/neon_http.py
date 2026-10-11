"""Neon over HTTPS (port 443) for networks that block Postgres' port 5432 (Adil's Wi-Fi does; campus may too).

Neon serves SQL at https://<endpoint-host>/sql (the transport its serverless driver uses). This is a tiny DB-API
style shim so `store.Store` runs unchanged: SELECTs run immediately; writes are queued and sent at `commit()` as
one batch, which Neon runs as a single transaction. Stdlib only.
"""

import json
import urllib.error
import urllib.parse
import urllib.request


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self.rows = []
        self.rowcount = -1

    def execute(self, sql, params=()):
        q = {"query": sql, "params": [p for p in params]}
        if sql.lstrip()[:6].upper() in ("SELECT", "WITH S"):
            res = self.conn.post(q)
            self.description = [(f["name"],) for f in res["fields"]]
            self.rows = res["rows"]
            self.rowcount = res.get("rowCount", len(self.rows))
        else:
            self.conn.pending.append(q)

    def fetchall(self):
        return self.rows


class NeonHTTPConnection:
    def __init__(self, url, timeout=10.0):
        self.url = url
        self.host = urllib.parse.urlsplit(url).hostname
        self.timeout = timeout
        self.pending = []

    def post(self, body):
        req = urllib.request.Request(f"https://{self.host}/sql", json.dumps(body).encode(), {
            "Content-Type": "application/json", "Neon-Connection-String": self.url, "Neon-Array-Mode": "true"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:              # the body says which statement failed; never the URL
            raise RuntimeError(f"neon http {e.code}: {e.read()[:300].decode(errors='replace')}") from None

    def cursor(self):
        return Cursor(self)

    def commit(self):
        if self.pending:
            batch, self.pending = self.pending, []
            self.post({"queries": batch})

    def rollback(self):
        self.pending = []

    def close(self):
        self.pending = []
