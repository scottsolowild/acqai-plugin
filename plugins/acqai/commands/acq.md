---
description: Get ready for ACQ AI (sign in when needed), or ask it a question with your docs as the context.
---

Run the acq skill.

**First tool call, always:**

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acqai.py" ready
```

Do not interpret the task, gather docs, shape a question, or send until `ready`
exits 0. A task in `$ARGUMENTS` does not skip this. `ready` checks Playwright,
the login, the route, and the chat the next send would continue; it runs setup
and opens the sign-in window when needed.

With no task (bare `/acq`, or only flags after stripping `-y` / `--yes`), show
the ready result and stop.

With a task, after ready exits 0, run the loop:

- Shape the task into one question ACQ AI can answer with mechanics.
- Show the person what is about to go out.
- Send it through the bundled script, read the answer, and refine it with a follow-up question or two in the same conversation, each one bringing in what the person's docs add.
- Report what ACQ AI said and what you added, and sort the result: adopt, later, or drop.

Flags in `$ARGUMENTS`:
- `-y` / `--yes` — this flag is the yes for the whole run. Show the first question once, then send it and up to two follow-ups with `-y` on each send. Do not wait for another yes in chat. Do not ask "Send it?" or "Reply yes." When the loop ends, write every Adopt change into the files now (including a new offer or page when the task calls for one), record the outcome, and stop. Do not ask which pile to keep or whether to edit.
- No task (bare `/acq`, or only flags) — run `ready` and stop.

Without `-y`, wait for a yes before each send, and wait again before file edits or recording the outcome.

Rules:
- Ready first, every time. A question argument is not a skip.
- Nothing is sent without a yes. On a plain `/acq <task>`, that is one yes in chat per send. On `/acq -y <task>`, the `-y` is that yes for the sends and the Adopt edits.
- Client names and transcripts stay home, and so does anything the person calls private.
- Never present your own analysis as ACQ AI's. If the send fails, stop and say why.

Arguments pass through: $ARGUMENTS
