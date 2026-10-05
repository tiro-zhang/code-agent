"""从本轮合成夹具独立核验事件、请求、产物和打包，不输出模型凭据。"""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from mewcode.config import load_config as load_model_config
from mewcode.hooks.config import load_config


REPO = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
ROOTS = {
    "enhanced": Path("/private/tmp/mewcode-hooks-e2e-20261005"),
    "plain": Path("/private/tmp/mewcode-hooks-plain-e2e-20261005"),
}


def records(root, name):
    return [json.loads(line) for line in (root / name).read_text().splitlines()]


def audit_run(root):
    events = records(root, "events.jsonl")
    requests = records(root, "requests.jsonl")
    actions = records(root, "actions.jsonl")
    sessions = {}
    request_offset = 0
    for identity in dict.fromkeys(item["session_id"] for item in events):
        current = [item for item in events if item["session_id"] == identity]
        counts = Counter(item["event"] for item in current)
        size = counts["message.before_request"]
        markers = [item["marker"] for item in requests[request_offset:request_offset + size]]
        request_offset += size
        sessions[identity] = {"events": dict(counts), "work_request_markers": markers}
        assert counts["session.start"] == counts["session.end"] == 1
        assert counts["turn.start"] == counts["turn.end"] == counts["message.user"]
        assert markers and markers[0] and sum(markers) == 1
    after = [item for item in events if item["event"] == "tool.after"]
    after_keys = [(item["session_id"], item["tool"]) for item in after]
    assert len(after_keys) == len(set(after_keys))
    assert all((item["session_id"], item["tool"]) in after_keys
               for item in events if item["event"] == "tool.before")
    assert request_offset == len(requests)
    assert all(item["name"] in {"read_file", "write_file"} for item in after)
    process_status = {}
    for name in ("timeout", "cancel"):
        marker = root / (name + ".started")
        if not marker.exists():
            continue
        pid = int(marker.read_text())
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        process_status[name] = {"pid": pid, "alive": alive,
                                "delayed_file_exists": (root / (name + ".leaked")).exists()}
        assert not alive and not process_status[name]["delayed_file_exists"]
    products = {}
    for name in ("format.txt", "alternative.txt", "plain-output.txt"):
        path = root / name
        if path.exists():
            data = path.read_bytes()
            products[name] = {"text": data.decode(), "sha256": hashlib.sha256(data).hexdigest()}
    assert not (root / "protected.txt").exists()
    assert not (root / "planfile.txt").exists()
    if "format.txt" in products:
        assert products["format.txt"]["text"] == "HELLO HOOKS\n"
        assert products["alternative.txt"]["text"] == "DENIED TEST\n"
    if "plain-output.txt" in products:
        assert products["plain-output.txt"]["text"] == "HELLO PLAIN HOOKS\n"
    return {"root": str(root), "sessions": sessions, "requests": len(requests),
            "model_ids": sorted({item["model"] for item in requests}),
            "actions": dict(Counter(item["action"] for item in actions)),
            "tool_results": dict(Counter(item["name"] for item in after)),
            "errors": [item["error"] for item in after if item["error"]],
            "products": products, "local_processes": process_status,
            "protected_file_exists": False, "plan_file_exists": False}


def audit_docs_and_package():
    readme = (REPO / "README.md").read_text().split("## 生命周期 Hook", 1)[1]
    sample = re.search(r"```yaml\n(.*?)\n```", readme, re.S).group(1)
    with tempfile.TemporaryDirectory(prefix="mewcode-hook-docs-") as name:
        root = Path(name)
        (root / ".mewcode").mkdir()
        (root / ".mewcode/hooks.yaml").write_text(sample)
        config = load_config(root)
        assert not config.diagnostics and len(config.rules) == 4
    wheel = REPO / "dist/mewcode-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as package:
        modules = sorted(name for name in package.namelist() if name.startswith("mewcode/hooks/"))
        assert len(modules) == 8 and "mewcode/matching.py" in package.namelist()
        metadata_name = next(name for name in package.namelist() if name.endswith("/METADATA"))
        dependencies = [line for line in package.read(metadata_name).decode().splitlines()
                        if line.startswith("Requires-Dist: httpx2")]
        assert len(dependencies) == 1
        python_files = [name for name in package.namelist() if name.startswith("mewcode/") and name.endswith(".py")]
        assert all(package.read(name) == (REPO / "src" / name).read_bytes() for name in python_files)
    # 只输出命中数，绝不打印真实密钥或其前缀。
    keys = [load_model_config(REPO / name).api_key for name in (".env.claude", ".env")]
    public = [REPO / "README.md", REPO / "checklist.md", REPO / "pyproject.toml", REPO / "uv.lock"]
    public.extend((REPO / "src/mewcode").rglob("*.py"))
    public.extend((REPO / "tests").rglob("*.py"))
    public.extend(path for path in FOLDER.iterdir() if path.is_file())
    change_root = REPO / "openspec/changes/add-lifecycle-hooks"
    if not change_root.exists():
        change_root = REPO / "openspec/changes/archive/2026-10-05-add-lifecycle-hooks"
    assert change_root.is_dir()
    public.extend(path for path in change_root.rglob("*") if path.is_file())
    main_spec = REPO / "openspec/specs/lifecycle-hooks/spec.md"
    if main_spec.exists():
        public.append(main_spec)
    hits = sum(bool(key and key.encode() in path.read_bytes()) for key in keys for path in public)
    assert hits == 0
    return {"readme_rules_loaded": len(config.rules), "wheel_modules": modules,
            "wheel_python_files_equal_current_source": len(python_files),
            "direct_http_dependency": dependencies, "real_key_hits_in_public_artifacts": hits}


def main():
    result = {name: audit_run(root) for name, root in ROOTS.items()}
    result["docs_and_package"] = audit_docs_and_package()
    result["total_real_model_requests"] = sum(result[name]["requests"] for name in ROOTS)
    (FOLDER / "request-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for name, root in ROOTS.items():
        for source in ("events.jsonl", "requests.jsonl", "actions.jsonl", "terminal.jsonl"):
            (FOLDER / (name + "-" + source)).write_text((root / source).read_text())
    print(json.dumps({"real_requests": result["total_real_model_requests"],
                      "readme_rules": result["docs_and_package"]["readme_rules_loaded"],
                      "real_key_hits": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
