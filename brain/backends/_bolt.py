"""Bolt — the binary protocol Cypher databases speak (Memgraph, Neo4j, …), in the standard library.

A helper several backends can share, not a backend (§backends). Only what brain's graph role needs:
connect, authenticate, run Cypher with parameters in one transaction, read the rows back. Values are
the plain ones — null, booleans, integers, floats, strings, lists and maps; a node or a relationship in a
result comes back as `("struct", signature, fields)` (brain's queries return ids, never whole nodes).

## The wire, in short (https://neo4j.com/docs/bolt/current/)

  handshake   60 60 B0 17, then four proposed versions (4 bytes each); the server answers with one
  message     a PackStream structure — B0+n fields, a signature byte, the fields — sent in chunks of at
              most 65,535 bytes, each prefixed by its size, the message ended by a 00 00 chunk
  exchange    HELLO (5.1+: then LOGON) → SUCCESS · BEGIN → RUN + PULL per statement → COMMIT
  failure     FAILURE {code, message}; everything after it is IGNORED until a RESET
"""
from __future__ import annotations

import socket
import struct
from typing import Dict, List, Optional, Sequence, Tuple

from brain import stores

# message signatures
HELLO, LOGON, GOODBYE, RESET, RUN, BEGIN, COMMIT, PULL = 0x01, 0x6A, 0x02, 0x0F, 0x10, 0x11, 0x12, 0x3F
SUCCESS, RECORD, IGNORED, FAILURE = 0x70, 0x71, 0x7E, 0x7F

# proposed versions, newest first: 5.8 down to 5.0 · 4.4 down to 4.1 · 4.0 · nothing
PROPOSALS = bytes([0, 8, 8, 5, 0, 3, 4, 4, 0, 0, 0, 4, 0, 0, 0, 0])
USER_AGENT = "local-brain/0.1"


class BoltError(stores.StoreError):
    """The server answered FAILURE, or the connection broke."""


# ── PackStream ──────────────────────────────────────────────────────────────
def _size(n: int, tiny: int, m8: int, m16: int, m32: int) -> bytes:
    if n < 16 and tiny:
        return bytes([tiny | n])
    if n < 0x100 and m8:
        return bytes([m8, n])
    if n < 0x10000:
        return bytes([m16]) + struct.pack(">H", n)
    return bytes([m32]) + struct.pack(">I", n)


def pack(v) -> bytes:
    if v is None:
        return b"\xC0"
    if v is True:
        return b"\xC3"
    if v is False:
        return b"\xC2"
    if isinstance(v, int):
        if -16 <= v < 128:
            return struct.pack(">b", v)
        if -128 <= v < 128:
            return b"\xC8" + struct.pack(">b", v)
        if -32768 <= v < 32768:
            return b"\xC9" + struct.pack(">h", v)
        if -2147483648 <= v < 2147483648:
            return b"\xCA" + struct.pack(">i", v)
        return b"\xCB" + struct.pack(">q", v)
    if isinstance(v, float):
        return b"\xC1" + struct.pack(">d", v)
    if isinstance(v, str):
        b = v.encode("utf-8")
        return _size(len(b), 0x80, 0xD0, 0xD1, 0xD2) + b
    if isinstance(v, (bytes, bytearray)):
        return _size(len(v), 0, 0xCC, 0xCD, 0xCE) + bytes(v)
    if isinstance(v, (list, tuple)):
        return _size(len(v), 0x90, 0xD4, 0xD5, 0xD6) + b"".join(pack(x) for x in v)
    if isinstance(v, dict):
        return _size(len(v), 0xA0, 0xD8, 0xD9, 0xDA) + b"".join(pack(str(k)) + pack(x) for k, x in v.items())
    raise TypeError("cannot send a %s over Bolt" % type(v).__name__)


def unpack(buf: bytes, i: int = 0):
    """→ (value, next offset)."""
    m = buf[i]
    i += 1
    if m < 0x80:
        return m, i
    if m >= 0xF0:
        return m - 0x100, i
    if m == 0xC0:
        return None, i
    if m == 0xC2:
        return False, i
    if m == 0xC3:
        return True, i
    if m == 0xC1:
        return struct.unpack_from(">d", buf, i)[0], i + 8
    ints = {0xC8: (">b", 1), 0xC9: (">h", 2), 0xCA: (">i", 4), 0xCB: (">q", 8)}
    if m in ints:
        fmt, n = ints[m]
        return struct.unpack_from(fmt, buf, i)[0], i + n
    n, width, kind = _length(m, buf, i)
    i += width
    if kind == "str":
        return buf[i:i + n].decode("utf-8"), i + n
    if kind == "bytes":
        return bytes(buf[i:i + n]), i + n
    if kind == "list":
        out = []
        for _ in range(n):
            x, i = unpack(buf, i)
            out.append(x)
        return out, i
    if kind == "map":
        d = {}
        for _ in range(n):
            k, i = unpack(buf, i)
            d[k], i = unpack(buf, i)
        return d, i
    sig = buf[i]                                          # a structure
    i += 1
    fields = []
    for _ in range(n):
        x, i = unpack(buf, i)
        fields.append(x)
    return ("struct", sig, fields), i


def _length(m: int, buf: bytes, i: int) -> Tuple[int, int, str]:
    """A sized marker → (length, how many bytes after the marker the length took, kind)."""
    for lo, kind in ((0x80, "str"), (0x90, "list"), (0xA0, "map"), (0xB0, "struct")):
        if lo <= m < lo + 16:
            return m - lo, 0, kind
    wide = {0xD0: ("str", 1), 0xD1: ("str", 2), 0xD2: ("str", 4), 0xD4: ("list", 1), 0xD5: ("list", 2),
            0xD6: ("list", 4), 0xD8: ("map", 1), 0xD9: ("map", 2), 0xDA: ("map", 4), 0xCC: ("bytes", 1),
            0xCD: ("bytes", 2), 0xCE: ("bytes", 4)}
    if m not in wide:
        raise BoltError("bolt: unknown PackStream marker 0x%02X" % m)
    kind, w = wide[m]
    n = buf[i] if w == 1 else struct.unpack_from(">H" if w == 2 else ">I", buf, i)[0]
    return n, w, kind


# ── a connection ────────────────────────────────────────────────────────────
class Connection:
    """One Bolt session. Use as a context manager; every failure raises BoltError (a StoreError)."""

    def __init__(self, host: str, port: int, timeout: float):
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
            self.sock.sendall(b"\x60\x60\xB0\x17" + PROPOSALS)
            v = self._read(4)
        except OSError as exc:
            raise BoltError("bolt %s:%d → %s" % (host, port, exc)) from None
        self.version = (v[3], v[2])
        if self.version == (0, 0):
            self.close()
            raise BoltError("bolt %s:%d → no protocol version in common" % (host, port))
        self.server = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        try:
            self._send(GOODBYE)
        except (OSError, BoltError):
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            got = self.sock.recv(n - len(out))
            if not got:
                raise BoltError("bolt: the server closed the connection")
            out += got
        return out

    def _send(self, sig: int, *fields) -> None:
        body = bytes([0xB0 | len(fields), sig]) + b"".join(pack(f) for f in fields)
        out = b"".join(struct.pack(">H", len(body[i:i + 0xFFFF])) + body[i:i + 0xFFFF]
                       for i in range(0, len(body), 0xFFFF))
        try:
            self.sock.sendall(out + b"\x00\x00")
        except OSError as exc:
            raise BoltError("bolt: %s" % exc) from None

    def _recv(self) -> Tuple[int, list]:
        body = b""
        try:
            while True:
                n = struct.unpack(">H", self._read(2))[0]
                if n == 0:
                    if body:
                        break
                    continue                                  # a no-op chunk (keep-alive)
                body += self._read(n)
        except OSError as exc:
            raise BoltError("bolt: %s" % exc) from None
        v, _ = unpack(body, 0)
        if not (isinstance(v, tuple) and v[0] == "struct"):
            raise BoltError("bolt: a message that is not a structure")
        return v[1], v[2]

    def _expect(self) -> dict:
        """Read one summary: SUCCESS → its metadata; FAILURE → RESET and raise."""
        sig, fields = self._recv()
        if sig == SUCCESS:
            return fields[0] if fields else {}
        if sig == FAILURE:
            self._fail(fields)
        if sig == IGNORED:
            return {"ignored": True}
        raise BoltError("bolt: unexpected message 0x%02X" % sig)

    def _fail(self, fields: list) -> None:
        """Raise the server's own reason — after a RESET, so the session is usable again.

        ⛔ A server may close the connection after a FAILURE (Memgraph does, for a wrong password): the reason
           is read first, so it is what gets reported, not "the server closed the connection".
        """
        meta = fields[0] if fields and isinstance(fields[0], dict) else {}
        why = "bolt: %s %s" % (meta.get("code", ""), str(meta.get("message", ""))[:240])
        try:
            self._reset()
        except BoltError:
            pass
        raise BoltError(why)

    def _reset(self) -> None:
        self._send(RESET)
        while True:
            sig, _ = self._recv()
            if sig in (SUCCESS, FAILURE):
                return

    def hello(self, user: str = "", password: str = "") -> dict:
        auth = ({"scheme": "basic", "principal": user, "credentials": password} if password
                else {"scheme": "none"})
        extra = {"user_agent": USER_AGENT}
        if self.version >= (5, 3):
            extra["bolt_agent"] = {"product": USER_AGENT}
        if self.version >= (5, 1):
            self._send(HELLO, extra)
            meta = self._expect()
            self._send(LOGON, auth)
            self._expect()
        else:
            extra.update(auth)
            self._send(HELLO, extra)
            meta = self._expect()
        self.server = str(meta.get("server") or "")
        return meta

    def run(self, statements: Sequence[Tuple[str, dict]], database: str = "",
            explicit: Optional[bool] = None) -> List[List[list]]:
        """Run Cypher statements — in one explicit transaction when there are several (or `explicit`).

        → each statement's rows, as lists of values.
        """
        explicit = len(statements) > 1 if explicit is None else explicit
        extra: Dict[str, object] = {"db": database} if database else {}
        if explicit:
            self._send(BEGIN, extra)
            self._expect()
        results = []
        for query, params in statements:
            self._send(RUN, query, dict(params or {}), {} if explicit else extra)
            self._send(PULL, {"n": -1})
            self._expect()                                   # RUN's SUCCESS {fields}
            rows = []
            while True:
                sig, fields = self._recv()
                if sig == RECORD:
                    rows.append(fields[0])
                    continue
                if sig == SUCCESS:
                    break
                if sig == FAILURE:
                    self._fail(fields)
                if sig == IGNORED:
                    self._reset()
                    raise BoltError("bolt: the statement was ignored")
            results.append(rows)
        if explicit:
            self._send(COMMIT)
            self._expect()
        return results


def connect(url: str, user: str, password: str, timeout: float) -> Connection:
    """`bolt://host:port` → an authenticated connection."""
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("bolt", "neo4j", "memgraph") or not p.hostname:
        raise BoltError("bolt: %r is not a bolt://host:port address" % url)
    c = Connection(p.hostname, p.port or 7687, timeout)
    try:
        c.hello(user, password)
    except BoltError:
        c.close()
        raise
    return c
