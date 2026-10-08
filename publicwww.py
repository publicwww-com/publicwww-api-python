#!/usr/bin/env python3
"""A small client for the PublicWWW API - https://publicwww.com/docs/api/

Standard library only. Use it as a module:

    from publicwww import Client
    pw = Client()                       # key from $PUBLICWWW_KEY
    page = pw.search('"angular.min.js"', per_page=20)
    for row in pw.iter_all('"angular.min.js"'):
        print(row["domain"])

or from the command line:

    python3 publicwww.py account
    python3 publicwww.py search '"angular.min.js"' --per-page 20
    python3 publicwww.py export '"angular.min.js"' > sites.txt
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("PUBLICWWW_API", "https://api.publicwww.com")
USER_AGENT = "publicwww-api-python/1.0"


class PublicWWWError(Exception):
    """An error answer from the API: HTTP status, error code and message."""

    def __init__(self, status, code, message):
        super().__init__("%s %s: %s" % (status, code, message))
        self.status = status
        self.code = code
        self.message = message


class Client:
    def __init__(self, key=None, api=API, timeout=600):
        self.key = key or os.environ.get("PUBLICWWW_KEY", "")
        if not self.key:
            raise PublicWWWError(0, "missing_key", "set PUBLICWWW_KEY or pass key=")
        self.api = api.rstrip("/")
        self.timeout = timeout

    def _open(self, path, params=None):
        """GET a path. Waits out the rate limit; any other error is raised."""
        url = self.api + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        while True:
            req = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + self.key,
                "User-Agent": USER_AGENT,
            })
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read() or b"{}").get("error", {})
                except ValueError:
                    err = {}
                # Two different 429s: too_many_requests means "slow down and
                # repeat", quota_exceeded means "come back tomorrow".
                if e.code == 429 and err.get("code") == "too_many_requests":
                    wait = e.headers.get("Retry-After") or err.get("retry_after") or 10
                    time.sleep(int(wait))
                    continue
                raise PublicWWWError(e.code, err.get("code", "http_error"),
                                     err.get("message", str(e))) from None

    def account(self):
        """Plan, quota and limits. Spends nothing."""
        with self._open("/v1/account") as res:
            return json.load(res)

    def search(self, query, page=1, per_page=100, **params):
        """One page of results as a dict: total, truncated, results, ..."""
        params.update(query=query, page=page, per_page=per_page)
        with self._open("/v1/search", params) as res:
            return json.load(res)

    def iter_all(self, query, per_page=None, meta=None, **params):
        """Every row of a query your plan covers, streamed as NDJSON - nothing
        is held in memory, and it is one search, not one per page.

        per_page defaults to the plan's max_per_page (asking for more is a 400
        per_page_too_large). Pass a dict as meta to receive the metadata line
        (total, returned, truncated)."""
        if per_page is None:
            per_page = self.account()["limits"]["max_per_page"]
        params.update(query=query, per_page=per_page, format="ndjson")
        with self._open("/v1/search", params) as res:
            for line in res:
                if not line.strip():
                    continue
                obj = json.loads(line)
                if obj.get("object") == "meta":     # always the first line
                    if meta is not None:
                        meta.update(obj)
                    continue
                yield obj


def main(argv=None):
    ap = argparse.ArgumentParser(description="PublicWWW API from the command line.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("account", help="plan, quota and limits")
    s = sub.add_parser("search", help="one page of results")
    s.add_argument("query")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--per-page", type=int, default=20)
    e = sub.add_parser("export", help="every result: one url per line, or --csv")
    e.add_argument("query")
    e.add_argument("--csv", action="store_true", help="domain;url;rank instead of urls")
    a = ap.parse_args(argv)

    try:
        pw = Client()
        if a.cmd == "account":
            print(json.dumps(pw.account(), indent=2))
        elif a.cmd == "search":
            page = pw.search(a.query, page=a.page, per_page=a.per_page)
            print("%d sites match, page %d of %d%s" % (
                page["total"], page["page"], page["total_pages"],
                " (cut short by the plan)" if page["truncated"] else ""))
            for r in page["results"]:
                print("%s\t%s\t%s" % ("" if r["rank"] is None else r["rank"], r["domain"], r["url"]))
        else:
            meta = {}
            for r in pw.iter_all(a.query, meta=meta):
                if a.csv:
                    print("%s;%s;%s" % (r["domain"], r["url"], "" if r["rank"] is None else r["rank"]))
                else:
                    print(r["url"])
            if meta.get("truncated"):
                print("note: the plan returned %d of %d" % (meta["returned"], meta["total"]), file=sys.stderr)
    except PublicWWWError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
