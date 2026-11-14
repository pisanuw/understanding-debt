# Phase 3.3 teaching sample: digitalme, for pisan@uw.edu

Made on 2026-11-14 at digitalme HEAD `f93102b1` with four `claude` CLI calls: the level 1 questions (`udebt quiz --preview` on the five units of the Phase 3.2 sample, 29 s), the tour (31 s, in `digitalme-tour.md`), the `chat-flow` walkthrough (30 s) and one teach-back (8 s). Nothing was answered or recorded except the teach-back, whose record was removed from the testbed afterwards.

## The five level 1 questions, one fact each

The Phase 3.2 sample (`digitalme-ladder-quiz.md`) asked these units compound inventory questions ("Name the kinds of checks ... and the work it triggers ..."); the owner could answer none. The same units at level 1 now:

1. **Chat request lifecycle (SSE)** (`chat-flow`): Why does the cheap check for abusive messages run before the main persona reply is generated?
   - Reference (shown after a miss): A hostile message can be answered with a curt canned reply (or blocked) and recorded as a strike without paying for the full persona reply, so abusers cost almost nothing.
   - Variants: What does the chat flow gain by screening a message for hostility before the expensive model call starts?; Why is the hostile-message screen placed ahead of the streamed reply rather than after it?

2. **Cost controls, rate limits and usage metering** (`cost-control`): What does an anonymous visitor see when the site's daily usage limit has been reached?
   - Reference (shown after a miss): The request is rejected with a rate-limit style error saying the daily limit is reached, with a hint to sign in and add their own Anthropic API key to continue without limits.
   - Variants: When the daily token budget is exhausted, what message does a non-signed-in visitor get back?; What happens to a visitor's chat request once the daily usage cap is hit?

3. **Admin dashboard and Persona Fixes loop** (`admin-persona-fix-loop`): Why does the persona-fix feature refuse to edit a file outside its fixed set of persona files, even if the model proposes one?
   - Reference (shown after a miss): The restriction is enforced server-side, so whatever the model proposes, edits can only touch the listed persona files and never code, docs or private persona material.
   - Variants: What does the fixed set of editable persona files protect against when the model suggests changes?; Why can't the AI-proposed persona edits touch code or private files?

4. **Auth and role-based access** (`auth-rbac`): Why does a role change for a signed-in user take effect on their very next request, even though their login token was issued earlier?
   - Reference (shown after a miss): Roles are not baked into the token; they are looked up from the database on every request and turned into capabilities, so there is no stale copy.
   - Variants: Why doesn't a user need to log in again for a newly granted or revoked role to apply?; What about where roles are looked up makes role changes immediate despite an already-issued token?

5. **Persona prompt and retrieval modes** (`persona-retrieval`): Why does editing a persona markdown file change what the bot says without any code change?
   - Reference (shown after a miss): The system prompt and retrieved context are assembled at runtime from the persona markdown files, so the knowledge lives in plain repo files rather than in code or a fine-tuned model.
   - Variants: What makes persona content editable without touching application code?; Why can the persona be updated purely by changing files in the repo's persona folder?

`ladder.compound` found none of the five compound and no first phrasing needed replacing; `ladder.identifiers_in` found no identifier.

## The chat-flow walkthrough

Traced: A visitor types a question in the chat box and the browser POSTs it to /api/chat, then renders the streamed reply as it arrives and gets a final "done" event.

1. `frontend/src/App.jsx:445-468`: The browser starts the request with an AbortController and a 60-second watchdog that re-arms whenever bytes arrive. Part of Yusuf Pisan's July hardening commit [human] added it: an idle socket never errors on its own, so without it the typing dots could spin forever. The Stop button uses the same controller, and aborting is what the server later sees as a disconnect.
2. `backend/main.py:2397-2412`: Inside the generator, before any model call, a cheap Haiku hostile check runs on the message. If it flags the message, strikes are recorded and the penalty is applied. The branches that follow either send a short in-voice reply, send nothing, or ban, and each ends the stream early. This is why the user turn is already in history at this point, and why each early exit has to keep history consistent.
3. `backend/main.py:2572-2595`: After retrieval and model selection, the stream opens on the async client. Each text delta passes through the sentence streamer, then PII scrubbing and control-tag stripping, and only then becomes an SSE 'chunk' event. Because the client is async and each chunk is yielded as it arrives, the event loop stays free for other visitors. That was the point of the June async refactor [assisted].
4. `backend/sentences.py:52-68`: The streamer releases text only up to completed sentence boundaries and holds the partial tail. When the tier's max_sentences is reached it cuts at that boundary and sets a permanent capped flag. The cap is enforced on the server by counting terminators, so it holds even if the model ignores the prompt or answers in another language. The tail flush at stream end (flush_complete or flush_truncated) respects the same flag.
5. `backend/main.py:2666-2682`: Once the reply is stored, the guardrail check and the follow-up generation are two independent Haiku calls. They run through asyncio.gather so the 'done' event, which carries the follow-up chips and the message counter, waits for one round trip instead of two. This is the 'slow done event' fix. The guardrail runs after delivery, so it records violations and adjusts trust but cannot block a reply that has already been sent.
6. `backend/main.py:2726-2750`: If the client disconnects, Starlette cancels the generator at its current await or yield. The handler repairs the in-memory history synchronously. It keeps a partial reply recorded with mode 'aborted' if any text was delivered, and otherwise pops the unanswered user turn. A dangling user turn would make the Messages API reject every later send in that session with a 400. The code then re-raises so cancellation still propagates.

The trap: A reader may think the guardrail check screens the reply before the user sees it, but it runs only after the full reply has streamed, so it records violations and adjusts trust and cannot block anything. Real-time protection comes only from the persona rules, the PII scrub and the pre-send hostile check.

Check questions written with it (one per level):

- level 1: What job does the part of the system that handles each chat message do for the visitor?
- level 2: Streaming the reply over a single one-way connection was chosen over a blocking request/response, a two-way socket or a background worker queue. What constraint made that the better choice?
- level 3: If the reply were delivered as one blocking response instead of a stream, what is the first other part of the system that would have to change?
- level 4: What happens to the conversation record if the visitor closes the tab or presses Stop halfway through a reply?
- level 5: If the chat endpoint ran the guardrail check and the follow-up generation one after the other instead of through asyncio.gather, what would change for the visitor?

`udebt dig chat-flow` prints each step's narration beside its actual lines from the working tree, numbered (not reproduced here); a second `dig` reuses the cached walkthrough with no call.

## A half-right teach-back on auth-rbac

Explanation given (deliberately half right: the purpose is right, the claim about the token is wrong): "It handles sign-in with Google and decides who may open the admin pages. I think the role is stored inside the login token, so a role change only applies the next time the user signs in."

```
[WRONG] level 2 (decision and alternatives)
What was right: It correctly says the unit handles Google sign-in and decides who may open the admin pages.
What was missing: It leaves out the decision itself: a Google ID token is exchanged for a JWT sent in the Authorization header, with no cookies, because Netlify and Render are on different origins. It also doesn't mention the alternatives that were rejected (cookie sessions, an external auth provider, a fixed admin list).
Misconception: The JWT doesn't carry the role. `create_jwt` stores only email, name and picture. Roles are looked up in SQLite on every request (`user_roles_for` and `_require_cap` in backend/main.py), so role changes apply immediately without a new sign-in.
  The answer: Optional Google sign-in exchanges the Google ID token for a JWT that the
  browser keeps in localStorage and sends in the Authorization header, with no cookies,
  because the frontend and backend are on different origins. The JWT carries only identity.
  Roles are stored in SQLite and mapped to capabilities on every request, so role changes
  take effect immediately, while cookie sessions, an external auth provider and a fixed
  admin list were rejected.
```

