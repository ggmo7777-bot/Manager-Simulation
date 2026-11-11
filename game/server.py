"""Local server for the playable slice: python -m game.server [--port 8765] [--seed 7]. Stdlib only."""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .session import GameSession, InvalidInput

INDEX = Path(__file__).parent / "static" / "index.html"
DEFAULT_PORT, DEFAULT_SEED = 8765, 7


class NotFound(Exception):
    pass


class Game:
    """The single in-memory game. Requests are serialised: the session wraps research functions temporarily."""
    def __init__(self, seed):
        self.lock = threading.Lock()
        self.session = GameSession(seed)

    def handle(self, method, path, body):
        with self.lock:
            if (method, path) == ("GET", "/api/state"):
                return self.session.state()
            if (method, path) == ("GET", "/api/reveal"):
                return self.session.reveal()
            if (method, path) == ("POST", "/api/new"):
                self.session = GameSession(body.get("seed", DEFAULT_SEED))
                return self.session.state()
            if (method, path) == ("POST", "/api/train"):
                return self.session.train(body.get("advice"))
            if (method, path) == ("POST", "/api/lineup"):
                return self.session.play_match(body.get("starters"))
        raise NotFound(path)


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
