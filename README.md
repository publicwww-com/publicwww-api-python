# PublicWWW API - Python

A small client for the [PublicWWW API](https://publicwww.com/docs/api/): search the
source code (HTML, JavaScript, CSS) of hundreds of millions of websites from your
own code. One file, standard library only, Python 3.8+.

## Setup

Issue a token on your [profile page](https://publicwww.com/profile/api) (a paid
plan is required for the API) and keep it in the environment, not in the code:

```sh
export PUBLICWWW_KEY=...
```

## Command line

```sh
python3 publicwww.py account                                   # plan and quota, spends nothing
python3 publicwww.py search '"angular.min.js"' --per-page 20   # one page
python3 publicwww.py export '"angular.min.js"' > sites.txt     # every url your plan covers
python3 publicwww.py export '"angular.min.js"' --csv > sites.csv
```

## As a module

```python
from publicwww import Client, PublicWWWError

pw = Client()                                    # key from $PUBLICWWW_KEY

page = pw.search('"angular.min.js"', per_page=20)
print(page["total"], "sites match")
for row in page["results"]:
    print(row["rank"], row["domain"], row["url"])   # rank is None for unranked sites

# Everything at once, streamed as NDJSON: one search, nothing held in memory.
meta = {}
for row in pw.iter_all('"angular.min.js" site:de', meta=meta):
    print(row["domain"])
if meta["truncated"]:
    print("the plan returned", meta["returned"], "of", meta["total"])
```

The query is the usual [PublicWWW syntax](https://publicwww.com/docs/query-syntax/):
`"exact phrase"`, several words for all of them, `-word` to exclude, `site:de`,
`depth:all` for inner pages.

## Errors

Any refusal raises `PublicWWWError` with `.status`, `.code` and `.message`, except
one: `429 too_many_requests` (more than ten requests a minute) is waited out using
`Retry-After` and the request is repeated. `429 quota_exceeded` is different - the
day's searches are used up, retrying will not help. All codes:
[errors](https://publicwww.com/docs/api/errors/).

`PUBLICWWW_API` overrides the API address if you ever need to.

## See also

- [API documentation](https://publicwww.com/docs/api/)
- [MCP server](https://publicwww.com/docs/mcp/) - the same search for AI assistants:
  `https://api.publicwww.com/mcp`
- The same client in [JavaScript](https://github.com/publicwww-com/publicwww-api-javascript), [PHP](https://github.com/publicwww-com/publicwww-api-php), [Go](https://github.com/publicwww-com/publicwww-api-go), [Ruby](https://github.com/publicwww-com/publicwww-api-ruby)

## License

MIT
