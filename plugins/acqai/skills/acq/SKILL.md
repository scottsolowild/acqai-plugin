---
name: acq
description: >
  Ask ACQ AI (Mozi) from Claude Code with the person's own docs as the
  context: get ready (sign in when the session is out), shape the question,
  show it, send it through the bundled script on their yes, refine the answer
  with follow-up questions, and bring the mechanics home with the edits they
  imply. A bare "/acq" is the readiness check alone. Pass -y or --yes on /acq
  to treat that flag as the yes for the whole loop (first send and follow-ups)
  and send without waiting for another yes in chat. Trigger on "/acq", "ask
  ACQ", "ask ACQ AI", "run this past ACQ", "what would ACQ say", "set up ACQ
  AI", "sign into ACQ AI", or a pasted ACQ AI reply to sort through.
---

# acq

ACQ AI advises. The person decides. This skill runs the loop: the question,
the send, the follow-ups, the result. The script is
`${CLAUDE_PLUGIN_ROOT}/scripts/acqai.py`. Every send goes through it, and every
send needs the person's yes for that run. On `/acq -y`, the flag is that yes.

## Get ready: every run starts here, and a bare `/acq` is only this

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acqai.py" ready
```

It checks what a send needs, in order, fixes what it can, and ends with the
state of things and `ready`:

- **Playwright** missing → it runs `setup`: a private venv with Playwright and its Chromium (one to three minutes). Pass `--company "Their Company"` when they belong to more than one company on ACQ AI, so the sign-in list is clicked for them.
- **The login** missing, or the saved session expired (a headless check, a few seconds) → it opens the sign-in window at portal.acquisition.com/advisor and waits for them, up to ten minutes. So give the command a long timeout, or hand it to them to run in their own terminal. Tell them what to do in the window: sign in with the email code, click your company if a list shows, then send one short message there ("hi" is enough). The script learns the chat route from that message and closes the window on its own once it has both.
- **The route** not learned after a login → it stops (exit 1). They run `login` again and send a message, or do the by-hand step in the README (Copy as cURL, then `discover --from-curl`).
- **The chat** the next send would continue is from the other app → it stops (exit 1) and names both ways on. That is the person's choice: `--new` on the send starts a fresh conversation, and the login the message names goes back to the old one.

`ready --dry-run` names what it would run and runs nothing, browser included,
so it is the way to show the person what is coming before a long step.

With no task, show the result and stop: they are signed in and can ask with
`/acq <task>`, or here is the one thing not fixed and what to do. With a task,
go on to the loop once `ready` exits 0.

## Two ways to run

- **`/acq <task>`** — step by step. After `ready`, show each question, wait for yes in chat, send with `-y`, then ask again before each follow-up.
- **`/acq -y <task>`** (or `--yes`) — the flag is the yes. After `ready`, show the first question and the docs that ride with it so they can see what goes out, then send that question and up to two follow-ups with `-y` on each `send` without waiting for another yes in chat. Do not ask "Send it?" or "Reply yes." Say up front that `-y` already covered this run. Cap follow-ups at two. Report when the loop ends.

`-y` / `--yes` may sit anywhere in the arguments. Strip them from the task text before you interpret it. With no task left after stripping, run `ready` and stop (same as a bare `/acq`).

## The loop: `/acq <task>`

1. **Interpret.** Turn the task into one question ACQ AI can answer with mechanics: what to change, why it works, what it displaces. A question about the person's voice, or about a relationship, is a different kind of question. Say so, and ask what they want ACQ AI's read on.

2. **Gather.** Read the docs the person points at, or the folder you are in. Build the question with the situation, the numbers, the decision, and the docs that matter, pasted in whole. Ask ACQ AI to answer in the person's own terms, to say what each recommendation rests on (their docs, its pattern library, or a guess), and to name any contradiction it sees. Ask it too, when a follow-up brings in more of the situation, to build on its last answer and say what the new detail changes.

   What stays home: client names, call transcripts, anything the person calls private, and anything you would hesitate to read aloud to a stranger. When in doubt, ask before you include it. Names the person wants kept out every time go on a list the script checks, one `names add "Jane Doe"` each. When a question includes one of them, the send stops before it goes out.

3. **Show, then send.** Write the question to a temp file (an argv cannot carry a long paste). Run the send with `--dry-run` first. It exits 1 when a line says NOT ready, such as a private name or a chat from the other app, so fix that before anything else. Show the person what is about to go out: the question and the list of docs riding with it. On a step-by-step run, wait for their yes in chat. On `/acq -y`, the flag already is that yes: do not wait, send next. Then:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acqai.py" send --file /tmp/acq-question.md -y
   ```

   The `-y` on `send` is their yes, carried to the script. The script paces itself and keeps the conversation, so a follow-up lands in the same thread. Read the answer, then refine it with a follow-up question or two in the same conversation. Each question brings in something the person's docs hold and ACQ AI does not have yet, such as a number or a result they already got. Where the answer is general, ask how it plays out with that number. Where it names a step without the reason, ask what makes it work. Where it and a doc point different ways, quote the doc and ask how the two fit. Write each one as a question rather than a correction, so the next answer builds on the last one and starts from more of the situation.

   On a step-by-step run, wait for a yes before each follow-up send. On `/acq -y`, do not wait: after the show, send the first question with `-y`, then write each follow-up to a temp file, dry-run it, and send with `-y`. Still dry-run every send so a NOT ready line stops the loop.

   Pass `--new` when the next question is a different topic. To pick up an earlier conversation, `answers` lists each answer with its chat. Read that answer's file, then send with `--continue <answer>`. Give each follow-up its reason in one line with `--why "…"`, and the file keeps it above the message.

4. **Report.** Give the person the answer with two labels: what ACQ AI said, and what you added. Then sort it into three piles.
   - **Adopt.** A change to a doc, drafted in the person's own voice. ACQ AI's wording is raw material. The mechanics travel, and the words get rewritten.
   - **Later.** An idea worth keeping that changes more than today's question.
   - **Drop.** The rest, with one line on why.

   Show the edits. Make them only when asked. Once the person decides, record it at the top of the answer's file, which the send named, so the file opens as a digest: `outcome <answer> --title "<the question, one line>" --answer "<the best answer, a few bullets>" --adopt "…" --suggest "…" --later "…" --drop "…"`, one flag for each line. `adopt` is a change you made, and `suggest` a change you drafted that waits on their yes.

## When the send fails

Stop and say why, with the fix the script named: `login` for a profile that is not signed in, `setup` for a missing Playwright, the login window (or `discover`) for a route never learned, a fresh `MOZI_TOKEN` for an expired cookie on the `--http` path. A private name comes out of the question, or the person says what to write in its place. A chat from the other app is the person's choice: `--new` starts a fresh conversation, and the login the message names goes back to the old one. Then run `ready` and show it: it applies the fix it can and names the one it cannot. Never answer alone and present it as ACQ AI's. Answering alone is a different thing, and it is labeled as yours.

## Guards

- Nothing sends without a yes. On `/acq <task>`, that is one yes in chat per send. On `/acq -y <task>`, the `-y` on the command is the yes for the run (first question and follow-ups); do not ask again in chat. `-y` on each `send` carries that consent to the script.
- One question at a time on the wire. The script paces itself. A step-by-step run is a series of chat yeses; a `-y` run is the flag as yes and a short chain.
- The script reaches ACQ AI's chat and nothing else. It never posts to the community.
- The person's account is theirs. The tool does what they would do by hand, in their own browser, for their own use.
