---
description: Ask ACQ AI a question with your docs as the context, or set it up.
---

Run the acq skill. Ask ACQ AI (Mozi) from here, with the person's own docs as
the context:

- Shape the task into one question ACQ AI can answer with mechanics.
- Show the person what is about to go out, and wait for their yes.
- Send it through the bundled script, read the answer, and refine it with a follow-up question or two in the same conversation, each one bringing in what the person's docs add.
- Report what ACQ AI said and what you added, and sort the result: adopt, later, or drop. Once the person decides, record it on the answer's file with `outcome`.

Rules:
- Nothing is sent without a yes. The `-y` on the send is that yes, carried to the script.
- Client names and transcripts stay home, and so does anything the person calls private.
- Never present your own analysis as ACQ AI's. If the send fails, stop and say why.

Arguments pass through: $ARGUMENTS
