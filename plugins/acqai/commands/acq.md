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
- Show the person what is about to go out, and wait for their yes.
- Send it through the bundled script, read the answer, and refine it with a follow-up question or two in the same conversation, each one bringing in what the person's docs add.
- Report what ACQ AI said and what you added, and sort the result: adopt, later, or drop. Once the person decides, record it with the best answer at the top of the answer's file with `outcome`.

Rules:
- Nothing is sent without a yes. The `-y` on the send is that yes, carried to the script.
- Client names and transcripts stay home, and so does anything the person calls private.
- Never present your own analysis as ACQ AI's. If the send fails, stop and say why.

Arguments pass through: $ARGUMENTS
