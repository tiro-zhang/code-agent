"""可观察的真实 JSON-RPC Server；故意允许乱序与故障响应。"""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import subprocess
from pathlib import Path
import sys
import threading
import time


class Peer:
    def __init__(self, era="modern", log=None, **behavior):
        self.era, self.log = era, log
        self.behavior = behavior
        self.messages = []
        self.lock = threading.Lock()
        self.recoveries = {}
        self.recovery_empty = False

    def record(self, event):
        with self.lock:
            self.messages.append(event)
            if self.log:
                with Path(self.log).open("a") as file:
                    file.write(json.dumps(event) + "\n")

    def reply(self, request):
        self.record(request)
        if "id" not in request:
            if request.get("method") == "notifications/cancelled":
                time.sleep(self.behavior.get("cancel_delay", 0))
            return None
        method, params = request["method"], request.get("params", {})
        error = None
        if method == "server/discover":
            if self.era == "legacy":
                error = {"code": -32601, "message": "Method not found"}
            result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
        elif method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mewcode-fixture", "version": "1"}}
        elif method == "tools/list":
            time.sleep(self.behavior.get("list_delay", 0))
            result = {"tools": [{"name": "echo" if not params.get("cursor") else "second",
                                 "description": "返回输入文字；用于验证外部 MCP 工具。",
                                 "inputSchema": {"type": "object"}}]}
            if not params.get("cursor"):
                result["nextCursor"] = "page2"
            if self.behavior.get("repeat_cursor"):
                result["nextCursor"] = "repeat"
            if self.behavior.get("empty"):
                result = {"tools": []}
        elif method == "tools/call":
            args = params.get("arguments", {})
            if args.get("exit"):
                os._exit(0)
            if args.get("stderr"):
                sys.stderr.write("TEST_SECRET" * 100000)
                sys.stderr.flush()
            time.sleep(args.get("delay", 0))
            if args.get("error") == "header":
                error = {"code": -32020, "message": "Header mismatch"}
            if args.get("input"):
                result = {"resultType": "input_required", "inputRequests": {}, "requestState": "fixture-state"}
            else:
                result = {"content": [{"type": "text", "text": args.get("text", "fixture-ok")}],
                          "structuredContent": {"echo": args.get("text", "fixture-ok"), "pid": os.getpid()}}
                if args.get("environment"):
                    result["structuredContent"] = dict(os.environ)
                if args.get("business_error"):
                    result["isError"] = True
        else:
            result = {}
        if method in {"initialize", "server/discover"} and self.behavior.get("no_tools"):
            result["capabilities"] = {}
        if self.era == "modern" and not error:
            result.setdefault("resultType", "complete")
            if method in {"server/discover", "tools/list"}:
                result.update(cacheScope="private", ttlMs=0)
        return {"jsonrpc": "2.0", "id": request["id"], **({"error": error} if error else {"result": result})}


@contextmanager
def http_peer(era="modern", **behavior):
    peer = Peer(era, **behavior)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, body=b"", content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if era == "legacy":
                self.send_header("Mcp-Session-Id", "fixture-session")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            response = peer.reply(request)
            if response is None:
                self.respond(202)
            elif request.get("params", {}).get("arguments", {}).get("recover"):
                token = "event-" + str(request["id"])
                peer.recoveries[token] = response
                self.respond(200, f"id: {token}\nretry: 20\ndata: \n\n".encode(), "text/event-stream")
            else:
                self.respond(200, json.dumps(response).encode())

        def do_GET(self):
            token = self.headers.get("Last-Event-ID")
            peer.record({"method": "HTTP_GET", "token": token})
            if token in peer.recoveries:
                body = (f"id: {token}\nretry: 20\ndata: \n\n" if peer.recovery_empty else
                        "event: message\ndata: " + json.dumps(peer.recoveries[token]) + "\n\n")
                self.respond(200, body.encode(), "text/event-stream")
            else:
                self.respond(405)

        def do_DELETE(self):
            peer.record({"method": "HTTP_DELETE"})
            self.respond(200)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield peer, f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def stdio_main(era, log, behavior):
    peer = Peer(era, log, **behavior)
    if behavior.get("hang_exit"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    peer.record({"method": "PROCESS_START", "pid": os.getpid()})
    if behavior.get("child"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        peer.record({"method": "CHILD_START", "pid": child.pid, "pgid": os.getpgrp()})
    output_lock = threading.Lock()

    def respond(request):
        response = peer.reply(request)
        if response:
            with output_lock:
                print(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}), flush=True)
                print(json.dumps(response), flush=True)

    for line in sys.stdin:
        threading.Thread(target=respond, args=(json.loads(line),), daemon=True).start()
    if behavior.get("hang_exit"):
        while True:
            time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--era", default="modern")
    parser.add_argument("--log")
    parser.add_argument("--behavior", default="{}")
    parser.add_argument("--http-url-file")
    args = parser.parse_args()
    if args.http_url_file:
        with http_peer(args.era, log=args.log, **json.loads(args.behavior)) as (_, url):
            Path(args.http_url_file).write_text(url)
            threading.Event().wait()
    else:
        stdio_main(args.era, args.log, json.loads(args.behavior))
