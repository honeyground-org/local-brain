"""The Postgres wire protocol (v3), in the standard library — for backends that live in Postgres.

A helper several backends can share, not a backend (§backends). Only what they need: connect (with TLS
when asked or offered), authenticate (SCRAM-SHA-256 — Postgres' default since 14 — MD5 or a plain
password), run a simple query and read every row back as text, and report a failure with the server's
own SQLSTATE and message.

## The wire, in short (https://www.postgresql.org/docs/current/protocol.html)

  startup     int32 length · int32 196608 (3.0) · "user\\0…\\0database\\0…\\0\\0" — no type byte
  TLS         int32 8 · int32 80877103 first; the server answers one byte, S (go ahead) or N (no)
  message     one type byte · int32 length (itself included) · body
  auth        R 0 ok · R 3 password · R 5 MD5 + salt · R 10 SASL mechanisms → R 11 continue → R 12 final
  query       Q "sql\\0" → T row description · D data rows · C complete … E error · Z ready
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import ssl
import struct
import urllib.parse
from typing import List, Optional, Tuple

from brain import stores

PROTOCOL = 196608                 # 3.0
SSL_REQUEST = 80877103


class PgError(stores.StoreError):
    """The server answered ErrorResponse (with its SQLSTATE in `code`), or the connection broke."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


# ── SCRAM-SHA-256 (RFC 5802 · RFC 7677) ─────────────────────────────────────
def scram_proof(password: str, client_first_bare: str, server_first: str,
                client_final_bare: str) -> Tuple[str, str]:
    """(ClientProof, ServerSignature), both base64 — from the exchange so far."""
    attrs = dict(kv.split("=", 1) for kv in server_first.split(","))
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(attrs["s"]), int(attrs["i"]))
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    auth_message = ",".join((client_first_bare, server_first, client_final_bare)).encode("utf-8")
    client_sig = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(client_key, client_sig))
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    server_sig = hmac.new(server_key, auth_message, hashlib.sha256).digest()
    return base64.b64encode(proof).decode("ascii"), base64.b64encode(server_sig).decode("ascii")


# ── a connection ────────────────────────────────────────────────────────────
class Connection:
    """One session. Use as a context manager; every failure raises PgError (a StoreError)."""

    def __init__(self, host: str, port: int, user: str, password: str, database: str, timeout: float,
                 sslmode: str = "prefer"):
        self.host = host
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise PgError("postgres %s:%d → %s" % (host, port, exc)) from None
        try:
            if sslmode != "disable":
                self._tls(sslmode, timeout)
            params = b"".join(k.encode() + b"\0" + v.encode("utf-8") + b"\0" for k, v in (
                ("user", user), ("database", database), ("client_encoding", "UTF8"),
                ("application_name", "local-brain")))
            body = struct.pack(">i", PROTOCOL) + params + b"\0"
            self.sock.sendall(struct.pack(">i", len(body) + 4) + body)
            self._auth(user, password)
            while True:                                          # parameter status, key data … ready
                t, _ = self._recv()
                if t == b"Z":
                    break
        except OSError as exc:
            self.close()
            raise PgError("postgres %s:%d → %s" % (host, port, exc)) from None
        except PgError:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        try:
            self.sock.sendall(b"X" + struct.pack(">i", 4))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    # ── the bytes ──
    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            got = self.sock.recv(n - len(out))
            if not got:
                raise PgError("postgres: the server closed the connection")
            out += got
        return out

    def _send(self, kind: bytes, body: bytes) -> None:
        self.sock.sendall(kind + struct.pack(">i", len(body) + 4) + body)

    def _recv(self) -> Tuple[bytes, bytes]:
        head = self._read(5)
        n = struct.unpack(">i", head[1:])[0]
        body = self._read(n - 4)
        if head[:1] == b"E":
            raise _error(body)
        return head[:1], body

    def _tls(self, sslmode: str, timeout: float) -> None:
        self.sock.sendall(struct.pack(">ii", 8, SSL_REQUEST))
        answer = self._read(1)
        if answer != b"S":
            if sslmode in ("require", "verify-full"):
                raise PgError("postgres: the server does not offer TLS (sslmode=%s)" % sslmode)
            return
        ctx = ssl.create_default_context()
        if sslmode != "verify-full":                             # libpq's prefer / require: encrypted, not verified
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)
        self.sock.settimeout(timeout)

    def _auth(self, user: str, password: str) -> None:
        while True:
            t, body = self._recv()
            if t != b"R":
                raise PgError("postgres: expected authentication, got %r" % t)
            code = struct.unpack(">i", body[:4])[0]
            if code == 0:
                return
            if code == 3:                                        # a plain password (only over TLS, ideally)
                self._send(b"p", password.encode("utf-8") + b"\0")
            elif code == 5:
                inner = hashlib.md5((password + user).encode("utf-8")).hexdigest().encode("ascii")
                self._send(b"p", b"md5" + hashlib.md5(inner + body[4:8]).hexdigest().encode("ascii") + b"\0")
            elif code == 10:
                mechanisms = body[4:].split(b"\0")
                if b"SCRAM-SHA-256" not in mechanisms:
                    raise PgError("postgres: no SASL mechanism in common (%s)" % b", ".join(m for m in mechanisms if m))
                self._scram(password)
            else:
                raise PgError("postgres: authentication method %d is not supported" % code)

    def _scram(self, password: str) -> None:
        nonce = base64.b64encode(os.urandom(18)).decode("ascii")
        first_bare = "n=,r=" + nonce                             # the user is the startup's (RFC 5802 lets it be empty)
        first = ("n,," + first_bare).encode("utf-8")
        self._send(b"p", b"SCRAM-SHA-256\0" + struct.pack(">i", len(first)) + first)
        t, body = self._recv()
        if t != b"R" or struct.unpack(">i", body[:4])[0] != 11:
            raise PgError("postgres: SCRAM — expected the server's first message")
        server_first = body[4:].decode("utf-8")
        if not dict(kv.split("=", 1) for kv in server_first.split(","))["r"].startswith(nonce):
            raise PgError("postgres: SCRAM — the server's nonce does not extend ours")
        final_bare = "c=biws,r=" + dict(kv.split("=", 1) for kv in server_first.split(","))["r"]
        proof, server_sig = scram_proof(password, first_bare, server_first, final_bare)
        self._send(b"p", (final_bare + ",p=" + proof).encode("utf-8"))
        t, body = self._recv()
        if t != b"R" or struct.unpack(">i", body[:4])[0] != 12:
            raise PgError("postgres: SCRAM — expected the server's final message")
        if body[4:].decode("utf-8").strip() != "v=" + server_sig:
            raise PgError("postgres: SCRAM — the server could not prove it knows the password")

    # ── a query ──
    def query(self, sql: str) -> List[List[Optional[str]]]:
        """Run one simple query (one statement or several) → the rows of the last one that returned rows."""
        self._send(b"Q", sql.encode("utf-8") + b"\0")
        rows: List[List[Optional[str]]] = []
        current: List[List[Optional[str]]] = []
        failure: Optional[PgError] = None
        while True:
            try:
                t, body = self._recv()
            except PgError as exc:
                if getattr(exc, "code", ""):                      # ErrorResponse — ReadyForQuery still follows
                    failure = exc
                    continue
                raise
            if t == b"T":
                current = []
            elif t == b"D":
                current.append(_row(body))
            elif t == b"C":
                if current:
                    rows = current
                current = []
            elif t == b"Z":
                if failure:
                    raise failure
                return rows


def _row(body: bytes) -> List[Optional[str]]:
    n = struct.unpack(">h", body[:2])[0]
    i, out = 2, []
    for _ in range(n):
        size = struct.unpack(">i", body[i:i + 4])[0]
        i += 4
        if size < 0:
            out.append(None)
        else:
            out.append(body[i:i + size].decode("utf-8"))
            i += size
    return out


def _error(body: bytes) -> PgError:
    fields = {}
    for part in body.split(b"\0"):
        if part:
            fields[part[:1].decode("ascii", "replace")] = part[1:].decode("utf-8", "replace")
    return PgError("postgres: %s %s" % (fields.get("C", ""), fields.get("M", "")[:240]), fields.get("C", "x"))


def connect(url: str, user: str, password: str, timeout: float, sslmode: str = "prefer") -> Connection:
    """`postgres://host:port/database` → an authenticated session."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("postgres", "postgresql") or not p.hostname:
        raise PgError("postgres: %r is not a postgres://host:port/database address" % url)
    return Connection(p.hostname, p.port or 5432, user or p.username or "postgres", password,
                      (p.path or "/").lstrip("/") or user, timeout, sslmode)


def literal(s: str) -> str:
    """A SQL string literal — standard_conforming_strings, so only the quote is doubled."""
    return "'" + str(s).replace("'", "''") + "'"
