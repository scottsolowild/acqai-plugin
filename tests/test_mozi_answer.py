"""A portal answer holds the reply text alone, and only a whole one passes.

The portal's chat-stream sends typed events, and its reasoning events carry a
`delta` the way its text events do. extract_answer kept the `delta` of every
event, so the first working portal send (2026-09-24 14:49, asking for one
word) logged a reasoning paragraph with "received" glued to its end. A typed
stream now reads the way the portal's own reader does, and one that carries
no reply text raises instead of answering with nothing or with the reasoning.

extract_answer also raises when a stream sends part of a reply and then an
error. It had returned the part as a whole answer, so the send exited 0.
The error it raises now, MoziCutShort, carries the part, so a caller can
keep it on the record while the send fails.

This file is shared byte for byte with the acqai plugin repo's tests/, so
it imports only the transport pair.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mozilib  # noqa: E402

CFG = {"url": "https://portal.acquisition.com/api/sandbar/chat-stream",
       "stream": True}
PART = "Price it at $4,500 and"
OVERLOADED = "The model is overloaded."
REASONING = ("**Navigating tool instructions**\n\nThe ask is one word. I'll "
             "start with a minimal searchBooks query, then I'll reply with "
             '"received."')


def _sse(*events: dict) -> bytes:
    """The events as the portal streams them, one data line each."""
    lines = [f"data: {json.dumps(e)}" for e in events] + ["data: [DONE]"]
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


class PortalStream(unittest.TestCase):
    def test_reasoning_between_text_deltas_stays_out_of_the_answer(self):
        raw = _sse(
            {"type": "start", "messageId": "m1"},
            {"type": "reasoning-start", "id": "r1"},
            {"type": "reasoning-delta", "id": "r1", "delta": REASONING[:40]},
            {"type": "reasoning-delta", "id": "r1", "delta": REASONING[40:]},
            {"type": "reasoning-end", "id": "r1"},
            {"type": "tool-input-available", "toolCallId": "c1",
             "toolName": "searchBooks", "input": {"query": "received"}},
            {"type": "tool-result-available", "toolCallId": "c1",
             "output": {"results": []}},
            {"type": "text-start", "id": "t1"},
            {"type": "text-delta", "id": "t1", "delta": "rece"},
            {"type": "response.reasoning_summary_text.delta",
             "delta": " and one more thought"},
            {"type": "text-delta", "id": "t1", "delta": "ived"},
            {"type": "text-end", "id": "t1"},
            {"type": "finish", "finishReason": "stop"},
        )
        self.assertEqual(mozilib.extract_answer(raw, CFG), "received")

    def test_each_text_shape_the_portal_reads(self):
        for event, want in (
            ({"type": "text-delta", "delta": "one"}, "one"),
            ({"type": "response.output_text.delta", "delta": "two"}, "two"),
            ({"type": "text", "text": "three"}, "three"),
            ({"type": "text-delta", "textDelta": "four"}, "four"),
        ):
            with self.subTest(event=event):
                raw = _sse({"type": "reasoning-delta", "delta": REASONING},
                           event, {"type": "finish"})
                self.assertEqual(mozilib.extract_answer(raw, CFG), want)

    def test_no_text_event_raises_and_names_the_events(self):
        raw = _sse(
            {"type": "reasoning-delta", "delta": REASONING},
            {"type": "tool-input-available", "toolCallId": "c1",
             "toolName": "searchBooks", "input": {}},
            {"type": "finish", "finishReason": "stop"},
        )
        with self.assertRaises(mozilib.MoziError) as caught:
            mozilib.extract_answer(raw, CFG)
        why = str(caught.exception)
        for kind in ("reasoning-delta", "tool-input-available", "finish"):
            self.assertIn(kind, why)
        self.assertNotIn("Navigating", why)

    def test_an_error_event_gives_its_own_reason(self):
        raw = _sse({"type": "reasoning-delta", "delta": REASONING},
                   {"type": "error", "errorText": OVERLOADED})
        with self.assertRaises(mozilib.MoziError) as caught:
            mozilib.extract_answer(raw, CFG)
        self.assertIn(OVERLOADED, str(caught.exception))
        # Reasoning is not reply text, so nothing was cut short.
        self.assertNotIsInstance(caught.exception, mozilib.MoziCutShort)


class CutShort(unittest.TestCase):
    """Part of a reply, then an error: the send fails and carries the part."""

    def test_an_error_after_reply_text_raises_with_the_part(self):
        raw = _sse({"type": "reasoning-delta", "delta": REASONING},
                   {"type": "text-delta", "delta": "Price it at "},
                   {"type": "text-delta", "delta": "$4,500 and"},
                   {"type": "error", "errorText": OVERLOADED})
        with self.assertRaises(mozilib.MoziCutShort) as caught:
            mozilib.extract_answer(raw, CFG)
        err = caught.exception
        self.assertIsInstance(err, mozilib.MoziError)
        self.assertEqual(err.partial, PART)
        self.assertIn(OVERLOADED, str(err))
        self.assertNotIn("Navigating", str(err))
        kept = err.logged()
        self.assertTrue(kept.startswith(PART + "\n\n"), kept)
        self.assertIn("Cut short here", kept)
        self.assertIn(OVERLOADED, kept)

    def test_an_error_with_no_reason_still_cuts_it_short(self):
        raw = _sse({"type": "text-delta", "delta": PART}, {"type": "error"})
        with self.assertRaises(mozilib.MoziCutShort) as caught:
            mozilib.extract_answer(raw, CFG)
        self.assertEqual(caught.exception.partial, PART)
        self.assertIn("no reason", str(caught.exception))


class OtherShapes(unittest.TestCase):
    """The shapes the portal's reader does not govern read as they did."""

    def test_legacy_data_stream_takes_its_text_parts(self):
        raw = (b'f:{"messageId":"m1"}\n'
               b'g:"thinking it through"\n'
               b'0:"Hel"\n'
               b'0:"lo \\"you\\""\n'
               b'd:{"finishReason":"stop"}\n')
        self.assertEqual(mozilib.extract_answer(raw, {}), 'Hello "you"')

    def test_plain_json_follows_the_answer_path(self):
        raw = json.dumps({"data": {"reply": "plain"}}).encode("utf-8")
        self.assertEqual(
            mozilib.extract_answer(raw, {"answer_path": ["data", "reply"]}),
            "plain")

    def test_untyped_json_lines_keep_every_delta(self):
        raw = b'data: {"delta":"Hel"}\ndata: {"delta":"lo"}\ndata: [DONE]\n'
        self.assertEqual(mozilib.extract_answer(raw, {}), "Hello")


if __name__ == "__main__":
    unittest.main()
