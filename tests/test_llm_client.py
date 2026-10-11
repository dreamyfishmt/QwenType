"""The LLM client keeps one pooled connection across refinements."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qwentype import llm
from qwentype.aio import AsyncRunner


class FakeChatServer:
    """OpenAI-style /v1/chat/completions over HTTP/1.1 keep-alive; records each request's client port."""

    def __init__(self):
        self.requests: list[tuple[int, str]] = []  # (client port, Authorization header)
        self.system_prompts: list[str] = []
        self.reply: str | None = None  # None = echo the transcript
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.client_address[1], self.headers.get("Authorization", "")))
                outer.system_prompts.append(body["messages"][0]["content"])
                text = outer.reply
                if text is None:
                    text = body["messages"][-1]["content"].rsplit("\n", 1)[-1]  # echo the transcript
                data = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):  # quiet
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class LlmClientTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeChatServer()
        self.runner = AsyncRunner()

    def tearDown(self):
        self.runner.submit(llm.close_client()).result(5)
        self.runner.stop()
        self.server.close()

    def _refine(self, text, key="k1", system_prompt="", **limits):
        coro = llm.refine(
            text,
            base_url=self.server.url,
            api_key=key,
            model="m",
            timeout=5,
            selected_language="",
            detected_language="English",
            system_prompt=system_prompt,
            **limits,
        )
        return self.runner.submit(coro).result(10)

    def test_connection_is_reused_and_settings_apply(self):
        self.assertEqual(self._refine("hello one"), "hello one")
        self.assertEqual(self._refine("hello two"), "hello two")
        self.assertEqual(self._refine("hello three", key="k2"), "hello three")  # changed API key
        ports = {port for port, _ in self.server.requests}
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(len(ports), 1, "expected one reused connection")
        self.assertEqual([auth for _, auth in self.server.requests], ["Bearer k1", "Bearer k1", "Bearer k2"])

    def test_custom_system_prompt(self):
        self._refine("one", system_prompt="  Summarize.  ")
        self._refine("two", system_prompt="   ")  # blank = built-in prompt
        self.assertEqual(self.server.system_prompts, ["Summarize.", llm.SYSTEM_PROMPT])

    def test_length_guard_limits(self):
        long = "please summarize this rather long dictated sentence for me"
        self.server.reply = "summary"
        self.assertEqual(self._refine(long), long)  # default limits reject it, custom prompt or not
        self.assertEqual(self._refine(long, system_prompt="Summarize."), long)
        self.assertEqual(self._refine(long, min_keep=0.1), "summary")
        self.assertEqual(self._refine(long, min_keep=None, max_growth=None), "summary")  # no limit
        self.server.reply = "  "
        self.assertEqual(self._refine(long, min_keep=None, max_growth=None), long)  # empty output: fall back

    def test_new_client_after_close(self):
        self._refine("first")
        self.runner.submit(llm.close_client()).result(5)
        self._refine("second")
        self.assertEqual(len({port for port, _ in self.server.requests}), 2)


if __name__ == "__main__":
    unittest.main()
