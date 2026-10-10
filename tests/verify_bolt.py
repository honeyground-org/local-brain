#!/usr/bin/env python3
"""★brain's Bolt client speaks the protocol as written★ — no database needed. (local · budget 0)

`brain/backends/_bolt.py` is brain's own client for Bolt, the binary protocol Memgraph (and Neo4j) speak
— standard library only, so nothing else checks it. A real server is measured by the Docker check; this
one runs everywhere:

  ① PackStream bytes match the examples in the specification (not only our own decoder), and every kind
     of value survives a round trip — the integer boundaries, long strings, nested lists and maps
  ② against a scripted server on a local socket: the version handshake, HELLO + LOGON on 5.1 and later
     (HELLO alone before), a query's rows, a message split across chunks of 65,535 bytes, a FAILURE that
     is reported with the server's own words and leaves the session usable (RESET), and a server that
     closes the connection right after refusing a password

How to run:  PYTHONPATH=. python3 tests/verify_bolt.py
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain.backends import _bolt as bolt  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


# ── ① PackStream ────────────────────────────────────────────────────────────
SPEC = [                                  # (value, bytes) from the PackStream specification's examples
    (None, b"\xC0"), (True, b"\xC3"), (False, b"\xC2"),
    (1, b"\x01"), (-1, b"\xFF"), (-16, b"\xF0"), (127, b"\x7F"), (-17, b"\xC8\xEF"), (128, b"\xC9\x00\x80"),
    (1234, b"\xC9\x04\xD2"), (-129, b"\xC9\xFF\x7F"), (32768, b"\xCA\x00\x00\x80\x00"),
    (2 ** 31, b"\xCB\x00\x00\x00\x00\x80\x00\x00\x00"), (1.1, b"\xC1" + struct.pack(">d", 1.1)),
    ("", b"\x80"), ("A", b"\x81A"), ("Größenmaßstäbe", b"\xD0\x12" + "Größenmaßstäbe".encode("utf-8")),
    ([], b"\x90"), ([1, 2, 3], b"\x93\x01\x02\x03"), ({}, b"\xA0"), ({"one": "eins"}, b"\xA1\x83one\x84eins"),
]


def packstream() -> None:
    print("① PackStream")
    wrong = [(v, bolt.pack(v), b) for v, b in SPEC if bolt.pack(v) != b]
    check("every example from the specification is encoded byte for byte", not wrong, repr(wrong[:2]))
    wrong = [v for v, b in SPEC if bolt.unpack(b)[0] != v]
    check("…and decoded back", not wrong, repr(wrong[:2]))
    values = [0, -16, -17, -128, -129, 32767, -32768, 32768, 2 ** 31 - 1, -2 ** 31, 2 ** 31, -2 ** 63, 2 ** 63 - 1,
              -0.0, 3.5, "x" * 15, "x" * 16, "é" * 200, "y" * 70000, list(range(16)), list(range(300)),
              {str(i): i for i in range(16)}, {"a": [1, {"b": [None, True, "c"]}]}, b"\x00" * 300]
    wrong = [v for v in values if bolt.unpack(bolt.pack(v))[0] != v]
    check("every kind of value survives a round trip (%d values, at every size boundary)" % len(values),
          not wrong, repr(wrong[:1])[:80])
    s = bolt.unpack(b"\xB3\x4E\x01\x91\x84Note\xA0")[0]
    check("a structure (a node) comes back as (\"struct\", signature, fields)", s == ("struct", 0x4E, [1, ["Note"], {}]))
    try:
        bolt.unpack(b"\xE0")
        check("an unknown marker is refused", False)
    except bolt.BoltError:
        check("an unknown marker is refused", True)


# ── ② a scripted server ─────────────────────────────────────────────────────
def message(sig: int, *fields) -> bytes:
    body = bytes([0xB0 | len(fields), sig]) + b"".join(bolt.pack(f) for f in fields)
    return b"".join(struct.pack(">H", len(body[i:i + 0xFFFF])) + body[i:i + 0xFFFF]
                    for i in range(0, len(body), 0xFFFF)) + b"\x00\x00"


class Server:
    """Plays a Bolt server for one connection: answers in the order a real one would, and records what it got."""

    def __init__(self, version=(5, 2), password="pw", close_on_bad_password=False):
        self.version, self.password, self.close_bad = version, password, close_on_bad_password
        self.got = []
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
        body = b""
        while True:
            n = struct.unpack(">H", self.read(c, 2))[0]
            if n == 0:
                break
            body += self.read(c, n)
        v = bolt.unpack(body)[0]
        self.got.append((v[1], v[2]))
        return v[1], v[2]

    def serve(self):
        c, _ = self.sock.accept()
        try:
            magic = self.read(c, 4)
            self.read(c, 16)
            self.magic = magic
            c.sendall(bytes([0, 0, self.version[1], self.version[0]]))
            failed = False
            while True:
                sig, fields = self.recv(c)
                if failed and sig != bolt.RESET:
                    c.sendall(message(bolt.IGNORED))
                    continue
                if sig in (bolt.HELLO, bolt.LOGON):
                    auth = fields[0]
                    if auth.get("scheme") == "basic" and auth.get("credentials") != self.password:
                        c.sendall(message(bolt.FAILURE, {"code": "Neo.ClientError.Security.Unauthorized",
                                                         "message": "The client is unauthorized"}))
                        if self.close_bad:
                            c.close()
                            return
                        failed = True
                        continue
                    c.sendall(message(bolt.SUCCESS, {"server": "Fake/1.0"} if sig == bolt.HELLO else {}))
                elif sig == bolt.RUN:
                    q = fields[0]
                    if "broken" in q:
                        c.sendall(message(bolt.FAILURE, {"code": "Fake.SyntaxError", "message": "no such thing: broken"}))
                        failed = True
                        continue
                    c.sendall(message(bolt.SUCCESS, {"fields": ["x"]}))
                elif sig == bolt.PULL:
                    q = self.got[-2][1][0]
                    if "big" in q:
                        c.sendall(message(bolt.RECORD, ["z" * 200000]))
                    else:
                        for i in range(3):
                            c.sendall(message(bolt.RECORD, [i, self.got[-2][1][1].get("p")]))
                    c.sendall(message(bolt.SUCCESS, {}))
                elif sig == bolt.RESET:
                    failed = False
                    c.sendall(message(bolt.SUCCESS, {}))
                elif sig in (bolt.BEGIN, bolt.COMMIT):
                    c.sendall(message(bolt.SUCCESS, {}))
                elif sig == bolt.GOODBYE:
                    c.close()
                    return
        except (EOFError, OSError):
            pass


def session() -> None:
    print("\n② a scripted server")
    s = Server((5, 2))
    c = bolt.connect("bolt://127.0.0.1:%d" % s.port, "brain", "pw", 5)
    check("the handshake sends the magic and agrees on a version", s.magic == b"\x60\x60\xB0\x17"
          and c.version == (5, 2), str(c.version))
    sigs = [g[0] for g in s.got]
    check("5.1 and later: HELLO without credentials, then LOGON with them",
          sigs[:2] == [bolt.HELLO, bolt.LOGON] and "credentials" not in s.got[0][1][0]
          and s.got[1][1][0].get("credentials") == "pw" and c.server == "Fake/1.0")
    rows = c.run([("RETURN $p", {"p": "é"})])
    check("a query returns its rows, with its parameters sent", rows == [[[0, "é"], [1, "é"], [2, "é"]]], str(rows))
    rows = c.run([("RETURN big", {})])
    check("a message larger than one chunk (200,000 bytes) is reassembled", rows == [[["z" * 200000]]])
    try:
        rows = c.run([("RETURN $p", {"p": "q" * 150000})])
    except bolt.BoltError as exc:
        rows = str(exc)
    check("…and one is split into chunks of at most 65,535 bytes when sent (150,000 bytes)",
          rows == [[[i, "q" * 150000] for i in range(3)]], str(rows)[:80])
    rows = c.run([("RETURN 1", {"p": 1}), ("RETURN 2", {"p": 2})])
    check("several statements run in one transaction (BEGIN … COMMIT)",
          [g[0] for g in s.got[-6:]] == [bolt.BEGIN, bolt.RUN, bolt.PULL, bolt.RUN, bolt.PULL, bolt.COMMIT]
          and rows == [[[0, 1], [1, 1], [2, 1]], [[0, 2], [1, 2], [2, 2]]])
    try:
        c.run([("RETURN broken", {})])
        check("a FAILURE raises", False)
    except bolt.BoltError as exc:
        check("a FAILURE raises with the server's own words", "no such thing: broken" in str(exc), str(exc)[:80])
    try:
        usable = c.run([("RETURN ok", {"p": 0})])[0][0] == [0, 0]
    except bolt.BoltError as exc:
        usable = False
        print("   ", str(exc)[:80])
    check("…and leaves the session usable (RESET)", usable)
    c.close()

    s = Server((4, 4))
    c = bolt.connect("bolt://127.0.0.1:%d" % s.port, "brain", "pw", 5)
    check("before 5.1: the credentials travel in HELLO", c.version == (4, 4) and s.got[0][0] == bolt.HELLO
          and s.got[0][1][0].get("credentials") == "pw")
    c.close()

    s = Server((5, 2), close_on_bad_password=True)
    try:
        bolt.connect("bolt://127.0.0.1:%d" % s.port, "brain", "wrong", 5)
        check("a wrong password is refused", False)
    except bolt.BoltError as exc:
        check("a wrong password is refused with the server's reason — even when it then hangs up",
              "unauthorized" in str(exc).lower(), str(exc)[:90])
    s = Server((5, 2))
    c = bolt.connect("bolt://127.0.0.1:%d" % s.port, "", "", 5)
    check("no password → the \"none\" scheme (a server without authentication)", s.got[1][1][0] == {"scheme": "none"})
    c.close()
    try:
        bolt.connect("http://127.0.0.1:1", "", "", 1)
        check("an address that is not bolt:// is refused", False)
    except bolt.BoltError:
        check("an address that is not bolt:// is refused", True)
    try:
        bolt.connect("bolt://127.0.0.1:9", "", "", 1)
        check("nothing listening → a BoltError (which is a StoreError: the local copy answers)", False)
    except bolt.BoltError as exc:
        from brain import stores
        check("nothing listening → a BoltError (which is a StoreError: the local copy answers)",
              isinstance(exc, stores.StoreError))


def main() -> int:
    packstream()
    session()
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ brain's Bolt client speaks the protocol as written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
