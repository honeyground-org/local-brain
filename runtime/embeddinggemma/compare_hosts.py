#!/usr/bin/env python3
"""Three-arm host evaluation from explicit commands and frozen, user-owned evidence.

This measures cited evidence recall and actual host usage, not semantic answer correctness.
No host, model, credentials, memory directory, or API provider is selected automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from brain import evalinit, i18n

ARMS = ("native", "old", "current")
SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"}, "abstain": {"type": "boolean"},
    "evidence": {"type": "array", "maxItems": 3, "items": {"type": "object",
        "properties": {"path": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["path", "quote"], "additionalProperties": False}}},
    "required": ["answer", "abstain", "evidence"], "additionalProperties": False}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def fingerprint(spec, cases, files):
    earned = []
    for item in files:
        path = Path(item["path"]).resolve()
        earned.append([str(path), hashlib.sha256(path.read_bytes()).hexdigest(),
                       item.get("names", []), item.get("autoloaded_text", "")])
    code = []
    for value in spec.get("code_paths", []):
        path = Path(value).resolve()
        paths = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        code.extend([str(p), hashlib.sha256(p.read_bytes()).hexdigest()] for p in paths)
    return digest({"spec": spec, "cases": cases, "files": earned, "code": code,
                   "language_policy": i18n.host_language_instruction(str(spec.get("response_language") or "auto")),
                   "runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_content(v) for v in value)
    if isinstance(value, dict):
        return text_content(value.get("text", value.get("content", "")))
    return ""


def parse_events(path, host, cwd=None):
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    tools, returned, reads, native_mcp = [], [], [], False
    usage, models, answer, error = None, [], None, None
    tool_names = {}
    for event in events:
        if host == "claude-code":
            if event.get("type") == "system" and event.get("mcp_servers"):
                native_mcp = True
            message = event.get("message") or {}
            blocks = message.get("content", []) if isinstance(message, dict) else []
            for block in blocks or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tools.append(block)
                    tool_names[block.get("id")] = block.get("name", "")
                    if block.get("name") == "Read":
                        target = block.get("input", {}).get("file_path", "")
                        reads.append(str(Path(cwd) / target) if cwd and not Path(target).is_absolute() else target)
                    elif block.get("name") in ("Grep", "Glob"):
                        target = block.get("input", {}).get("path") or cwd
                        if target:
                            reads.append(str(Path(cwd) / target) if cwd and not Path(target).is_absolute() else target)
                if block.get("type") == "tool_result" and not block.get("is_error"):
                    name = tool_names.get(block.get("tool_use_id"), "")
                    if name in ("Read", "Grep", "Glob") or name.startswith("mcp__"):
                        returned.append(text_content(block.get("content", "")))
            if event.get("type") == "result":
                usage, models = event.get("usage"), list(event.get("modelUsage") or {})
                answer = event.get("structured_output")
                error = event.get("subtype") if event.get("is_error") else None
        else:
            item = event.get("item") or {}
            if item.get("type") in ("command_execution", "mcp_tool_call"):
                tools.append(item)
                returned.append(text_content(item.get("aggregated_output", item.get("result", ""))))
                native_mcp = native_mcp or item.get("type") == "mcp_tool_call"
            if item.get("type") == "agent_message":
                try:
                    answer = json.loads(item.get("text", ""))
                except ValueError:
                    pass
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
            if event.get("type") in ("turn.failed", "error"):
                error = "host_error"
    return {"answer": answer, "usage": usage, "models": models, "error": error,
            "tools": tools, "reads": reads, "returned": returned, "native_mcp": native_mcp}


def stop(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()


def percentile(values, q):
    values = sorted(values)
    return values[max(0, math.ceil(q * len(values)) - 1)] if values else None


def evaluate(spec, cases, files, workspace, repeats=1, timeout=120, resume=False):
    # One language policy for all arms, independent of the evaluator's personal config.
    response_language = str(spec.get("response_language") or "auto")
    if i18n.response_language(response_language) == "auto" and response_language != "auto":
        raise ValueError("response_language must be auto or a BCP 47 language tag")
    if set(spec.get("arms", {})) != set(ARMS):
        raise ValueError("exactly native, old and current profiles are required")
    hosts = {p.get("host") for p in spec["arms"].values()}
    if len(hosts) != 1 or not hosts <= {"claude-code", "codex"}:
        raise ValueError("compare one explicitly selected host across all three arms")
    for profile in spec["arms"].values():
        if not isinstance(profile.get("command"), list) or not profile["command"]:
            raise ValueError("each profile requires an explicit argument list, never a shell command")
        if any(k in profile.get("env", {}) for k in ("HOME", "home", "CODEX_HOME")):
            raise ValueError("host homes must not be replaced by an evaluation")
    if not cases or any(not c.get("q") for c in cases) or repeats < 1 or timeout <= 0:
        raise ValueError("real questions, positive repeats and a positive timeout are required")
    if not files or not spec.get("read_roots"):
        raise ValueError("frozen evidence and explicit read roots are required")
    identity = fingerprint(spec, cases, files)
    output = workspace / "measurement.json"
    if resume:
        report = json.loads(output.read_text(encoding="utf-8"))
        if report["identity"] != identity or report["repeats"] != repeats:
            raise ValueError("cannot resume after inputs, profiles, code or repeats changed")
        report.pop("halted", None)
    else:
        workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
        report = {"identity": identity, "repeats": repeats, "status": "running", "rows": [],
                  "limitations": ["evidence recall is not semantic answer correctness",
                                   "one host profile; no chronological release claim",
                                   "repeats are not independent quality samples"]}
    known = {str(Path(f["path"]).resolve()): f for f in files}
    allowed = [Path(x).resolve() for x in spec.get("read_roots", [])]
    def permitted(path):
        p = Path(path).resolve()
        return any(p == root or root in p.parents for root in allowed)
    def save():
        temp = output.with_suffix(".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(output)
    save()
    done = {(r["repeat"], r["case"], r["arm"]) for r in report["rows"] if r["completed"]}
    for repeat in range(repeats):
        for i, case in enumerate(cases):
            if report.get("halted"):
                break
            arms = ARMS[(repeat+i) % 3:] + ARMS[:(repeat+i) % 3]
            for arm in arms:
                if (repeat, i, arm) in done:
                    continue
                profile = spec["arms"][arm]
                stem = workspace / ("%d-%d-%s" % (repeat, i, arm))
                schema_path = stem.with_suffix(".schema.json")
                schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
                prompt = (spec["prompt_prefix"] + "\n" + i18n.host_language_instruction(response_language)
                          + "\nQuestion: " + case["q"])
                substitutions = {"prompt": prompt, "schema": str(schema_path),
                                 "schema_json": json.dumps(SCHEMA), "artifact": str(stem)}
                command = []
                for value in profile["command"]:
                    for key, replacement in substitutions.items():
                        value = value.replace("{"+key+"}", replacement)
                    command.append(value)
                env = dict(os.environ, **profile.get("env", {}))
                start = time.perf_counter()
                code = 124
                spawn_error = None
                with stem.with_suffix(".jsonl").open("w", encoding="utf-8") as stdout, \
                     stem.with_suffix(".stderr").open("w", encoding="utf-8") as stderr:
                    try:
                        proc = subprocess.Popen(command, cwd=profile["cwd"], env=env, stdout=stdout,
                                                stderr=stderr, start_new_session=os.name != "nt")
                        code = proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        stop(proc)
                    except OSError as exc:
                        code, spawn_error = 127, type(exc).__name__
                parsed = parse_events(stem.with_suffix(".jsonl"), profile["host"], profile["cwd"])
                answer = parsed["answer"] if isinstance(parsed["answer"], dict) else {}
                parsed["error"] = parsed["error"] or spawn_error
                returned = "\n".join(parsed["returned"])
                evidence = []
                for e in (answer.get("evidence") or [])[:3]:
                    if not isinstance(e, dict):
                        continue
                    path = str(Path(e.get("path", "")).resolve())
                    item, quote = known.get(path), e.get("quote", "")
                    valid = bool(item and quote and quote in Path(path).read_text(encoding="utf-8", errors="replace"))
                    observed = bool(quote and (quote in returned or item and
                                    quote in item.get("autoloaded_text", "")))
                    evidence.append({"names": item.get("names", []) if item else [],
                                     "quote_valid": valid, "observed": observed})
                outside = [p for p in parsed["reads"] if not permitted(p)]
                contaminated = bool(outside or arm == "native" and parsed["native_mcp"])
                gold = evalinit.golds(case.get("gold", []))
                completed = code == 0 and not parsed["error"] and bool(answer) and not contaminated
                row = {"repeat": repeat, "case": i, "arm": arm, "completed": completed,
                       "seconds": time.perf_counter()-start, "exit_code": code,
                       "error": parsed["error"], "contaminated": contaminated,
                       "tool_calls": len(parsed["tools"]), "returned_chars": len(returned),
                       "retrieval_tool_calls": sum(t.get("name", "") in ("Read", "Grep", "Glob") or
                           t.get("name", "").startswith("mcp__") or
                           t.get("type") in ("command_execution", "mcp_tool_call") for t in parsed["tools"]),
                       "usage": parsed["usage"], "models": parsed["models"], "evidence": evidence,
                       "abstain": answer.get("abstain"),
                       "memory_needed": bool(gold) if "gold" in case else None,
                       "hit3": bool(gold and any(e["quote_valid"] and e["observed"] and
                           any(evalinit.gold_hit(gold, name) for name in e["names"]) for e in evidence))}
                report["rows"].append(row)
                save()
                print(json.dumps({k: row[k] for k in ("repeat", "case", "arm", "completed")}), flush=True)
                if not completed and not spec.get("continue_on_error", False):
                    report["halted"] = {"case": i, "arm": arm, "reason": "incomplete host run"}
                    break
    report["inputs_unchanged"] = fingerprint(spec, cases, files) == identity
    models = {model for r in report["rows"] for model in r["models"]}
    report["models_consistent"] = len(models) == 1
    report["summary"] = {}
    for arm in ARMS:
        rows = [r for r in report["rows"] if r["arm"] == arm and r["completed"]]
        first = [r for r in rows if r["repeat"] == 0 and r["memory_needed"] is True]
        controls = [r for r in rows if r["repeat"] == 0 and r["memory_needed"] is False]
        report["summary"][arm] = {"completed": len(rows), "quality_n": len(first),
            "evidence_hit3": sum(r["hit3"] for r in first),
            "control_n": len(controls),
            "unneeded_citations": sum(bool(r["evidence"]) for r in controls),
            "p50_s": percentile([r["seconds"] for r in rows], .5),
            "p95_s": percentile([r["seconds"] for r in rows], .95)}
    complete = {(r["repeat"], r["case"], r["arm"]) for r in report["rows"] if r["completed"]}
    report["status"] = ("complete" if len(complete) == repeats*len(cases)*3 and
                        report["inputs_unchanged"] and report["models_consistent"] else "incomplete")
    save()
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spec", required=True)
    p.add_argument("--cases", required=True)
    p.add_argument("--files", required=True, help="Frozen path/names/autoloaded evidence manifest")
    p.add_argument("--workspace", required=True)
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--resume", action="store_true")
    a = p.parse_args()
    load = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    report = evaluate(load(a.spec), load(a.cases), load(a.files), Path(a.workspace),
                      a.repeats, a.timeout, a.resume)
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
