"""A portal answer holds the reply text alone, and only a whole one passes.

The portal's chat-stream sends typed events, and its reasoning events carry a
`delta` the way its text events do. extract_answer kept the `delta` of every
event, so the first working portal send (2026-09-24 14:49, asking for one
word) logged a reasoning paragraph with "received" glued to its end. A typed
stream now reads the way the portal's own reader does, and one that carries
no reply text raises instead of answering with nothing or with the reasoning.

extract_answer also raises when a reply stops partway. It had returned the
part as a whole answer, so the send exited 0. The error it raises now,
MoziCutShort, carries the part, so a caller can keep it on the record while
the send fails.

Where a reply ends follows the portal's chat page, as its client bundle
reads the sandbar stream (read 2026-09-25). The read stops at the first
closing event, and nothing after it counts. When nothing streamed, the
closing event's own text is the reply. A close on an error event, on
response.error, or with finishReason "error" fails the reply, and a stream
with no closing event is one the page calls interrupted. The page adds and
clears nothing for text-reset there, and it takes a textDelta string on any
event. The older app's data stream stops at its first 3: error part: after
0: text it raises MoziCutShort, and with none it raises MoziError with its
reason, where the raw stream had come back as the answer.

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

    def test_the_older_apps_error_part_after_text_cuts_it_short(self):
        raw = (b'f:{"messageId":"m1"}\n'
               b'g:"thinking it through"\n'
               b'0:"Price it at "\n'
               b'0:"$4,500 and"\n'
               b'3:"The model is overloaded."\n')
        with self.assertRaises(mozilib.MoziCutShort) as caught:
            mozilib.extract_answer(raw, {})
        self.assertEqual(caught.exception.partial, PART)
        self.assertIn(OVERLOADED, str(caught.exception))

    def test_the_older_apps_error_part_with_no_text_names_its_reason(self):
        raw = (b'f:{"messageId":"m1"}\n'
               b'g:"thinking it through"\n'
               b'3:"The model is overloaded."\n'
               b'0:"never read"\n')
        with self.assertRaises(mozilib.MoziError) as caught:
            mozilib.extract_answer(raw, {})
        err = caught.exception
        self.assertNotIsInstance(err, mozilib.MoziCutShort)
        self.assertIn(OVERLOADED, str(err))
        self.assertNotIn("thinking", str(err))


class WhereTheReplyEnds(unittest.TestCase):
    """The first closing event ends the read, the way the portal's page reads
    its sandbar stream."""

    def test_nothing_after_the_closing_event_counts(self):
        raw = _sse({"type": "text-delta", "delta": "rece"},
                   {"type": "finish", "finishReason": "stop"},
                   {"type": "text-delta", "delta": "ived"})
        self.assertEqual(mozilib.extract_answer(raw, CFG), "rece")

    def test_a_closing_event_carries_the_reply_when_nothing_streamed(self):
        parts = [{"type": "text", "text": "who"},
                 {"type": "reasoning", "text": REASONING},
                 {"type": "text", "text": "le"}]
        for closing in ({"type": "finish", "text": "whole"},
                        {"type": "response.completed",
                         "responseMessage": {"content": "whole"}},
                        {"type": "finish", "responseMessage": {"parts": parts}}):
            with self.subTest(closing=closing):
                raw = _sse({"type": "reasoning-delta", "delta": REASONING},
                           closing)
                self.assertEqual(mozilib.extract_answer(raw, CFG), "whole")
        # Text that streamed wins over the closing event's.
        raw = _sse({"type": "text-delta", "delta": "streamed"},
                   {"type": "finish", "text": "whole"})
        self.assertEqual(mozilib.extract_answer(raw, CFG), "streamed")

    def test_a_response_that_completed_to_call_tools_goes_on(self):
        raw = _sse({"type": "response.completed", "finishReason": "tool-calls"},
                   {"type": "text-delta", "delta": "after the tools"},
                   {"type": "finish", "finishReason": "stop"})
        self.assertEqual(mozilib.extract_answer(raw, CFG), "after the tools")

    def test_an_error_before_any_text_ends_the_read(self):
        raw = _sse({"type": "error", "errorText": OVERLOADED},
                   {"type": "text-delta", "delta": "never read"},
                   {"type": "finish", "finishReason": "stop"})
        with self.assertRaises(mozilib.MoziError) as caught:
            mozilib.extract_answer(raw, CFG)
        self.assertNotIsInstance(caught.exception, mozilib.MoziCutShort)
        self.assertIn(OVERLOADED, str(caught.exception))

    def test_each_close_on_an_error_cuts_the_reply_short(self):
        for closing in ({"type": "finish", "finishReason": "error"},
                        {"type": "response.error"},
                        {"type": "error", "error": {"message": OVERLOADED}}):
            with self.subTest(closing=closing):
                raw = _sse({"type": "text-delta", "delta": PART}, closing)
                with self.assertRaises(mozilib.MoziCutShort) as caught:
                    mozilib.extract_answer(raw, CFG)
                self.assertEqual(caught.exception.partial, PART)
        self.assertIn(OVERLOADED, str(caught.exception))

    def test_a_stream_with_no_closing_event_was_interrupted(self):
        raw = _sse({"type": "text-delta", "delta": PART})
        with self.assertRaises(mozilib.MoziCutShort) as caught:
            mozilib.extract_answer(raw, CFG)
        self.assertEqual(caught.exception.partial, PART)
        self.assertIn("before it finished", str(caught.exception))
        raw = _sse({"type": "reasoning-delta", "delta": REASONING})
        with self.assertRaises(mozilib.MoziError) as caught:
            mozilib.extract_answer(raw, CFG)
        self.assertNotIsInstance(caught.exception, mozilib.MoziCutShort)


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
