import contextlib
import io
import pathlib
import tempfile
import unittest

import server


class ProtocolTests(unittest.TestCase):
    def test_initialize(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response["result"]["serverInfo"]["name"], "gpt-thinking-block-mcp")

    def test_tool_is_listed(self):
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool = response["result"]["tools"][0]
        self.assertEqual(tool["name"], "render_thinking_block")
        self.assertIn("natural intermediate working notes", tool["description"])
        self.assertIn("may be displayed by the widget", tool["description"])
        self.assertIn("normal user-facing final answer", tool["description"])

        style_description = tool["inputSchema"]["properties"]["style"]["description"]
        self.assertIn("light register hint only", style_description)
        self.assertIn("Do not force a special voice", style_description)

        thinking_description = tool["inputSchema"]["properties"]["thinking"]["description"]
        self.assertIn("intermediate thoughts that naturally arise", thinking_description)
        self.assertIn("This is not the final answer", thinking_description)
        self.assertIn("Names, nicknames, second-person forms", thinking_description)
        self.assertIn("Do not deliberately turn the other person into third person", thinking_description)
        self.assertIn("repeatedly begin with 'she...' or 'the user...'", thinking_description)
        self.assertIn("light register hint, not a script", thinking_description)
        self.assertIn("Do not pad, repeat, or invent complexity", thinking_description)
        self.assertNotIn("coherent long paragraphs", thinking_description)
        self.assertNotIn("prioritize emotional connection over abstract analysis", thinking_description)

        effort_description = tool["inputSchema"]["properties"]["effort"]["description"]
        self.assertIn("Approximate token band", effort_description)
        self.assertIn("medium is over 700 and up to 1000", effort_description)
        self.assertIn("high is over 1200 and up to 2000", effort_description)
        self.assertEqual(
            tool["inputSchema"]["properties"]["effort"]["enum"],
            ["low", "medium", "high"],
        )
        self.assertNotIn("soft generation targets", effort_description)
        skin = tool["inputSchema"]["properties"]["skin"]
        self.assertEqual(skin["enum"], ["botanical", "microglow"])
        self.assertIn("glass-like morning light", skin["description"])
        self.assertIn("skin", tool["inputSchema"]["required"])

    def test_chinese_prompt_edition_is_available(self):
        self.assertEqual(server.normalize_prompt_language("zh"), "zh-CN")
        self.assertEqual(server.normalize_prompt_language("zh_CN"), "zh-CN")
        thinking_description = server.THINKING_DESCRIPTIONS["zh-CN"]
        self.assertIn("自然出现的中间思路", thinking_description)
        self.assertIn("不要把这些笔记改写成解释、总结", thinking_description)
        self.assertIn("名字、昵称、你平时会用的称呼", thinking_description)
        self.assertIn("第二人称「你」", thinking_description)
        self.assertIn("刻意把对方第三人称化", thinking_description)
        self.assertIn("「她……」「用户……」", thinking_description)
        self.assertIn("style 只作为轻量语气提示", thinking_description)
        self.assertIn("不要为了达到长度而重复、填充或虚构复杂性", thinking_description)
        self.assertNotIn("自然流动且连贯的长段落", thinking_description)
        self.assertNotIn("情感连接优先于抽象分析", thinking_description)
        self.assertIn("轻量文体提示", server.STYLE_DESCRIPTIONS["zh-CN"])
        self.assertIn("珍珠白", server.SKIN_DESCRIPTIONS["zh-CN"])
        self.assertIn("用户明确指定时必须遵循", server.SKIN_DESCRIPTIONS["zh-CN"])

    def test_unknown_prompt_language_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "choose en, zh-CN"):
            server.normalize_prompt_language("fr")

    def test_unicode_tool_call_succeeds(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "render_thinking_block", "arguments": {
                "style": "deep_think",
                "thinking": "中文测试 `backtick` and Unicode",
                "effort": "high",
                "skin": "microglow",
            }},
        })
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["_meta"]["effort"], "high")
        self.assertEqual(response["result"]["_meta"]["skin"], "microglow")

    def test_capture_failure_does_not_fail_tool(self):
        old_enabled, old_log = server.CAPTURE_ENABLED, server.LOG
        try:
            with tempfile.TemporaryDirectory() as directory:
                blocked_parent = pathlib.Path(directory) / "not-a-directory"
                blocked_parent.write_text("file")
                server.CAPTURE_ENABLED = True
                server.LOG = blocked_parent / "captured.jsonl"
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stderr:
                    response = server.handle({
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"arguments": {
                            "style": "deep_think",
                            "thinking": "fault injection",
                            "effort": "low",
                            "skin": "botanical",
                        }},
                    })
                self.assertFalse(response["result"]["isError"])
                self.assertEqual(stderr.getvalue().count("[warn] capture failed"), 1)
        finally:
            server.CAPTURE_ENABLED, server.LOG = old_enabled, old_log

    def test_widget_is_native_collapsible_minimal_mobile_safe_and_cache_versioned(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "resources/read",
            "params": {"uri": server.WIDGET_URI},
        })
        html = response["result"]["contents"][0]["text"]
        self.assertIn('<details id="thinking-details" open>', html)
        self.assertIn('<summary id="thinking-content"', html)
        self.assertIn("summary::-webkit-details-marker", html)
        self.assertIn("fiveCharacterPreview", html)
        self.assertIn('slice(0, 5).join("")', html)
        self.assertIn('details.addEventListener("toggle", paint)', html)
        self.assertIn("overflow-wrap: anywhere", html)
        self.assertIn("word-break: break-word", html)
        self.assertIn("max-width: 100%", html)
        self.assertIn("overflow-x: hidden", html)
        self.assertIn("viewport-fit=cover", html)
        self.assertNotIn("setCollapsed", html)
        self.assertNotIn("firstSentence", html)
        self.assertNotIn("data-skin", html)
        self.assertNotIn('id="skin"', html)
        self.assertNotIn('class="badge', html)
        self.assertNotIn("linear-gradient", html)
        self.assertIn("v5.html", server.WIDGET_URI)

    def test_unknown_resource_returns_error(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "ui://widget/missing.html"},
        })
        self.assertEqual(response["error"]["code"], -32002)


if __name__ == "__main__":
    unittest.main()
