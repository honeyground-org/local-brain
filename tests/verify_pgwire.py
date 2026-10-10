#!/usr/bin/env python3
"""★brain's Postgres client speaks the protocol as written★ — no database needed. (local · budget 0)

`brain/backends/_pgwire.py` is brain's own client for the Postgres wire protocol (pgvector lives behind
it) — standard library only, so nothing else checks it. A real server is measured by the Docker check;
this one runs everywhere:

  ① SCRAM-SHA-256 gives the proof and the server signature of RFC 7677's own example, byte for byte
  ② against a scripted server on a local socket: the startup message, SCRAM with a server that proves
     itself (and one that cannot — refused), MD5, a plain password, rows with NULL and UTF-8, several
     statements in one query, an error reported with its SQLSTATE that leaves the session usable, a
     refused password, TLS required but not offered, and a missing table read as "gone" (§stores.StoreGone)

How to run:  PYTHONPATH=. python3 tests/verify_pgwire.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import struct
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import stores  # noqa: E402
from brain.backends import _pgwire as pg  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def rfc7677() -> None:
    print("① SCRAM-SHA-256")
    proof, sig = pg.scram_proof("pencil", "n=user,r=rOprNGfwEbeRWgbNEkqO",
                                "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096",
                                "c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0")
    check("the client proof is RFC 7677's", proof == "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=", proof)
    check("the server signature is RFC 7677's", sig == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=", sig)


# ── a scripted server ───────────────────────────────────────────────────────
def msg(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack(">i", len(body) + 4) + body


def auth(code: int, extra: bytes = b"") -> bytes:
    return msg(b"R", struct.pack(">i", code) + extra)


def error(code: str, text: str) -> bytes:
    return msg(b"E", b"SERROR\0C" + code.encode() + b"\0M" + text.encode("utf-8") + b"\0\0")


READY = msg(b"Z", b"I")


def rows(cols: int, data: list) -> bytes:
    out = msg(b"T", struct.pack(">h", cols) + b"".join(b"c%d\0" % i + b"\0" * 18 for i in range(cols)))
    for r in data:
        body = struct.pack(">h", len(r))
        for v in r:
            if v is None:
                body += struct.pack(">i", -1)
            else:
                b = v.encode("utf-8")
                body += struct.pack(">i", len(b)) + b
        out += msg(b"D", body)
    return out + msg(b"C", b"SELECT %d\0" % len(data))


class Server:
    """Plays a Postgres server for one connection, with the authentication method and password asked for."""

    def __init__(self, method="scram", password="pw", honest=True, tls=False):
        self.method, self.password, self.honest, self.tls = method, password, honest, tls
        self.startup, self.queries, self.ssl_asked = {}, [], False
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def read(self, c, n):
        out = b""
        while len(out) < n:
            got = c.recv(n - len(out))
            if not got:
                raise EOFError
            out += got
        return out

    def recv(self, c):
        head = self.read(c, 5)
        return head[:1], self.read(c, struct.unpack(">i", head[1:])[0] - 4)

    def serve(self):
        c, _ = self.sock.accept()
        try:
            n = struct.unpack(">i", self.read(c, 4))[0]
            body = self.read(c, n - 4)
            if struct.unpack(">i", body[:4])[0] == pg.SSL_REQUEST:
                self.ssl_asked = True
                c.sendall(b"N")
                n = struct.unpack(">i", self.read(c, 4))[0]
                body = self.read(c, n - 4)
            parts = body[4:].split(b"\0")
            self.startup = {parts[i].decode(): parts[i + 1].decode("utf-8") for i in range(0, len(parts) - 1, 2) if parts[i]}
            user = self.startup.get("user", "")
            if not self.authenticate(c, user):
                return
            c.sendall(auth(0) + msg(b"S", b"server_version\0" + b"17.0\0") + msg(b"K", b"\0" * 8) + READY)
            while True:
                t, body = self.recv(c)
                if t == b"X":
                    return
                q = body.rstrip(b"\0").decode("utf-8")
                self.queries.append(q)
                if "no_such_table" in q:
                    c.sendall(error("42P01", 'relation "no_such_table" does not exist') + READY)
                elif "broken" in q:
                    c.sendall(error("42601", "syntax error at or near \"broken\"") + READY)
                elif q.startswith("CREATE"):
                    c.sendall(msg(b"C", b"CREATE TABLE\0") + rows(1, [["2"]]) + READY)
                else:
                    c.sendall(rows(3, [["1", "\u00e9\ud55c\uae00", None], ["2", "x", "y"]]) + READY)
        except (EOFError, OSError):
            pass

    def authenticate(self, c, user) -> bool:
        if self.method == "password":
            c.sendall(auth(3))
            ok = self.recv(c)[1].rstrip(b"\0").decode() == self.password
        elif self.method == "md5":
            salt = b"\x01\x02\x03\x04"
            c.sendall(auth(5, salt))
            inner = hashlib.md5((self.password + user).encode()).hexdigest().encode()
            ok = self.recv(c)[1].rstrip(b"\0") == b"md5" + hashlib.md5(inner + salt).hexdigest().encode()
        else:
            c.sendall(auth(10, b"SCRAM-SHA-256\0\0"))
            _, body = self.recv(c)
            mech, rest = body.split(b"\0", 1)
            first = rest[4:].decode()
            self.client_first = first
            bare = first.split(",", 2)[2]
            cnonce = dict(kv.split("=", 1) for kv in bare.split(","))["r"]
            salt, it = b"saltsalt", 4096
            server_first = "r=%sSERVER,s=%s,i=%d" % (cnonce, base64.b64encode(salt).decode(), it)
            c.sendall(auth(11, server_first.encode()))
            final = self.recv(c)[1].decode()
            final_bare, proof = final.rsplit(",p=", 1)
            want, sig = pg.scram_proof(self.password, bare, server_first, final_bare)
            ok = proof == want
            if ok:
                c.sendall(auth(12, ("v=" + (sig if self.honest else base64.b64encode(b"x" * 32).decode())).encode()))
        if not ok:
            c.sendall(error("28P01", 'password authentication failed for user "%s"' % user))
            c.close()
        return ok


def session() -> None:
    print("\n② a scripted server")
    s = Server("scram")
    with pg.connect("postgres://127.0.0.1:%d/brain" % s.port, "brain", "pw", 5, "disable") as c:
        check("startup: user, database and UTF-8 asked for", s.startup.get("user") == "brain"
              and s.startup.get("database") == "brain" and s.startup.get("client_encoding") == "UTF8", str(s.startup))
        check("SCRAM: the user travels in the startup, the first message names none (n=,r=…)",
              s.client_first.startswith("n,,n=,r="), s.client_first[:20])
        got = c.query("SELECT 1")
        check("rows come back as text, NULL as None, UTF-8 intact", got == [["1", "\u00e9\ud55c\uae00", None], ["2", "x", "y"]], str(got))
        got = c.query("CREATE TABLE t (x int); SELECT 2")
        check("several statements in one query → the rows of the last that has rows", got == [["2"]], str(got))
        try:
            c.query("SELECT broken")
            check("an error raises", False)
        except pg.PgError as exc:
            check("an error raises with its SQLSTATE and the server's words", exc.code == "42601" and "broken" in str(exc),
                  str(exc)[:80])
        try:
            usable = c.query("SELECT 1")[0][0] == "1"
        except (pg.PgError, IndexError) as exc:
            usable = False
            print("   ", exc)
        check("…and the session is still usable", usable)
    s = Server("scram")
    with pg.connect("postgres://127.0.0.1:%d/brain" % s.port, "brain", "pw", 5, "prefer"):
        check("prefer: TLS is asked for; the server said no, so the session went on in the clear", s.ssl_asked)
    s = Server("scram")
    try:
        pg.connect("postgres://127.0.0.1:%d/brain" % s.port, "brain", "pw", 5, "require")
        check("require: refused when the server offers no TLS", False)
    except pg.PgError as exc:
        check("require: refused when the server offers no TLS", "does not offer TLS" in str(exc), str(exc)[:70])
    for method in ("md5", "password"):
        s = Server(method)
        try:
            with pg.connect("postgres://127.0.0.1:%d/brain" % s.port, "brain", "pw", 5, "disable") as c:
                ok = c.query("SELECT 1")[0][0] == "1"
            why = ""
        except pg.PgError as exc:
            ok, why = False, str(exc)[:70]
        check("%s authentication" % method, ok, why)
    s = Server("scram", password="right")
    try:
        pg.connect("postgres://127.0.0.1:%d/brain" % s.port, "brain", "wrong", 5, "disable")
        check("a wrong password is refused", False)
    except pg.PgError as exc:
        check("a wrong password is refused with the server's SQLSTATE", exc.code == "28P01", str(exc)[:70])
    s = Server("scram", honest=False)
    try:
        pg.connect("postgres://127.0.0.1:%d/brain" % s.port, "brain", "pw", 5, "disable")
        check("a server that cannot prove it knows the password is refused", False)
    except pg.PgError as exc:
        check("a server that cannot prove it knows the password is refused", "could not prove" in str(exc), str(exc)[:70])

    print("\n   pgvector over it")
    from brain.backends import pgvector
    s = Server("scram")
    be = pgvector.PgVectors({"backend": "pgvector", "role": "vector", "url": "postgres://127.0.0.1:%d/brain" % s.port,
                             "options": {"sslmode": "disable", "table_prefix": "no_such_table"}, "secret": "pw"})
    try:
        be.count(None, "m", 3)
        check("a missing table (42P01) is read as \"gone\" — the next sync refills it", False)
    except stores.StoreError as exc:
        check("a missing table (42P01) is read as \"gone\" — the next sync refills it", stores.gone(exc)
              and isinstance(exc, stores.StoreGone), type(exc).__name__)
    long = pgvector.PgVectors({"backend": "pgvector", "url": "postgres://h/db",
                               "options": {"namespace": "x" * 40}}).table("gemini-embedding-2+search", 768)
    check("a table name never exceeds Postgres' 63 bytes", len(long) <= 63 and long.islower(), long)
    import array
    v32 = array.array("f", [0.1, 1 / 3.0, -2.5e-8, 0.7071067811865476, 1e-38])
    back = array.array("f", [float(x) for x in pgvector._vec(v32).strip("[]").split(",")])
    check("a float32 written as text reads back as the very same float32 (so both sides score the same vector)",
          back.tobytes() == v32.tobytes(), pgvector._vec(v32)[:60])


def main() -> int:
    rfc7677()
    session()
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ brain's Postgres client speaks the protocol as written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
