"""Neo4j — the graph role, over Neo4j's HTTP transaction API (§stores · §graphstore.CONTRACT).

What it is sent: document ids and the links between them; names only when the person turned `names` on
(§graphstore.sync decides that, for every graph backend). The queries are the shared openCypher ones
(§backends._cypher); this file is the connection.
"""
from __future__ import annotations

from typing import List, Tuple

from brain import stores
from brain.backends._cypher import CypherGraph


class Neo4jGraph(CypherGraph):
    """Neo4j over HTTP: one POST to `/db/<database>/tx/commit` per transaction."""
    backend = "neo4j"
    DDL = ("CREATE CONSTRAINT brain_doc_key IF NOT EXISTS FOR (d:BrainDoc) REQUIRE d.key IS UNIQUE",
           "CREATE INDEX brain_doc_ns IF NOT EXISTS FOR (d:BrainDoc) ON (d.ns)")

    def __init__(self, c: dict):
        super().__init__(c)
        self.database = str(stores.opt(c, "database"))
        self.headers = stores.basic_auth(str(stores.opt(c, "user")), stores.secret(c))

    def target(self) -> str:
        return "neo4j:%s/%s#%s" % (self.url, self.database, self.ns)

    def run(self, statements: List[Tuple[str, dict]], timeout: float = 0.0) -> List[List[list]]:
        r = stores.http_json("POST", "%s/db/%s/tx/commit" % (self.url, self.database),
                             {"statements": [{"statement": s, "parameters": p} for s, p in statements]},
                             self.headers, timeout or stores.QUERY_TIMEOUT)
        errs = r.get("errors") or []
        if errs:
            raise stores.StoreError("neo4j: %s" % "; ".join("%s %s" % (e.get("code"), e.get("message"))
                                                             for e in errs)[:300])
        return [[row.get("row") for row in (res.get("data") or [])] for res in (r.get("results") or [])]

    def ping(self) -> dict:
        rows = self.run([("CALL dbms.components() YIELD versions, edition RETURN versions[0], edition", {})])
        v = rows[0][0] if rows and rows[0] else ["", ""]
        return {"ok": True, "version": "%s %s" % (v[0], v[1])}


NEO4J_DIST = "https://dist.neo4j.org/"
NEO4J_VERSION = "5.26.31"
_UNIX = (NEO4J_DIST + "neo4j-community-%s-unix.tar.gz" % NEO4J_VERSION,
         "f8fc23340561405f1ff10ca6ac2d317d095d3c74509a616883c45d7a61f5cfec")


def native_layout(ctx: dict) -> dict:
    """Neo4j without Docker: the official tarball, run in the foreground (`neo4j console`) by the service
    manager, on the Java runtime brain downloaded for it (§nativestores.RUNTIMES).

    ⛔ Small memory settings on purpose: the graph is a few thousand links, and a default Neo4j takes a
       quarter of the machine's memory for its page cache.
    ⛔ The first password is set with `neo4j-admin … @file` — picocli reads the argument from that file, so
       the password never appears on a command line.
    """
    import os as _os
    win = ctx["platform"].startswith("windows")
    home, data, logs = ctx["home"], ctx["data"]["data"], ctx["data"]["logs"]
    fwd = lambda p: p.replace("\\", "/")             # neo4j.conf takes forward slashes on every platform
    conf = ("# Written by local-brain (`brain stores --native`).\n"
            "server.directories.data=%s\nserver.directories.logs=%s\n"
            "server.default_listen_address=127.0.0.1\n"
            "server.http.listen_address=127.0.0.1:%d\nserver.bolt.listen_address=127.0.0.1:%d\n"
            "server.https.enabled=false\n"
            "dbms.usage_report.enabled=false\n"
            "server.memory.heap.initial_size=256m\nserver.memory.heap.max_size=512m\n"
            "server.memory.pagecache.size=128m\n"
            % (fwd(data), fwd(logs), ctx["ports"]["http"], ctx["ports"]["bolt"]))
    bin_ = lambda n: _os.path.join(home, "bin", n + (".bat" if win else ""))
    env = {"JAVA_HOME": ctx["runtime_home"], "NEO4J_HOME": home}
    return {"argv": [bin_("neo4j"), "console"], "env": env, "cwd": home,
            "files": {_os.path.join(home, "conf", "neo4j.conf"): conf},
            "init": [[bin_("neo4j-admin"), "dbms", "set-initial-password", "@" + ctx["secret_file"]]]}


BACKEND = stores.Backend(
    name="neo4j", role="graph", title="Neo4j", make=Neo4jGraph, url="http://localhost:7474",
    secret=stores.Secret(env="NEO4J_PASSWORD", field="neo4j_password", required=True),
    options=(stores.Option("database", "neo4j", "database name"),
             stores.Option("user", "neo4j", "user (the password comes from NEO4J_PASSWORD or secrets.json)")),
    docker=stores.Docker(image="neo4j:5.26.31-community",
                         ports=(stores.Port("http", 7474, "BRAIN_NEO4J_HTTP_PORT"),
                                stores.Port("bolt", 7687, "BRAIN_NEO4J_BOLT_PORT")),
                         data=(("data", "/data"), ("logs", "/logs")),
                         env={"NEO4J_AUTH": "neo4j/{secret}"}, generate_secret=True,
                         telemetry_off=("NEO4J_dbms_usage__report_enabled=false",)),   # on by default
    # Without Docker: the official tarball (zip on Windows), checksums from dist.neo4j.org (2026-11-07).
    native=stores.Native(
        version=NEO4J_VERSION,
        files={"darwin-arm64": _UNIX, "darwin-x64": _UNIX, "linux-x64": _UNIX, "linux-arm64": _UNIX,
               "windows-x64": (NEO4J_DIST + "neo4j-community-%s-windows.zip" % NEO4J_VERSION,
                               "e8d91edb1b0864e8602e3a41ceda7555da5ebe4028d62da09b5dfb15eff297c4")},
        ports=(stores.Port("http", 7474, "BRAIN_NEO4J_HTTP_PORT"), stores.Port("bolt", 7687, "BRAIN_NEO4J_BOLT_PORT")),
        data=("data", "logs"), layout=native_layout, runtime="java21", generate_secret=True,
        telemetry="dbms.usage_report.enabled=false in neo4j.conf"),
)
