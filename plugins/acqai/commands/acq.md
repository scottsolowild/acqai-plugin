---
description: Get ready for ACQ AI (sign in when needed), or ask it a question with your docs as the context.
---

Run the acq skill. With no task, this run is the readiness check alone: run
`ready`, which checks Playwright, the login, the route, and the chat the next
send would continue, fixes what it can (setup, the sign-in window), and says
what is next. Show the person the result and stop.

With a task, `ready` runs first, then the loop. Ask ACQ AI (Mozi) from here,
with the person's own docs as the context:

- Shape the task into one question ACQ AI can answer with mechanics.
- Show the person what is about to go out.
- Send it through the bundled script, read the answer, and refine it with a follow-up question or two in the same conversation, each one bringing in what the person's docs add.
- Report what ACQ AI said and what you added, and sort the result: adopt, later, or drop. Once the person decides, record it with the best answer at the top of the answer's file with `outcome`.

Flags in `$ARGUMENTS`:
- `-y` / `--yes` — this flag is the yes for the whole loop. Show the first question once, then send it and up to two follow-ups with `-y` on each send. Do not wait for another yes in chat. Do not ask "Send it?" or "Reply yes."
- No task (bare `/acq`, or only flags) — run `ready` and stop.

Rules:
- Nothing is sent without a yes. On a plain `/acq <task>`, that is one yes in chat per send. On `/acq -y <task>`, the `-y` is that yes for the run.
- Client names and transcripts stay home, and so does anything the person calls private.
- Never present your own analysis as ACQ AI's. If the send fails, stop and say why.

Arguments pass through: $ARGUMENTS
