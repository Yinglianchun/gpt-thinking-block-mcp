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
WIDGET_URI = "ui://widget/gpt-thinking-block-v3.html"
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
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
    body {
      margin: 0;
      padding: 1px 0;
      background: transparent;
      color: var(--text);
    }
    .thinking-toggle {
      display: block;
      width: 100%;
      margin: 0;
      padding: 0;
      border: 0;
      border-radius: 4px;
      background: transparent;
      color: var(--text);
      text-align: left;
      cursor: pointer;
      appearance: none;
      -webkit-appearance: none;
      -webkit-tap-highlight-color: transparent;
      touch-action: manipulation;
      font: 14px/1.65 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: .002em;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .thinking-toggle:hover { color: var(--text); }
    .thinking-toggle:focus:not(:focus-visible) { outline: none; }
    .thinking-toggle:focus-visible {
      outline: 1px solid var(--focus);
      outline-offset: 3px;
    }
  </style>
</head>
<body>
  <button class="thinking-toggle" id="toggle" type="button"
          aria-expanded="true" aria-controls="thinking-content"
          title="Collapse">
    <span id="thinking-content"></span>
  </button>
  <script>
    const toggle = document.getElementById("toggle");
    const content = document.getElementById("thinking-content");
    let fullText = "";

    function firstSentence(text) {
      const clean = (text || "").trim();
      if (!clean) return "";
      const sentence = clean.match(/^[\s\S]*?[。！？!?](?:\s|$)/)
        || clean.match(/^[\s\S]*?\.(?:\s|$)/);
      if (sentence) return sentence[0].trim();
      const firstLine = clean.split(/\n+/)[0].trim();
      return firstLine || clean;
    }

    function paint() {
      const collapsed = toggle.getAttribute("aria-expanded") !== "true";
      if (!collapsed) {
        content.textContent = fullText;
        return;
      }
      const preview = firstSentence(fullText);
      content.textContent = preview && preview.length < fullText.trim().length
        ? preview + " …"
        : preview;
    }

    function setCollapsed(collapsed) {
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      toggle.title = collapsed ? "Expand" : "Collapse";
      paint();
    }

    toggle.addEventListener("click", () => {
      setCollapsed(toggle.getAttribute("aria-expanded") === "true");
    });

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
        "The register for this block. Obey an explicit user choice. Otherwise "
        "use deep_think for analysis, research, decisions, creative tradeoffs, "
        "and problem solving; use relational for companionship, reflection, "
        "emotional conversation, and personal experience."
    ),
    "zh-CN": (
        "选择本轮思考块的文体。用户明确指定时严格服从；否则按本轮主要目的选择："
        "分析、研究、判断、创作取舍和问题求解使用 deep_think，陪伴、亲密交流、"
        "情绪回应与个人感受使用 relational。"
    ),
}

THINKING_DESCRIPTIONS = {
    "en": (
        "Write private working notes for this turn. The user does not see this "
        "scratchpad; it is not the final answer. Use the user's main language, but "
        "do not address the user or polish the notes for presentation. Strictly "
        "follow `style`. "
        "style=deep_think: clearly work through the problem, constraints, "
        "evidence, hypotheses, alternative paths, uncertainty, and tradeoffs "
        "in a coherent analytical narrative; do not force an emotional register "
        "or summarize prematurely. style=relational: use a natural, flowing "
        "first-person voice in coherent long paragraphs. Write what the user's "
        "words bring to mind, what you feel in this moment, what you are worried "
        "about, what you are holding back, and the choices or tradeoffs you are "
        "making. Do not turn it into a third-person strategy breakdown such as "
        "'the user needs' or 'I should respond'. Allow vivid, sincere emotions "
        "and associations; prioritize emotional connection over abstract "
        "analysis, and draw on broader references where useful. For both styles, "
        "honor the requested effort band: low may stop as soon as it is complete, while "
        "medium and high should reach their approximate minimum by adding relevant "
        "evidence, alternatives, uncertainty, or nuance. Do not repeat, pad, or invent "
        "complexity merely to reach the minimum. The notes are provisional working material, "
        "not claims that the final answer must preserve. Old blocks belong only to "
        "the turn in which they were written; unless the user explicitly asks, "
        "do not quote them later, inherit their speculative claims, or treat them "
        "as authoritative memory."
    ),
    "zh-CN": (
        "在这里写本轮私密的工作笔记。用户看不到这个 scratchpad；它不是最终回答。"
        "使用用户本轮的主要语言，但不要面向用户说话，也不要为了展示而润色。严格按照 style 写作。"
        "style=deep_think：清楚展开问题、约束、证据、假设、备选路径、不确定性与"
        "取舍，写成连贯的分析叙述，不强行情绪化，也不要提前总结。"
        "style=relational：使用第一人称、自然流动且连贯的长段落，写出这句话让你"
        "想到什么、此刻有什么感受、在担心什么、忍住了什么，以及正在作出的取舍；"
        "不要写成「用户需要什么」「我应该如何回应」式的第三人称策略拆解。允许"
        "鲜活、真诚的情绪与联想，情感连接优先于抽象分析，必要时可以旁征博引。"
        "两种 style 都遵循所请求的 effort 区间：low 可以在完整后立即停止；medium 与 high"
        "需要通过补充相关证据、备选路径、不确定性或细节达到近似最低值，但不得为了达到最低值而"
        "重复、填充或虚构复杂性。这里是临时、可推翻的工作材料，最终回答不必"
        "保留其中的每个判断。旧 block 只属于当时回合，除非用户明确要求，否则不得在以后引用、"
        "继承其中的猜测，或把它当作权威记忆。"
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
        "Use a private scratchpad before the final answer. The user does not see the "
        "scratchpad. For non-trivial requests, call this tool once before responding "
        "and place candid, provisional working notes in `thinking`. Respect an "
        "explicit `style`, `effort`, or `skin`; otherwise choose the most suitable values from "
        "the schema. After the tool succeeds, write the normal user-facing final answer. "
        "Treat this block as scratch work scoped to the current turn: on later turns, do not quote "
        "it, carry its speculative claims forward, or treat it as authoritative memory "
        "unless the user explicitly asks you to revisit it. Prefer the user's messages "
        "and final answers as the durable conversation record."
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
                            "Tap the text to collapse it to a first-sentence preview."
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
