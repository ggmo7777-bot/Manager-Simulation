"""Local server for the playable slice: python -m game.server [--port 8765] [--seed 7]. Stdlib only."""
import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .session import GameSession, InvalidInput, seed_from_code

INDEX = Path(__file__).parent / "static" / "index.html"
DEFAULT_PORT, DEFAULT_SEED = 8765, 7
SAVE_DIR = Path(__file__).resolve().parents[1] / "game-save"
SAVE_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")


class NotFound(Exception):
    pass


class Game:
    """The single in-memory game. Requests are serialised: the session wraps research functions temporarily."""
    def __init__(self, seed, save_dir=SAVE_DIR):
        self.lock = threading.Lock()
        self.save_dir = Path(save_dir)
        self.session = GameSession(seed)

    def handle(self, method, path, body):
        with self.lock:
            if (method, path) == ("GET", "/api/state"):
                return self.session.state()
            if (method, path) == ("GET", "/api/reveal"):
                return self.session.reveal()
            past_reveal = re.fullmatch(r"/api/reveal/(\d+)", path) if method == "GET" else None
            if past_reveal:
                return self.session.reveal(int(past_reveal.group(1)))
            if (method, path) == ("POST", "/api/next_season"):
                return self.session.next_season()
            if (method, path) == ("POST", "/api/new"):
                seed = seed_from_code(body["code"]) if "code" in body else body.get("seed", DEFAULT_SEED)
                self.session = GameSession(seed)
                return self.session.state()
            if (method, path) == ("POST", "/api/train"):
                return self.session.train(body.get("advice"), body.get("scout"))
            if (method, path) == ("POST", "/api/lineup"):
                return self.session.play_match(body.get("starters"), body.get("offers"))
            if (method, path) == ("GET", "/api/saves"):
                return self._list_saves()
            if (method, path) == ("POST", "/api/save"):
                return self._save(self._save_path(body.get("name")))
            if (method, path) == ("POST", "/api/load"):
                return self._load(self._save_path(body.get("name")))
        raise NotFound(path)

    def _save_path(self, name):
        if type(name) is not str or not SAVE_NAME.fullmatch(name):
            raise InvalidInput("save name must be 1-40 letters, digits, - or _")
        return self.save_dir / f"{name}.json"

    def _save(self, path):
        self.save_dir.mkdir(exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.session.save_payload(), indent=1))
        os.replace(temporary, path)  # a crash never leaves a half-written save
        return {"saved": path.stem, "day": self.session.world.day}

    def _load(self, path):
        if not path.is_file():
            raise InvalidInput(f"no save named {path.stem}")
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            raise InvalidInput("save file is not valid JSON") from None
        self.session = GameSession.resume(payload)
        return self.session.state()

    def _list_saves(self):
        saves = []
        for path in sorted(self.save_dir.glob("*.json")) if self.save_dir.is_dir() else []:
            if not SAVE_NAME.fullmatch(path.stem):
                continue
            try:
                payload = json.loads(path.read_text())
                saves.append({"name": path.stem, "seed": payload["seed"], "steps": len(payload["inputs"]),
                              "season": 1 + sum(item["kind"] == "next_season" for item in payload["inputs"])})
            except (json.JSONDecodeError, KeyError, TypeError):
                saves.append({"name": path.stem, "seed": None, "steps": None, "season": None})
        return {"saves": saves}


def make_handler(game):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload, content_type="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method):
            if method == "GET" and self.path == "/":
                return self._send(200, INDEX.read_bytes(), "text/html")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if method == "POST" else {}
                if not isinstance(body, dict):
                    raise InvalidInput("body must be a JSON object")
                self._send(200, game.handle(method, self.path, body))
            except NotFound:
                self._send(404, {"error": "not found"})
            except (InvalidInput, json.JSONDecodeError) as error:
                self._send(400, {"error": str(error)})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def log_message(self, *args):
            pass
    return Handler


def serve(port, seed):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Game(seed)))
    print(f"http://127.0.0.1:{port}/  (seed {seed})")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    serve(args.port, args.seed)
