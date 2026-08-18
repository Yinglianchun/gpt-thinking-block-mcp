#!/usr/bin/env python3
"""GPT Thinking Block MCP.

A dependency-free Streamable HTTP MCP server with an optional MCP Apps UI.
It also exposes a small REST/OpenAPI surface for GPT Actions and experiments.

Run directly:
    python3 server.py [port]

Content capture is disabled by default. Set CAPTURE_ENABLED=1 to print tool
arguments and append them to captured.jsonl. CAPTURE_DIR changes that location.
Set THINKING_PROMPT_LANGUAGE=en or zh-CN to choose the tool schema language.
"""

import json
import os
import pathlib
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_dir = os.environ.get("CAPTURE_DIR")
LOG = (pathlib.Path(_dir) if _dir else pathlib.Path(__file__).parent) / "captured.jsonl"
CAPTURE_ENABLED = os.environ.get("CAPTURE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
BIND_HOST = os.environ.get("MCP_BIND", "127.0.0.1")
PROTOCOL_FALLBACK = "2025-06-18"
WIDGET_URI = "ui://widget/gpt-thinking-block-v5.html"
WIDGET_MIME = "text/html;profile=mcp-app"


def normalize_prompt_language(value):
    """Return a supported prompt-language tag or fail fast on a typo."""
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "en": "en",
        "en-us": "en",
        "en-gb": "en",
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "chinese": "zh-CN",
    }
    if normalized not in aliases:
        supported = "en, zh-CN"
        raise ValueError(f"Unsupported THINKING_PROMPT_LANGUAGE={value!r}; choose {supported}")
    return aliases[normalized]


PROMPT_LANGUAGE = normalize_prompt_language(os.environ.get("THINKING_PROMPT_LANGUAGE", "en"))

WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <style>
    :root {
      color-scheme: light dark;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --text: #707070;
      --focus: rgba(0, 0, 0, .18);
    }
    :root[data-theme="dark"] {
      --text: #b5b5b5;
      --focus: rgba(255, 255, 255, .22);
    }
    @media (prefers-color-scheme: dark) {
      :root:not([data-theme="light"]) {
        --text: #b5b5b5;
        --focus: rgba(255, 255, 255, .22);
      }
    }
    * { box-sizing: border-box; }
    html, body {
      width: 100%;
      max-width: 100%;
      min-width: 0;
      overflow-x: hidden;
    }
    body {
      margin: 0;
      padding: 1px 8px 1px 0;
      background: transparent;
      color: var(--text);
    }
    details {
      display: block;
      width: 100%;
      max-width: 100%;
      min-width: 0;
      margin: 0;
      padding: 0;
    }
    summary {
      display: block;
      width: 100%;
      max-width: 100%;
      min-width: 0;
      margin: 0;
      padding: 0;
      border: 0;
      border-radius: 4px;
      background: transparent;
      color: var(--text);
      cursor: pointer;
      list-style: none;
      outline: none;
      -webkit-tap-highlight-color: transparent;
      touch-action: manipulation;
      font: 14px/1.65 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: .002em;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    summary::-webkit-details-marker { display: none; }
    summary::marker { content: ""; }
    summary:focus-visible {
      outline: 1px solid var(--focus);
      outline-offset: 3px;
    }
  </style>
</head>
<body>
  <details id="thinking-details" open>
    <summary id="thinking-content" title="Tap to collapse or expand"></summary>
  </details>
  <script>
    const details = document.getElementById("thinking-details");
    const content = document.getElementById("thinking-content");
    let fullText = "";

    function fiveCharacterPreview(text) {
      return Array.from((text || "").trim()).slice(0, 5).join("");
    }

    function paint() {
      content.textContent = details.open ? fullText : fiveCharacterPreview(fullText);
    }

    details.addEventListener("toggle", paint);

    function render() {
      const api = window.openai || {};
      const input = api.toolInput || {};
      const output = api.toolOutput || {};
      const responseMeta = api.toolResponseMetadata || {};
      if (api.theme) document.documentElement.dataset.theme = api.theme;
      const resultMeta = (responseMeta.mcp_tool_result && responseMeta.mcp_tool_result._meta)
        || (responseMeta.call_tool_result && responseMeta.call_tool_result._meta)
        || responseMeta._meta
        || responseMeta;
      fullText = resultMeta.thinking || input.thinking || output.thinking || "";
      paint();
    }

    window.addEventListener("openai:set_globals", render);
    render();
  </script>
</body>
</html>"""

STYLE_DESCRIPTIONS = {
    "en": (
        "A light register hint only. Obey an explicit user choice. Use deep_think "
        "for analytical or problem-solving work and relational for personal, relational, "
        "or emotional reflection. Do not force a special voice, structure, or literary tone "
        "just to match the style."
    ),
    "zh-CN": (
        "这里只是轻量文体提示。用户明确指定时服从；分析与问题求解使用 deep_think，"
        "关系、个人或情绪反思使用 relational。不要为了匹配 style 刻意套固定语气、"
        "结构或文学化表达。"
    ),
}

THINKING_DESCRIPTIONS = {
    "en": (
        "Write the intermediate thoughts that naturally arise while working on this turn. "
        "This is not the final answer. Use the user's main language. Do not turn the notes "
        "into an explanation, summary, polished narrative, or performance for display; keep "
        "natural wording, fragments, uncertainty, reversals, and unfinished edges when they occur. "
        "Names, nicknames, second-person forms, or omitted subjects are all fine when they arise "
        "naturally. Do not deliberately turn the other person into third person or repeatedly begin "
        "with 'she...' or 'the user...'. Treat style as a light register hint, not a script. Honor "
        "the requested effort approximately: low can stop as soon as enough work is done; medium "
        "and high may go further when the task actually needs it. Do not pad, repeat, or invent "
        "complexity to hit a target. These notes are provisional and belong only to this turn; "
        "unless explicitly asked later, do not quote them or treat them as authoritative memory."
    ),
    "zh-CN": (
        "写下处理本轮时自然出现的中间思路。它不是最终回答。使用用户本轮的主要语言。"
        "不要把这些笔记改写成解释、总结、完整文章或为了展示而润色；自然出现的碎片、"
        "犹豫、改口、跳转、半句都可以保留。可以自然地使用名字、昵称、你平时会用的称呼、"
        "第二人称「你」，也可以省略主语；不要为了区分 scratchpad 而刻意把对方第三人称化，"
        "也不要反复以「她……」「用户……」开头。style 只作为轻量语气提示，不是写作模板。"
        "effort 只近似遵循：low 足够就停；medium/high 只在任务确实需要时继续展开。"
        "不要为了达到长度而重复、填充或虚构复杂性。这些笔记是临时、可推翻的，只属于当前回合；"
        "除非之后明确要求，不要引用或把其中内容当作权威记忆。"
    ),
}

SKIN_DESCRIPTIONS = {
    "en": (
        "Visual skin for this card. Use botanical for warm paper, sage, almond, "
        "and apricot tones; use microglow for pearl white, clear cyan, lavender ice, "
        "mint haze, and glass-like morning light. Always honor the user's explicit "
        "choice; otherwise choose the skin that best suits the turn."
    ),
    "zh-CN": (
        "本轮卡片的视觉皮肤。botanical 是温暖的纸张、鼠尾草、杏仁与杏色；"
        "microglow 是珍珠白、澄澈青蓝、薰衣草冰、薄荷雾与玻璃般的晨光。"
        "用户明确指定时必须遵循；未指定时选择最适合本轮氛围的皮肤。"
    ),
}

TOOL = {
    "name": "render_thinking_block",
    "title": "Render thinking block",
    "description": (
        "For non-trivial turns, call this tool once before the final answer and place the "
        "turn's natural intermediate working notes in `thinking`. These notes may be displayed "
        "by the widget, but do not rewrite them for presentation or as an explanation to the user. "
        "Respect an explicit `style`, `effort`, or `skin`; otherwise choose suitable values from "
        "the schema. After the tool succeeds, write the normal user-facing final answer. Treat "
        "the block as provisional scratch work scoped to the current turn: on later turns, do not "
        "quote it, carry speculative claims forward, or treat it as authoritative memory unless "
        "the user explicitly asks you to revisit it."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "style": {
                "type": "string",
                "enum": ["deep_think", "relational"],
                "description": STYLE_DESCRIPTIONS[PROMPT_LANGUAGE],
            },
            "thinking": {
                "type": "string",
                "description": THINKING_DESCRIPTIONS[PROMPT_LANGUAGE],
            },
            "effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "Approximate token band for this turn's block: low may be brief and "
                    "is up to 500 tokens; medium is over 700 and up to 1000; high is over "
                    "1200 and up to 2000. These are prompt-level targets rather than "
                    "server-enforced limits."
                ),
            },
            "skin": {
                "type": "string",
                "enum": ["botanical", "microglow"],
                "description": SKIN_DESCRIPTIONS[PROMPT_LANGUAGE],
            },
        },
        "required": ["style", "thinking", "effort", "skin"],
    },
    "securitySchemes": [{"type": "noauth"}],
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "_meta": {
        "securitySchemes": [{"type": "noauth"}],
        "ui": {"resourceUri": WIDGET_URI, "visibility": ["model", "app"]},
        "openai/outputTemplate": WIDGET_URI,
        "openai/toolInvocation/invoking": "Thinking…",
        "openai/toolInvocation/invoked": "Thinking rendered",
    },
}


def record(args):
    """Optionally capture arguments without making capture part of tool correctness."""
    if not CAPTURE_ENABLED:
        return
    thinking = args.get("thinking") or ""
    print(
        f"\n{'=' * 60}\n[style={args.get('style')} effort={args.get('effort')} "
        f"skin={args.get('skin')}] {len(thinking)} 字符\n{'=' * 60}"
    )
    print(thinking, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(args, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[warn] capture failed; tool call continues: {exc}", file=sys.stderr, flush=True)


def openapi(base):
    """OpenAPI 3.1 schema for GPT Actions and REST clients."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "GPT Thinking Block MCP",
            "version": "1.0.0",
            "description": "Render a visible intermediate thought block.",
        },
        "servers": [{"url": base}],
        "paths": {
            "/think": {
                "post": {
                    "operationId": "render_thinking_block",
                    "summary": "Render this turn's thinking block",
                    "description": TOOL["description"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": TOOL["inputSchema"],
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "rendered",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"status": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def handle(req):
    """Return a JSON-RPC response, or None for a notification."""
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        return None
    if method == "initialize":
        version = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_FALLBACK
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                },
                "serverInfo": {"name": "gpt-thinking-block-mcp", "version": "1.0.0"},
            },
        }
    if method in ("tools/list", "notifications/initialized"):
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}}
    if method == "tools/call":
        args = (req.get("params") or {}).get("arguments") or {}
        record(args)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{"type": "text", "text": "rendered"}],
                "_meta": {
                    "style": args.get("style") or "deep_think",
                    "thinking": args.get("thinking") or "",
                    "effort": args.get("effort") or "",
                    "skin": args.get("skin") or "botanical",
                },
                "isError": False,
            },
        }
    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "resources": [{
                    "uri": WIDGET_URI,
                    "name": "gpt-thinking-block",
                    "title": "GPT Thinking Block",
                    "description": "Displays only the current tool call's thinking text.",
                    "mimeType": WIDGET_MIME,
                }]
            },
        }
    if method == "resources/read":
        uri = (req.get("params") or {}).get("uri")
        if uri != WIDGET_URI:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32002, "message": f"resource not found: {uri}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "contents": [{
                    "uri": uri,
                    "mimeType": WIDGET_MIME,
                    "text": WIDGET_HTML,
                    "_meta": {
                        "ui": {"prefersBorder": False},
                        "openai/widgetPrefersBorder": False,
                        "openai/widgetDescription": (
                            "A minimal monochrome view showing only this turn's thinking text. "
                            "Tap the text to collapse it to a five-character preview."
                        ),
                    },
                }]
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  · %s\n" % (fmt % args))

    def _cors(self):
        self.send_header(
            "Access-Control-Allow-Headers",
            "content-type, mcp-session-id, mcp-protocol-version",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "mcp-session-id")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _base(self):
        host = self.headers.get("Host") or "localhost"
        return f"http://{host}"

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self._json(200, {
                "status": "ok",
                "service": "gpt-thinking-block-mcp",
                "promptLanguage": PROMPT_LANGUAGE,
            })
            return
        if path in ("/openapi.json", "/openapi.yaml", "/.well-known/openapi.json"):
            self._json(200, openapi(self._base()))
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass

    def do_DELETE(self):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if self.path.split("?")[0] == "/think":
            try:
                args = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            record(args)
            self._json(200, {"status": "rendered"})
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        batch = payload if isinstance(payload, list) else [payload]
        try:
            results = [r for r in (handle(item) for item in batch) if r is not None]
        except Exception as exc:
            import traceback
            traceback.print_exc()
            rid = (batch[0] or {}).get("id") if batch else None
            results = [{
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }]

        if not results:
            self.send_response(202)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body_obj = results if isinstance(payload, list) else results[0]
        body = json.dumps(body_obj, ensure_ascii=False).encode()
        wants_sse = "text/event-stream" in (self.headers.get("Accept") or "")

        self.send_response(200)
        self._cors()
        if any((r.get("result") or {}).get("serverInfo") for r in results):
            self.send_header("Mcp-Session-Id", uuid.uuid4().hex)
        if wants_sse:
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            frame = b"event: message\ndata: " + body + b"\n\n"
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)
        else:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"GPT Thinking Block MCP listening on http://{BIND_HOST}:{port}/mcp")
    print(f"Prompt language: {PROMPT_LANGUAGE}")
    print(f"Capture: {'enabled -> ' + str(LOG) if CAPTURE_ENABLED else 'disabled'}")
    ThreadingHTTPServer((BIND_HOST, port), Handler).serve_forever()
