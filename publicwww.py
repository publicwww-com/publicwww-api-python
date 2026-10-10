#!/usr/bin/env python3
"""A small client for the PublicWWW API - https://publicwww.com/docs/api/

Standard library only. Use it as a module:

    from publicwww import Client
    pw = Client()                       # key from $PUBLICWWW_KEY
    page = pw.search('"angular.min.js"', per_page=20)
    for row in pw.iter_all('"angular.min.js"'):
        print(row["domain"])

    c = pw.create_cluster(query='"googletagmanager.com/gtm.js"')
    for row in pw.iter_extract(c["id"], presets=["email", "phone"]):
        print(row["domain"], row["values"])

or from the command line:

    python3 publicwww.py account
    python3 publicwww.py search '"angular.min.js"' --per-page 20
    python3 publicwww.py export '"angular.min.js"' > sites.txt
    python3 publicwww.py cluster-create --query '"googletagmanager.com/gtm.js"'
    python3 publicwww.py extract 12 --presets email,phone > contacts.csv
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("PUBLICWWW_API", "https://api.publicwww.com")
USER_AGENT = "publicwww-api-python/1.1"


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

    def _open(self, path, params=None, body=None):
        """GET a path, or POST body as JSON when it is given. Waits out the
        rate limit; any other error is raised."""
        url = self.api + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": "Bearer " + self.key, "User-Agent": USER_AGENT}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        while True:
            req = urllib.request.Request(url, data=data, headers=headers)
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

    def _json(self, path, params=None, body=None):
        with self._open(path, params, body) as res:
            return json.load(res)

    def account(self):
        """Plan, quota and limits, clusters included. Spends nothing."""
        return self._json("/v1/account")

    def search(self, query, page=1, per_page=100, **params):
        """One page of results as a dict: total, truncated, results, ..."""
        params.update(query=query, page=page, per_page=per_page)
        return self._json("/v1/search", params)

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

    # ---- clusters: saved lists of sites, to extract data from or combine ----

    def clusters(self):
        """The account's clusters, newest first: count, max, clusters
        (id, name, size, created)."""
        return self._json("/v1/clusters")

    def cluster(self, cluster_id, page=1, per_page=1000):
        """One cluster and a page of its domains (per_page up to 10000)."""
        return self._json("/v1/clusters/%d" % int(cluster_id), {"page": page, "per_page": per_page})

    def iter_cluster(self, cluster_id, per_page=10000):
        """Every domain of a cluster, page after page."""
        page = 1
        while True:
            res = self.cluster(cluster_id, page=page, per_page=per_page)
            yield from res["domains"]
            if page >= res["total_pages"]:
                return
            page += 1

    def create_cluster(self, query=None, domains=None, name=None):
        """A new cluster from a search (query=, costs one search like
        search()) or from a list of domains or urls (domains=; only sites in
        the index are kept). 409 cluster_limit when the account has 100."""
        body = {"query": query} if query is not None else {"domains": list(domains or [])}
        if name:
            body["name"] = name
        return self._json("/v1/clusters", body=body)

    def combine_clusters(self, operation, cluster_ids, name=None):
        """A new cluster: "and" - domains in every cluster, "or" - in any,
        "diff" - in the first but not in the second (exactly two)."""
        body = {"operation": operation, "clusters": [int(i) for i in cluster_ids]}
        if name:
            body["name"] = name
        return self._json("/v1/clusters/combine", body=body)

    def cluster_presets(self):
        """Ready-made extraction expressions: presets (name, regex, about)."""
        return self._json("/v1/clusters/presets")

    def extract(self, cluster_id, presets=None, regex=None, offset=0, limit=1000, skip_empty=True):
        """One part of an extraction: domains [offset, offset + limit) of the
        cluster (limit up to 1000). presets are names ("email", "phone", ...),
        regex your own PCRE in slashes, the first group is the value. The
        answer has rows (domain, values - one list per expression, matches)
        and next_offset, None when the cluster is done. Costs extraction
        points."""
        body = {"presets": list(presets or []), "regex": list(regex or []),
                "offset": offset, "limit": limit, "skip_empty": skip_empty}
        return self._json("/v1/clusters/%d/extract" % int(cluster_id), body=body)

    def iter_extract(self, cluster_id, presets=None, regex=None, offset=0, limit=1000, skip_empty=True, meta=None):
        """Every row of an extraction over the whole cluster from offset on,
        part after part (following next_offset). Pass a dict as meta to
        receive the last part's numbers (regex, points_left, ...). If the
        day's extraction points run out midway, raises PublicWWWError
        extract_quota_exceeded; its message says the offset to resume from."""
        while offset is not None:
            part = self.extract(cluster_id, presets, regex, offset, limit, skip_empty)
            yield from part["rows"]
            if meta is not None:
                meta.update({k: v for k, v in part.items() if k != "rows"})
            if part.get("stopped") == "extract_quota_exceeded":
                raise PublicWWWError(429, "extract_quota_exceeded",
                                     "the day's extraction points ran out; resume with offset=%s" % part["next_offset"])
            offset = part["next_offset"]

    def rename_cluster(self, cluster_id, name):
        return self._json("/v1/clusters/%d/rename" % int(cluster_id), body={"name": name})

    def delete_cluster(self, cluster_id):
        """Deletes the cluster for good: {"deleted": id, "name": ...}."""
        return self._json("/v1/clusters/%d/delete" % int(cluster_id), body={})


def _cluster_line(c):
    return "%s\t%s\t%s\t%s" % (c["id"], c["size"], c["created"], c["name"])


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
    sub.add_parser("clusters", help="the account's clusters: id, size, created, name")
    c = sub.add_parser("cluster", help="every domain of a cluster, one per line")
    c.add_argument("id", type=int)
    c = sub.add_parser("cluster-create", help="a cluster from a search or from a file of domains")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--query")
    g.add_argument("--domains-file", help="one domain or url per line, - for stdin")
    c.add_argument("--name")
    c = sub.add_parser("cluster-combine", help="a new cluster from existing ones")
    c.add_argument("operation", choices=["and", "or", "diff"])
    c.add_argument("ids", type=int, nargs="+")
    c.add_argument("--name")
    sub.add_parser("presets", help="ready-made extraction expressions")
    x = sub.add_parser("extract", help="values from every site of a cluster as CSV")
    x.add_argument("id", type=int)
    x.add_argument("--presets", default="", help="comma-separated names, e.g. email,phone")
    x.add_argument("--regex", action="append", default=[], help="your own expression, repeatable")
    x.add_argument("--offset", type=int, default=0, help="resume here (no header line then)")
    c = sub.add_parser("cluster-rename", help="give a cluster a new name")
    c.add_argument("id", type=int)
    c.add_argument("name")
    c = sub.add_parser("cluster-delete", help="delete a cluster for good")
    c.add_argument("id", type=int)
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
        elif a.cmd == "export":
            meta = {}
            for r in pw.iter_all(a.query, meta=meta):
                if a.csv:
                    print("%s;%s;%s" % (r["domain"], r["url"], "" if r["rank"] is None else r["rank"]))
                else:
                    print(r["url"])
            if meta.get("truncated"):
                print("note: the plan returned %d of %d" % (meta["returned"], meta["total"]), file=sys.stderr)
        elif a.cmd == "clusters":
            res = pw.clusters()
            print("%d clusters of at most %d" % (res["count"], res["max"]))
            for c in res["clusters"]:
                print(_cluster_line(c))
        elif a.cmd == "cluster":
            for d in pw.iter_cluster(a.id):
                print(d)
        elif a.cmd == "cluster-create":
            if a.query is not None:
                print(_cluster_line(pw.create_cluster(query=a.query, name=a.name)))
            else:
                f = sys.stdin if a.domains_file == "-" else open(a.domains_file)
                domains = [line.strip() for line in f if line.strip()]
                print(_cluster_line(pw.create_cluster(domains=domains, name=a.name)))
        elif a.cmd == "cluster-combine":
            print(_cluster_line(pw.combine_clusters(a.operation, a.ids, name=a.name)))
        elif a.cmd == "presets":
            for p in pw.cluster_presets()["presets"]:
                print("%s\t%s" % (p["name"], p["about"]))
        elif a.cmd == "extract":
            presets = [p for p in a.presets.split(",") if p.strip()]
            out = csv.writer(sys.stdout, lineterminator="\n")
            if a.offset == 0:
                out.writerow(["domain"] + presets + a.regex)
            meta, n = {}, 0
            for r in pw.iter_extract(a.id, presets=presets, regex=a.regex, offset=a.offset, meta=meta):
                out.writerow([r["domain"]] + ["\n".join(v) for v in r["values"]])
                n += 1
            print("%d sites with matches, %s extraction points left" % (n, meta.get("points_left")), file=sys.stderr)
        elif a.cmd == "cluster-rename":
            print(_cluster_line(pw.rename_cluster(a.id, a.name)))
        elif a.cmd == "cluster-delete":
            res = pw.delete_cluster(a.id)
            print("deleted %s\t%s" % (res["deleted"], res["name"]))
    except PublicWWWError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
