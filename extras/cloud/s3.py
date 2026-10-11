"""Minimal S3 client for Vultr Object Storage: AWS Signature V4 PUT/GET, stdlib only, path-style URLs.

    VULTR_S3_ENDPOINT    https://ewr1.vultrobjects.com   (the hostname Vultr shows for the bucket's region)
    VULTR_S3_BUCKET      here-exports
    VULTR_S3_ACCESS_KEY  / VULTR_S3_SECRET_KEY            (extras/cloud/.env; never logged)

Vultr ignores the region name in the signature, so "us-east-1" is used unless VULTR_S3_REGION says otherwise.
"""

import hashlib
import hmac
import os
import time
import urllib.request
from urllib.parse import quote, unquote, urlsplit

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signing_key(secret, date, region, service="s3"):
    k = _hmac(("AWS4" + secret).encode(), date)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    return k


def sign(method, url, headers, payload_hash, access_key, secret_key, region="us-east-1", amz_date=None,
         service="s3"):
    """Return `headers` plus x-amz-date, x-amz-content-sha256 and Authorization for this request."""
    amz_date = amz_date or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    parts = urlsplit(url)
    h = {k.lower(): str(v).strip() for k, v in headers.items()}
    h["host"] = parts.netloc
    h["x-amz-date"] = amz_date
    h["x-amz-content-sha256"] = payload_hash
    names = sorted(h)
    query = "&".join(sorted(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                            for k, _, v in (p.partition("=") for p in parts.query.split("&") if p)))
    path = quote(unquote(parts.path or "/"), safe="/-_.~")          # S3 encodes the path once, never twice
    canonical = "\n".join([method, path, query,
                           "".join(f"{n}:{h[n]}\n" for n in names), ";".join(names), payload_hash])
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    sig = hmac.new(signing_key(secret_key, amz_date[:8], region, service), to_sign.encode(),
                   hashlib.sha256).hexdigest()
    h["authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, "
                          f"Signature={sig}")
    return h


class S3:
    def __init__(self, endpoint, bucket, access_key, secret_key, region="us-east-1", timeout=30):
        self.endpoint = endpoint.rstrip("/")
        if "://" not in self.endpoint:
            self.endpoint = "https://" + self.endpoint
        self.bucket, self.access_key, self.secret_key = bucket, access_key, secret_key
        self.region, self.timeout = region, timeout

    @classmethod
    def from_env(cls):
        """None unless all four VULTR_S3_* variables are set (uploads are optional)."""
        e = {k: os.environ.get(f"VULTR_S3_{k}", "").strip() for k in ("ENDPOINT", "BUCKET", "ACCESS_KEY", "SECRET_KEY")}
        if not all(e.values()):
            return None
        return cls(e["ENDPOINT"], e["BUCKET"], e["ACCESS_KEY"], e["SECRET_KEY"],
                   os.environ.get("VULTR_S3_REGION", "us-east-1"))

    def url(self, key):
        return f"{self.endpoint}/{self.bucket}/{quote(key, safe='/-_.~')}"

    def request(self, method, key, body=b"", content_type=None):
        headers = {"content-type": content_type} if content_type else {}
        url = self.url(key)
        h = sign(method, url, headers, hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256,
                 self.access_key, self.secret_key, self.region)
        h.pop("host")
        req = urllib.request.Request(url, data=body if method == "PUT" else None, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status, r.read()

    def put(self, key, body, content_type="application/octet-stream"):
        return self.request("PUT", key, body, content_type)[0]

    def get(self, key):
        return self.request("GET", key)[1]
