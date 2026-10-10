# Adding a storage backend

brain answers two kinds of question that have databases built for them — **similarity search** (the
`vector` role) and **traversal of the links between notes** (the `graph` role). The local SQLite file
answers both by default and always keeps the canonical copy; a dedicated database is a serving index
for one role, filled by a sync and trusted only while it is in sync.

**A backend is one file.** Put a module in `brain/backends/` that defines `BACKEND`, and nothing else
needs editing. From that declaration brain builds:

| What | Where it comes from |
|---|---|
| `brain stores --set graph=NAME` | the name and the role |
| one `brain stores --<option>` flag per option | `options` |
| `brain stores --docker NAME` — container, data folders, readiness | `docker` |
| `brain install --vector-store NAME` / `--graph-store NAME[=URL]` | the role, and whether `docker` is set |
| the `brain stores` screen: what it is sent, whether it answers, whether it is in sync | the role and `ping()` |
| the contract check, live and in Docker, in CI | every registered backend, automatically |

A file that does not load, or does not meet its role's contract, is not offered, and `brain stores`
prints the reason.

## The declaration

```python
from brain import stores

class MyGraph:
    backend = "mygraph"
    remote = True
    def __init__(self, c: dict): ...      # c = {role, backend, url, options, [secret]}
    # … the methods of the role's contract (below)

BACKEND = stores.Backend(
    name="mygraph",                       # must equal the file name: brain/backends/mygraph.py
    role="graph",                         # "vector" or "graph"
    title="MyGraph",
    make=MyGraph,                         # the class; brain calls make(choice)
    url="http://localhost:9999",          # the usual address (a choice or BRAIN_GRAPH_STORE_URL overrides it)
    secret=stores.Secret(env="MYGRAPH_PASSWORD", field="mygraph_password", required=True),
    options=(stores.Option("database", "main", "database name"),),   # bool, str or int defaults
    docker=stores.Docker(                 # optional — leave it out for a hosted-only service
        image="example/mygraph:2.4.1",    # pinned: a version tag (or a digest), never latest
        ports=(stores.Port("api", 9999, "BRAIN_MYGRAPH_PORT"),),     # bound to 127.0.0.1
        data=(("data", "/var/lib/mygraph"),),                        # <brain home>/stores/mygraph/data
        env={"MYGRAPH_AUTH": "admin/{secret}"},                     # a {secret} value goes by name only
        generate_secret=True,             # brain creates the password once, in secrets.json (0600)
        telemetry_off=("MYGRAPH_TELEMETRY=off",)),                  # required — () if it reports nothing
)
```

Read option values with `stores.opt(c, "database")` — it returns the person's value or the default you
declared. Read the credential with `stores.secret(c)`; never put a secret in an option. The role's own
options (`namespace` for both roles, `names` for the graph) are taken by every backend of the role.

## The contract

`vecstore.CONTRACT` and `graphstore.CONTRACT` list the methods; the docstrings at the top of
`brain/vecstore.py` and `brain/graphstore.py` say what each one returns. In short:

- **vector** — `target · ping · ensure · reset · drop · upsert · delete_docs · search · count`.
  `search` returns each document's closest chunk as `[(doc_id, cosine)]`, best first, ties by `doc_id`.
- **graph** — `target · ping · ensure · reset · drop · upsert_nodes · delete_nodes · add_edges ·
  remove_edges · counts` and the five questions `neighbors · incoming · linked · targets · edges`.
  A database that speaks openCypher can subclass `brain/backends/_cypher.py`'s `CypherGraph` and
  supply only `run()` and `ping()` — `neo4j.py` (over HTTP) and `memgraph.py` (over Bolt, with
  `brain/backends/_bolt.py`) are the examples.

Rules that hold for every backend:

- **Answers are identical to the local copy's.** `brain stores --check` and the contract check compare
  them question by question.
- **`target()` is stable** — it is the key of the ledger that records what was sent. It must change when
  the role's `namespace` option changes.
- **Raise `stores.StoreError`** for anything the database does not do; brain then answers from the local
  copy and records the failure. An answer that means *"this is gone"* should contain `HTTP 404`, so the
  next sync refills it.
- **Only what the role hands you leaves the machine.** A vector backend receives vectors and
  `{doc_id, chunk_no}`; a graph backend receives document ids, links, and a name only when the person
  turned `names` on — already scrubbed. Do not read the index yourself to send more.
- **Nothing reports home.** Many database images send usage statistics by default (Qdrant, Neo4j and
  Memgraph all do). `telemetry_off` is required: the variable (`NAME=VALUE`) or argument
  (`--flag=value`) that turns it off, or `()` when the image has none — find it in the image's
  configuration, then confirm in its log or settings that it took effect.
- **Standard library only.** Speak the database's own protocol (`stores.http_json` for HTTP). A wire
  protocol several backends would share goes in a `_`-prefixed module next to them.

## Checking it

```bash
PYTHONPATH=. python3 tests/verify_store_adapters.py     # it loads, meets the contract, and is offered everywhere
docker run -d -p 127.0.0.1:9999:9999 example/mygraph:2.4.1
BRAIN_TEST_MYGRAPH_URL=http://localhost:9999 BRAIN_TEST_MYGRAPH_SECRET=… \
  PYTHONPATH=. python3 tests/verify_stores_live.py      # the whole contract against the real database
BRAIN_TEST_DOCKER=mygraph PYTHONPATH=. python3 tests/verify_docker_stores.py   # through brain's own Docker
```

In CI the Docker check runs every backend that declares an image — a new one is tested there without
editing the workflow. Measure it on a real corpus with `brain stores --check` and put the numbers in the
pull request (CONTRIBUTING.md); a new backend is a high-impact change.
