---
name: acq
description: >
  Ask ACQ AI (Mozi) from Claude Code or Cowork with the person's own docs as
  the context: get ready first (sign in when the session is out; a question
  argument does not skip this), shape the question, show it, send it through
  the plugin's acqai tools on their yes, refine the answer with follow-up
  questions, and bring the mechanics home with the edits they imply. A bare
  "/acq" is the readiness check alone. Pass -y or --yes on /acq to treat that
  flag as the yes for the whole run: send without waiting in chat, then write
  the Adopt edits into files and record the outcome, with no further ask.
  Trigger on "/acq", "ask ACQ", "ask ACQ AI", "run this past ACQ", "what would
  ACQ say", "set up ACQ AI", "sign into ACQ AI", or a pasted ACQ AI reply to
  sort through.
---

# acq

ACQ AI advises. The person decides. This skill runs the loop: the question,
the send, the follow-ups, the result. The plugin's `acqai` tools run each step
on the person's own computer, where their browser holds the ACQ AI login:
`ready`, `login`, `send`, `wait`, `answers`, `answer`, `outcome`, and `names`.
Every send goes through `send`, and every send needs the person's yes for that
run. On `/acq -y`, the flag is that yes for the send and for writing the Adopt
edits.

## The tools

- **Call the tools, not the shell.** In Cowork the shell runs in a VM with no screen for the sign-in window and no route to ACQ AI, so `acqai.py` run from there fails. When the `acqai` tools are missing, the plugin's connector did not start: say so and stop. The fix is the plugin's Connectors tab in Cowork (Customize → Plugins → acqai), or `/mcp` in Claude Code. In a Claude Code terminal session only, the same commands also run from the shell: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acqai.py" <command>`.
- **Jobs.** `ready`, `login`, and a live `send` return within 45 seconds. When the work takes longer (setup, the sign-in window, a long answer), the result ends on `still running` and names a job. Call `wait` with that job until the result ends on an exit code.
- **Exit codes.** Each result ends on one: 0 done, 1 a stop with its reason above it (a dry run with a NOT line, or a send that failed), 2 a usage error, 3 a send with no yes.
- **Command names.** A result that names `acqai <command>` names the tool of that name. `acqai setup` is the first step `ready` runs. `acqai discover` is the by-hand step in the README, for a terminal.

## Get ready: every run starts here, and a bare `/acq` is only this

**First tool call, always:** `ready`, before Interpret, Gather, Show, or Send.
A task in the arguments does not skip this. Do not shape a question until
`ready` ends on exit 0.

It checks what a send needs, in order, fixes what it can, and ends with the
state of things and `ready`:

- **Playwright** missing → it runs setup: a private venv with Playwright and its Chromium (one to three minutes). Pass `company: "Their Company"` when they belong to more than one company on ACQ AI, so the sign-in list is clicked for them. The company set at install is `${user_config.mozi_company}`: when that is a name, not blank or a placeholder, pass it on the first `ready`.
- **The login** missing, or the saved session expired (a headless check, a few seconds) → it opens the sign-in window on the person's screen at portal.acquisition.com/advisor and waits for them, up to ten minutes. Tell them what to do in the window: sign in with the email code, click your company if a list shows, then send one short message there ("hi" is enough). The script learns the chat route from that message and closes the window on its own once it has both. Call `wait` on the job while they do.
- **The route** not learned after a login → it stops (exit 1). They run `login` again and send a message, or do the by-hand step in the README (Copy as cURL, then `discover --from-curl` in a terminal).
- **The chat** the next send would continue is from the other app → it stops (exit 1) and names both ways on. That is the person's choice: `new: true` on the send starts a fresh conversation, and the login the message names goes back to the old one (`login` with `legacy: true` for the older app).

`ready` with `dry_run: true` names what it would run and runs nothing, browser
included, so it is the way to show the person what is coming before a long
step.

With no task, show the result and stop: they are signed in and can ask with
`/acq <task>`, or here is the one thing not fixed and what to do. With a task,
go on to the loop once `ready` ends on exit 0. If it ends on any other code,
stop: report the error and the fix, and do not answer alone.

## Two ways to run

- **`/acq <task>`**: step by step. After `ready`, show each question, wait for yes in chat, send with `yes: true`, then ask again before each follow-up. After the loop, show the Adopt / Later / Drop piles and wait: make file edits only when asked, and record the outcome only once they say what to keep.
- **`/acq -y <task>`** (or `--yes`): the flag is the yes for the whole run. After `ready`, show the first question and the docs that ride with it so they can see what goes out, then send that question and up to two follow-ups with `yes: true` on each `send` without waiting for another yes in chat. Do not ask "Send it?" or "Reply yes." When the loop ends, write every Adopt change into the files now (edit an existing doc, or create a new one when the task or the answer calls for a separate page or offer), record the outcome, and report what you wrote. Do not ask which pile to keep. Do not ask permission to edit. Later and Drop stay in the report only. Cap follow-ups at two.

`-y` / `--yes` may sit anywhere in the arguments. Strip them from the task text before you interpret it. With no task left after stripping, run `ready` and stop (same as a bare `/acq`).

## The loop: `/acq <task>`

0. **Ready** (already required above). Do not start step 1 until `ready` ended on exit 0.

1. **Interpret.** Turn the task into one question ACQ AI can answer with mechanics: what to change, why it works, what it displaces. A question about the person's voice, or about a relationship, is a different kind of question. Say so, and ask what they want ACQ AI's read on.

2. **Gather.** Read the docs the person points at, or the folder you are in. Build the question with the situation, the numbers, the decision, and the docs that matter, pasted in whole. Ask ACQ AI to answer in the person's own terms, to say what each recommendation rests on (their docs, its pattern library, or a guess), and to name any contradiction it sees. Ask it too, when a follow-up brings in more of the situation, to build on its last answer and say what the new detail changes.

   What stays home: client names, call transcripts, anything the person calls private, and anything you would hesitate to read aloud to a stranger. When in doubt, ask before you include it. Names the person wants kept out every time go on a list the script checks: `names` with `action: "add"` and the names. When a question includes one of them, the send stops before it goes out.

3. **Show, then send.** Pass the whole question as `send`'s `question` text. Run `send` with `dry_run: true` first. It ends on exit 1 when a line says NOT ready, such as a private name or a chat from the other app, so fix that before anything else. Show the person what is about to go out: the question and the list of docs riding with it. On a step-by-step run, wait for their yes in chat. On `/acq -y`, the flag already is that yes: do not wait, send next. Then call `send` again with the same question and `yes: true`.

   `yes: true` is their yes, carried to the script. The script paces itself and keeps the conversation, so a follow-up lands in the same thread. An answer can take two minutes, so `wait` on the job until it ends. Read the answer, then refine it with a follow-up question or two in the same conversation. Each question brings in something the person's docs hold and ACQ AI does not have yet, such as a number or a result they already got. Where the answer is general, ask how it plays out with that number. Where it names a step without the reason, ask what makes it work. Where it and a doc point different ways, quote the doc and ask how the two fit. Write each one as a question rather than a correction, so the next answer builds on the last one and starts from more of the situation.

   On a step-by-step run, wait for a yes before each follow-up send. On `/acq -y`, do not wait: after the show, send the first question with `yes: true`, then dry-run each follow-up and send it with `yes: true`. Still dry-run every send so a NOT ready line stops the loop.

   Pass `new: true` when the next question is a different topic. To pick up an earlier conversation, `answers` lists each answer with its chat. Read that answer's file with `answer`, then send with `continue` set to its name. Give each follow-up its reason in one line with `why`, and the file keeps it above the message.

4. **Report, then land.** Give the person the answer with two labels: what ACQ AI said, and what you added. Then sort it into three piles.
   - **Adopt.** A change to a doc, drafted in the person's own voice. ACQ AI's wording is raw material. The mechanics travel, and the words get rewritten. When the task asks for a new offer, page, or version, and the answer says it has to be separate, Adopt includes that new file.
   - **Later.** An idea worth keeping that changes more than today's question.
   - **Drop.** The rest, with one line on why.

   On a step-by-step run: show the edits. Make them only when asked. Once the person decides, record it at the top of the answer's file, which the send named, so the file opens as a digest: `outcome` with `answer` set to that file, `title` (the question, one line), `best_answer` (a few bullets), and one line per item in `adopt`, `suggest`, `later`, and `drop`. `adopt` is a change you made, and `suggest` a change you drafted that waits on their yes.

   On `/acq -y`: do not wait. Write every Adopt change into the files now. Create a new file when Adopt is a separate offer or page (next to the doc they pointed at, or in the folder you are in, with a clear slug). Then record the outcome with `adopt` for each change you wrote, and `later` / `drop` for the rest. Report the paths you wrote. Never leave the run on "tell me which to record" or "I can also make the edits."

## When the send fails

Stop and say why, with the fix the script named: `login` for a profile that is not signed in, `ready` for a missing Playwright, the login window (or `discover` in a terminal) for a route never learned. A private name comes out of the question, or the person says what to write in its place. A chat from the other app is the person's choice: `new: true` starts a fresh conversation, and the login the message names goes back to the old one. Then run `ready` and show it: it applies the fix it can and names the one it cannot. Never answer alone and present it as ACQ AI's. Answering alone is a different thing, and it is labeled as yours.

## Guards

- **Ready before the loop.** Do not Interpret, Gather, or Send until `ready` ends on exit 0. A question argument is not a skip.
- Nothing sends without a yes. On `/acq <task>`, that is one yes in chat per send, and file edits wait for another yes. On `/acq -y <task>`, the `-y` on the command is the yes for the run: the sends, the Adopt file edits, and the outcome record. Do not ask again in chat. `yes: true` on each `send` carries that consent to the script.
- One question at a time on the wire. The script paces itself, and one job runs at a time. A step-by-step run is a series of chat yeses; a `-y` run is the flag as yes, a short chain, and the files written before the run ends.
- The script reaches ACQ AI's chat and nothing else. It never posts to the community.
- The person's account is theirs. The tool does what they would do by hand, in their own browser, for their own use.
