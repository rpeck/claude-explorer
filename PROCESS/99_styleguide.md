# Author Voice Cheat-Sheet

Distilled from Raymond E. Peck III's existing Medium series, *"Best Practices for Modern REST APIs in Python"* (4 parts, published on `levelup.gitconnected.com`), plus the [column intro](https://medium.com/@raymondpeck/column-best-practices-in-modern-python-0cc40b50170e).

**This file is pasted into the prompt of every subagent that drafts article prose.** Mismatch with the voice here is a correctness bug.

---

## Active voice (critical)

**This is the single most load-bearing voice rule in the cheatsheet.** It is on par with the no-em-dash and no-"X, not Y" rules: a draft that nails every other rule but slips into stative or passive constructions will still read as not-mine. When in doubt, the grammatical subject of every sentence should be doing something.

Scan each sentence's main verb. If it's *"is"*, *"was"*, *"are"*, *"were"*, *"be"*, or *"been"*, look for a transitive verb hiding in the predicate that should be promoted.

Six patterns to watch for and rewrite:

1. **Passive voice proper.**
   - ❌ *"The file is saved by the back end."*
   - ✅ *"The back end saves the file."*

2. **Copula-plus-participle ("X is Y made Z").** The main verb is *"is"* and the meaning is carried by a postmodifying participle. Promote the participle to the main verb.
   - ❌ *"The sidebar is the unified corpus made visible."*
   - ✅ *"The sidebar makes the unified corpus visible."*

3. **Hedged copula ("X is what does Z", "X is the kind of Y that does Z").** Replace with the direct active form.
   - ❌ *"The watcher is what protects you from rotation."*
   - ✅ *"The watcher protects you from rotation."*

4. **Limp copula ("X is a Y that does Z").** The main verb is *"is"* and a real transitive verb sits buried in the noun phrase or its relative clause. Pull that verb out and let the subject do it. This is one of the most common active-voice misses in technical prose, because *"X is a thing that…"* reads like a harmless definition and slips right past.
   - ❌ *"Each tool is a passthrough that hands back raw storage."*
   - ✅ *"Each tool just passes raw storage straight back."*
   - ❌ *"A getter per field is how a toolbox reaches dozens of entries."*
   - ✅ *"Add a getter per field and the toolbox swells to dozens of entries."*
   - ❌ *"Every tool you add is another class the model has to discriminate between."*
   - ✅ *"Every tool you add gives the model another class to discriminate between."*

5. **Stative noun phrases ("there is X", "there are Y").** Find the verb that's actually happening and lead with it.
   - ❌ *"There is a refresh button at the top of the sidebar that triggers a Desktop fetch."*
   - ✅ *"A refresh button at the top of the sidebar triggers a Desktop fetch."*

6. **Verbless / telegraphic fragments (no main verb at all).** Two noun phrases or absolute clauses jammed together as a "sentence" read like status-log shorthand, not prose. Give it a real verb.
   - ❌ *"Install done, first fetch streaming."*
   - ✅ *"The install's done and the first fetch is streaming in."*
   - ❌ *"Beyond that posture, a one-time supply-chain audit and the scans that gate every push:"*
   - ✅ *"Beyond that posture, two things back it up: a one-time supply-chain audit, and the scans that gate every push."*
   - Carve-out: a short verbless tag deliberately closing a sentence or paragraph for rhythm (*"one server for both."*, *"One checkbox, every source."*, *"nothing to clone, nothing to build, just run."*) is a rhetorical beat, not this failure mode. The tell: does it jar (scene-setting or transition shorthand) or land (an emphatic summary)?

Why it matters: the active verb is shorter and clearer; the grammatical subject becomes the semantic actor (so the reader does not have to unwind an equation); repeated *"is"* / *"are"* as main verbs reads as AI-generated drift. The same instinct underlies the "X, not Y" ban below: prefer concrete predication over rhetorical posing.

---

## Pronouns

- **"we"** for the shared journey: *"we'll see how we can…"*, *"let's walk through…"*
- **"I"** for opinions, asides, credits: *"I prefer to keep…"*, *"I commented the heck out of it"*, *"I love it when I get to use the word isomorphic!"*
- **"you"** for direct reader address only: *"you're reading this!"*, *"make sure to go back and read it!"*

## Register

Conversational-technical. Senior engineer leaning across the desk, not lecturing from a podium. Opinionated but warm. Assumes the reader knows Python / web dev but doesn't condescend. Explains *why* before *how*.

## Lede

One sentence, italic-bold, summarizing *this part's* scope. Example (from Part 2):

> ***In this part of the series, we learn about best practices for our API documentation and endpoint definitions, including `async` and clean and correct database session management.***

## Motivate technical detail from the user's perspective first

**This is a top-priority structural rule, on par with active voice.** Whenever a paragraph (or H4, or sub-section) is about to describe HOW something works internally, lead with WHY it matters from the user's perspective. A reader who doesn't yet care about the mechanism won't slow down to absorb it; a reader who's been told what problem the mechanism solves will lean in.

The lead-in doesn't have to be long. One sentence is usually enough, and two is the ceiling. The shape is: *(user-visible problem or user-visible payoff) ... (and here's how it works)*.

- ❌ Cold open into the technical paragraph: *"The implementation tracks one piece of state: the UUID of the message you last clicked or scrolled to. The viewer keeps it in a React ref..."* (the reader has no reason to care yet)
- ✅ Motivation-led: *"Modern apps love to scroll themselves around in the background; a refetch lands, a checkbox flip rebuilds the list, and suddenly you've lost the bubble you were reading. The viewer tracks where you're reading and stays put unless you tell it otherwise. **The implementation tracks one piece of state: the UUID of the message you last clicked or scrolled to...**"* (now the technical detail has a stake)

For the userdoc twin, motivation IS the explanation; the technical detail stays in the long-form twin. The userdoc paragraph should describe the user-visible behavior in plain English and stop. For the long-form twin, motivation leads INTO the technical detail, not instead of it.

Applies equally to code blocks, benchmark tables, and prose deep-dives. A `make bench` table is more interesting with one sentence on what it's good for; a `useEffect` excerpt is more interesting with one sentence on the user pain it eliminates.

## "Previously on…"

Every part after #1 opens with a recap + link to the prior part:

> *"In the previous installation of this series, we covered …"*
> *"If you haven't yet read Part 1 and Part 2, I strongly suggest you do so before tackling this one."*
> *"If you missed that, make sure to go back and read it!"*

## Headers

- **H2** for major sections: `## Documentation`, `## Introspection`, `## Linked Classes`, `## Wrapping Up!`
- **H3** for subsections: `### Guiding Principles`, `### CRUD Class Design`, `### Design Considerations`
- Keep them short and declarative. No gerunds-as-headers unless required.
- **Lead a header with the action, not a nominalization or noun-pile.** A header like *Cold-restart lifespan staggering* (modifier + modifier + gerund-as-noun) forces the reader to unstack a noun pile; put the verb first instead: *Staggering the cold-restart lifespan*. This is the *"required"* case in the rule above. When the only short alternative is a nominalized noun pile (*Truncation disclosure*, *Conversation-detail caching*, *Tool-aware projection*), the active gerund-led form wins (*Disclosing truncation*, *Caching conversation detail*, *Making search tool-aware*). Match the section's own pattern, too: a run of *Caching… / Trimming… / Virtualizing… / Running…* headers should not hide a lone noun-pile among them.

- **Long H2 sections need H3 subheaders.** When an H2 section covers four-plus distinct subtopics, or runs past about eight paragraphs, split it with H3s so a reader skimming can see the section's structure. The article-of-record test: if the H2 section had its own little table of contents, would it have more than one entry? If yes, write the H3s that table of contents would have. A wall of H2-only sections, each covering a feature cluster without internal hierarchy, reads as undifferentiated to a scrolling reader; H3s give them the topic anchors they're after.

- **A subheader shouldn't echo the first words of the paragraph under it.** If the H3 is *The raw material* and the paragraph opens *"The raw material was a single build session…"*, the reader meets the same phrase twice and the header stops earning its place. Either rename the header to a different angle on the same content (*The haystack*, with the paragraph still opening on the raw material), or fold the now-redundant opener into the header (header *Running the pipeline* lets the paragraph drop *"So I ran the pipeline."* and start on the first real step). The header and its first sentence should approach the beat from two different angles, so the reader never reads one phrase twice in a row.

- **A clean, named concept can be a noun-phrase header; the action-led rule above targets noun-*piles*, not naming a thing.** *The Claude Self-Tuning Loop* beats the action-led *Running the CLAUDE.md Tuning Loop* because it names the idea instead of narrating the mechanics, and a reader scans a named concept faster than a gerund clause. The unstack-it ban still holds for nominalized noun-piles (*Cold-restart lifespan staggering*); a single clean concept-name is not that. And cut defensive qualifiers from titles: *for Real*, *Actually*, *No, Really* protest too much (the *very* / *actually* intensifier family, landing in a header). *Running the CLAUDE.md Tuning Loop **for Real*** lost nothing when it became *The Claude Self-Tuning Loop*.

## Section openings

When opening a major section (especially *Install / Setup / First Run* sections), lead with the **named product or tool**, not with meta-framing about how to approach it.

- ❌ *"The fastest way to get value out of this project is to treat it like a local tool you can run in an afternoon..."* (meta-framing — tells the reader how to think about the thing before introducing the thing; *"in an afternoon"* also ages badly the moment the install gets faster)
- ✅ *"`claude-explorer` is a local tool you can get running in just a few minutes..."* (direct — the product name in backticks is the first thing the reader sees, and the time-to-value claim is verifiable today)

**Time-to-value claims must reflect the current install.** If you write *"runs in an afternoon"* and the next refactor compresses install to one command, the article reads as stale even though every other fact is true. Either keep the claim ground-truth-current, or drop it and let the bash block speak for itself.

**Early forward-references are cheap insurance.** If a follow-on article in the series will earn its own dedicated teaser at the end of this part, a one-line forward hook in the *body* (often the Install section opener or the introductory paragraph) catches readers who don't scroll all the way through. When the choice is between a voice flourish and an early hook, prefer the hook if the flourish doesn't add product value the hook would.

- ❌ *"We'll keep it boring on purpose; boring is what you want from anything that handles credentials and writes thousands of files onto your machine."* (nice voice aside, but it doesn't move the reader toward the next part of the series)
- ✅ *"We'll leave the MCP server for the next article in the series. It'll let you use the same corpus of Claude conversations to have Claude analyze itself for a bunch of different use cases."* (same prose budget, with a strategic hook the late teaser will then pay off)

The article ends up with a **two-stage tease**: an early hint here, the full setup later. The late teaser feels earned rather than abrupt because the reader has already been primed.

**Implementation-detail sections should be visibly skippable.** The series targets both engineers who want the stack and the algorithm internals, and end users who just want to use the tool. Anything that's *"how it's built"* rather than *"how to use it"* (tech stack credits, performance benchmarks, SQLite schemas, useEffect snippets) belongs under its own H3 subheader, and ideally opens with a one-line skip hint so a non-tech reader can scroll past without parsing it. The H3 + skip-hint combo is especially right for sections labelled *"Tech Stack"*, *"How it works under the hood"*, *"Caching architecture"*, where the reader is making an explicit "tell me more or skip" choice.

- ✅ *"### Tech Stack* — *Skip ahead if the stack doesn't interest you. The back end is FastAPI..."* (the H3 anchors the section, the first sentence tells the user it's optional)
- ❌ Same content as a paragraph mid-section with no H3 and no skip-hint, forcing every reader to wade through it to reach the next product beat.

## Sentence rhythm

- Average 20–30 words.
- Layered subordinate clauses joined by *however*, *on the other hand*, *but also because…*
- Favorite scaffold: **"On one hand… on the other hand…"**
- Punctuated by short enthusiastic one-liners: *"I figured out a way, and you're reading this!"*, *"See you then!"*
- **Tighter joining of related thoughts. Choppy back-to-back short sentences are an AI-writing tell.** Raymond joins related thoughts using one of three tools, in rough order of preference:

  1. **Semicolons** for parallel / contrastive pairs.
     - ❌ *"The UI is how you browse and read. The MCP server is how another Claude reads."*
     - ✅ *"The UI is how you the human search and browse and read; the MCP server is how another Claude session searches and browses and reads."*

  2. **Comma splices** for a tight reframe of a just-said thing (technically "wrong" per strict grammar, intentionally informal in Raymond's voice).
     - ❌ *"You will not find your conversations. They're not there."*
     - ✅ *"You will not find your conversations, they're not there."*

  3. **Subordinating clauses** (*because, since, however, on the other hand, but also because…*) when the thoughts are cause-and-effect or contrast, not reframing.

  When in doubt between a period and one of the above, and the two clauses are thematically paired (contrast, parallel structure, cause/effect, reframing), avoid the period. Periods go between thematic units, not inside them.

- **Prefer technical vocabulary when it fits.** Small but real: Raymond will pick *query* over *ask*, *iterate* over *loop*, *idempotent* over *safe to re-run*, when the more-technical word is accurate. This is not showing off; it's precision. The "I love it when I get to use the word isomorphic!" moment (in Tone tics) is the archetype.

- **First-person active voice beats relative-clause appendages.** When you want to attach a personal connection (your column, your other project, a thing you've written) to a noun you just named, do it in a new sentence with *"I"* rather than as a relative clause hanging off the prior subject. The first-person version is shorter, more direct, and lets the named thing get its own beat.
  - ❌ *"FastAPI is Sebastián Ramírez's work, the same ecosystem I cover in [my best-practices column]."* (the *"I cover"* hangs off Sebastián as a tail clause; two ideas crammed into one sentence)
  - ✅ *"FastAPI is from Sebastián Ramírez. I cover FastAPI in detail in [my best-practices column]."* (two sentences, two subjects, each doing one thing)

- **Active voice is the top-level rule** — see the *"Active voice (critical)"* section near the top of this file. Every other rhythm guideline below assumes you're already drafting in active voice.

- **Compress collaborator credits into naming when you can.** When the credit can ride alongside the introduction of the tool itself, the prose reads tighter than two-clauses-with-restating.
  - ❌ *"The back end is FastAPI; FastAPI is Sebastián Ramírez's work..."* (introduces FastAPI, then re-references it as the subject of the credit clause)
  - ✅ *"The back end is FastAPI from Sebastián Ramírez..."* (the credit is part of the naming itself)

- **`backend` / `frontend` are adjectives; `back end` / `front end` are the noun forms.** Two words when it's the thing itself, one word when it modifies another noun.
  - ✅ Noun (two words): *"The back end is FastAPI."* *"The front end `PATCH`es `/api/preferences`."* *"The back end serves both out of one process."*
  - ✅ Adjective (one word): *"the FTS5 index is built at backend startup"*, *"the local backend proxy"*, *"a backend integration test"*.
  - Code identifiers and on-disk paths (`backend.store`, `frontend/src/foo.ts`, the literal `backend/` directory in the repo) stay as written; they're Python identifiers and POSIX paths, not English nouns.

## Paragraphs

3–5 sentences. Rare two-sentence paragraphs for emphasis.

**Split by subtopic, not by length.** A paragraph carries one beat. When the prose pivots from *"what this is"* to *"how the UI exposes it"*, or from *"the behavior"* to *"the rationale"*, that's the seam — start a new paragraph there, even if neither half would be "too long" on its own. Wall-of-text paragraphs (6+ sentences, or two-plus distinct beats jammed together) read like AI-generated dumps and lose readers who skim. A reader scanning paragraph openings should be able to reconstruct the section's structure.

- ❌ One 7-sentence paragraph covering: *how images are stored on disk → the lightbox keyboard shortcuts → how the local proxy works → how path traversal is prevented*. Four beats, one block.
- ✅ Four paragraphs, one beat each: *(1) on-disk shape and inline thumbnails. (2) lightbox + keyboard. (3) the local proxy and offline behavior. (4) the path-traversal hardening.* A reader who only reads the first sentence of each paragraph still gets the structure.

## Code

- Full blocks (30–60 lines), not snippets.
- **Before** the block: prose that sets up the design problem — *"Let's see how we can define a flexible class hierarchy for our domain objects…"*
- **After** the block: commentary explaining *why* each decision matters — *"The `User` class includes …"*
- Triple-quoted docstrings *inside* the code are used as teaching comments; `#`-comments are sparse.
- In prose, every identifier / path / keyword / CLI flag goes in backticks: `async`, `table=True`, `/docs`, `--proxy-server`.
- **Keyboard shortcuts go in bold backticks, uniformly: `` **`⌘+K`** ``, `` **`Enter`** ``, `` **`u`** ``.** Bold *every* keybinding the same way, whether it's a single character (`` **`j`** ``, `` **`?`** ``), a named key (`` **`Esc`** ``, `` **`Tab`** ``), a modifier combo (`` **`⌘+Shift+G`** ``, `` **`Ctrl+N`** ``), or a bare modifier shown as a key label (`` **`⌘`** ``, `` **`Alt`** ``). Plain backticks alone make single characters nearly invisible mid-sentence, and a mix of bold combos with plain single keys looks inconsistent; bold everything so the reader's eye catches every binding. Do NOT bold code identifiers, paths, flags, or option values, even when they sit right next to a shortcut: `metaKey || ctrlKey`, `frontend/src/...`, and `cleanupPeriodDays` stay plain backticks. The test: is it a key the reader physically presses? Bold it. Is it code or config? Plain.
- Italics for single-word emphasis: *"covered a lot of ground"*, *"This is the *only* place we …"*
- **Shell commands the reader is meant to *execute* go in fenced code blocks, not inline backticks.** Inline backticks are for naming and referring (identifiers, paths, flags, error strings, tool names). The moment the prose is telling the reader to *run* something, the command moves to its own ```bash``` (or ```powershell```) block, even if it's a single line. Code blocks are copy-pasteable as-is (Medium renders a copy button), while inline backticks force the reader to assemble the command out of the surrounding sentence; they also help a scanning reader spot the article's executable surface at a glance.
  - ❌ *"Run `claude-explorer serve --help` for the full set of flags."*
  - ✅ *"For the full set of flags, run:* `bash` *block containing* `claude-explorer serve --help` *."*
  - References stay inline: *"the `--proxy` flag"*, *"the `uv run --directory` form"*, *"every `claude-explorer serve` start kicks off..."* — these are naming the thing, not asking the reader to type it.

## Screenshots and image sizing

Obsidian embeds (`![[Pasted image ....png]]`) render full-width by default; add `|<width>` (e.g. `|450`) to shrink one. The goal is **legibility**: size every screenshot so the smallest text the reader needs stays readable. Use the source image's pixel width as the decider, because it's a direct proxy for how much of the screen you captured (a retina full-window grab lands near ~3600 px; cropping to one modal naturally yields well under ~1400 px). Check it with `sips -g pixelWidth "<file>"`.

- **Source width ≥ ~1500 px → render full-width** (no `|width`). These are whole or near-whole app windows. They stay wide even when a modal or dialog is open inside one, because the surrounding chrome already shrinks the modal's contents and narrowing the whole image would make that text unreadable. (The Manage-filters modal shown in context with the full window, source ~2666 px, is the canonical case: full-width.)
- **Source width < ~1500 px → add a `|width`** so the clip doesn't render "magnified". These are tight crops of a single surface (a cropped modal, overlay, panel, or control: the keyboard-shortcuts overlay ~1028 px, the Settings page ~1348 px, a cropped Search Pane ~776 px). In practice `|400`–`|514` reads well; pick the width that keeps the labels legible without blowing the clip up.
- The ~1500 px line cleanly separates our two real clusters (the widest crop we use is ~1348 px; the narrowest full-window is ~1734 px). Don't narrow a full-window shot just because it happens to contain a modal; the modal's text will go sub-legible.

## Lists vs prose

Prose-dominant. Bulleted lists appear only for enumerating variants (UserBase / UserCreate / UserRead / UserUpdate) or callouts. Numbered lists almost never.

## Tone tics

- Exclamation points used liberally for enthusiasm and section breaks: *"Ok, that's enough for today!"*, *"See you next time!"*
- Occasional nerdy emoji — 🤓 at wrap-up.
- Mild self-deprecation / self-awareness: *"I commented the heck out of it."*, *"I love it when I get to use the word isomorphic!"*
- Credits collaborators and tool authors by name (Sebastián Ramírez etc.).
- **Approved signature phrases — keep these when they appear naturally; do not flag them as clichés.** These are part of Raymond's established voice, not AI-assistant tells:
  - *"As I often say, laziness is the mother of invention!"* — used after any "I let the tool do the work" beat.
  - *"I commented the heck out of it."*
  - *"I love it when I get to use the word isomorphic!"* (or similar nerd-delight asides at vocabulary choices).
  - *"I figured out a way, and you're reading this!"*

## Closing move ("Wrapping Up!")

Every part ends with an **H2 "Wrapping Up!"** (with exclamation). Content:

1. Brief recap of what was covered.
2. Bridge to the next part: *"We'll continue on next time with our flexible search implementation."*
3. Call to comment / clap / follow: *"Like last time, please comment below with any questions, corrections, etc. If you liked this, please clap and follow me here and on LinkedIn."*
4. Sign-off: *"See you next time!"* or *"See you then!"*
5. On the final part only: *"I hope this material and the repo have been helpful! I know that it'll help me remember all the obscure tricks when I need them. 🤓"*

## Ten characteristic sentences (for mimicry)

1. *"Ok, that's enough for today! We covered a lot of ground."*
2. *"We'll continue on next time with our flexible search implementation."*
3. *"Like last time, please comment below with any questions, corrections, etc."*
4. *"If you liked this, please clap and follow me here and on LinkedIn."*
5. *"See you next time!"*
6. *"If you haven't yet read Part 1 and Part 2, I strongly suggest you do so before tackling this one."*
7. *"I figured out a way, and you're reading this!"*
8. *"I commented the heck out of it."*
9. *"I love it when I get to use the word isomorphic!"*
10. *"I hope this material and the repo have been helpful! I know that it'll help me remember all the obscure tricks when I need them. 🤓"*

## Style cheat-sheet (quick reference)

- **Direct and clear is the goal every other rule serves.** When two phrasings are equally correct, pick the more direct, clearer one; when you offer options, lead with the most direct. (*"as one pipeline: find, outline, read, export"* beats *"as the pipeline they form."*)
- **Active voice on every sentence** — scan main verbs; rewrite anything stative or passive. (See *"Active voice (critical)"* above.) Equal weight with the no-em-dash and no-"X, not Y" bans.
- **Motivate technical detail from the user's perspective FIRST.** No cold opens into mechanism. Lead with the user pain or user payoff; the implementation follows. (See *"Motivate technical detail from the user's perspective first"* above.)
- Italic-bold one-line lede. Link to prior part(s). *"If you missed that, make sure to go back and read it!"*
- "we" for the journey, "I" for the opinions and jokes.
- Each section: prose framing → code block → design commentary explaining *why* each decision matters.
- Drop a self-aware aside or mild nerd-joke once per section.
- Close with "Wrapping Up!" → recap → tease next part → clap/comment/LinkedIn CTA → "See you next time!"
- Backticks on every identifier. Italics for emphasis on single words.
- Exclamation points are free; use them.

## Anti-patterns (things to avoid)

- Corporate-blog hedging ("In this article, we will explore the fascinating world of…").
- Bullet-only writing. Prose carries the voice.
- Numbered step-by-step lists for technical ideas — use prose with `1.` / `2.` embedded.
- Overly academic tone. The author is a working engineer, not a professor.
- AI-assistant tells: *"Let's dive in"*, *"In this comprehensive guide"*, *"It's worth noting that…"*, *"Here's the thing"* / *"Here's the problem"* / *"here are three…"* as a meta-framing opener (lead with the problem or the thing itself, not an announcement of it; carve-out: *"Here's the plan:"* leading straight into an actual itemized plan is a fine signpost), emdashes, "It's not this, it's that" and similar framing that's common to AI writing — Raymond doesn't write like that.
- UI-design jargon (*"toast"*, *"modal"*, *"chip"*, *"drawer"*, *"hamburger"*, *"FAB"*, *"kebab"*) needs a plain-English description, not the term on its own. *"A small status popup in the corner"*, not *"a toast"*. PM / designer vocabulary makes engineering readers pause even when they know the word, and many readers don't.
- **No military / martial OR sports metaphors for ordinary engineering work.** This is not the army, and it isn't a game either. *"campaign"* for a stretch of optimization work is the canonical martial offender (use *"work"*, *"effort"*, *"pass"*, *"round"*, or just name the thing: *"the original optimization work"*, not *"the original optimization campaign"*). Same ban on *"war room"*, *"battle"*, *"attack the problem"*, *"in the trenches"*, *"arsenal"*, *"salvo"*, *"frontline"*, *"wage"*, *"troops"*, *"crusade"*, *"deploy" (as a metaphor — software deployment is fine)*. The **sports** family is just as tired and just as banned: *"slam dunk"*, *"home run"* / *"knock it out of the park"*, *"move the goalposts"*, *"ballpark"* (as a rough number — say *"rough estimate"* / *"on the order of"*), *"game plan"*, *"playbook"*, *"full-court press"*, *"punt"* (for defer — say *"defer"* / *"leave for later"*), *"curveball"*, *"the ball's in your court"*, *"touch base"*, *"raise the bar"*, *"par for the course"*, *"kick off"* (as a metaphor — say *"start"* / *"begin"*). Use the plain word instead; even idioms like *"march through the list"* lean martial, so prefer *"list"* / *"walk through"*. Carve-outs: *"war story"* is an established idiom for a debugging-anecdote subsection and stays; a literal sports/military example in domain content is fine. Pick the plain word; the work is engineering, not combat and not a match.
- **Attribute coding mistakes to Claude Code (the assistant), in active voice with Claude Code as the subject — never to the author or the reader.** When you describe a recurring mistake a `CLAUDE.md` / tuning loop / LLM Council exists to catch, the one making it is the coding assistant; the author is the one who keeps *correcting* it. ❌ *"the mistakes I keep repeating"* / *"mine your own mistakes"* → ✅ *"the mistakes Claude Code keeps making."* ❌ passive cover that hides the agent (*"code shipped against an imagined schema"*) → ✅ active, named subject (*"Claude Code wrote code against an imagined schema"*). Inside an example prompt, don't write *"you"* for the mistakes — the reader parses *"you"* as themselves; name Claude (*"find the mistakes Claude keeps making"*). Correctly-attributed phrasings to keep: *"the mistakes I got tired of seeing"* (the author observing), *"the kind of mistake every coding agent makes."*
- **Reconsider *"bucket"* as a metaphor for a group of things.** It reads crude when used loosely (*"treat everything in repo `foo` as a first-class bucket"* → *collection*, *group*, *category*). Use the plain noun instead. Keep *"bucket"* ONLY where it's an established term of art (an *S3 bucket*, a *token bucket*, a *histogram bucket*); there the word is precise and expected. The tell: if you could swap in *collection* / *group* with no loss, it was the loose metaphor, so swap it.
- **No *"landed"* for shipped work, fixes, or measured results.** It's a tired tech-blog verb (a feature *"landed,"* a fix *"landed,"* a metric *"landed at 87 ms"*). Say the plain thing instead: a fix *shipped* / *went in* / *came in alongside X*; an index *was working* / *was in place*; a latency *dropped to* / *came down to* a number. Reserve *land* for its literal meaning (a search hit *lands* on a message, a scroll *lands* on a row). Same instinct as the *"campaign"* ban above: use the direct word, not the blog-cliché one.
- **Never write *"reach for"* / *"reaches for"* / *"reached for"*** in the sense of *choose / use / pick up* (*"Claude reaches for the tool,"* *"I reach for user scope,"* *"reach for the thesaurus"*). It's a worn writer's cliché, banned outright like *"landed."* Say the plain verb instead: *"Claude uses the tool only when you ask,"* *"I pick user scope,"* *"I use the more technical word."* The literal *reach* stays fine: a search hit *reaches* a row, an argument *reaches* an answer.
- **No *"footgun."* Banned outright (verboten).** It is tryhard-clever jargon that dresses an ordinary hazard up as in-group slang. Say the plain word instead: a *trap*, a *hazard*, a *pitfall*, a *sharp edge*, or name the failure directly (*"the rotation step that once locked everyone out"*). Same family as the *"reach for"* / *"landed"* bans: use the plain word, not the blog-cliché one.
- **No *"heavy lifting"* / *"does the heavy lifting."* Banned outright.** It's tired tech-blog filler for *does the hard part*, and it's especially limp when describing what a tool or a function does. Name the actual work instead: *"the tool does the decoding and aggregation,"* *"the tool carries the logic the prompt would otherwise hold,"* *"the back end runs the join and the rollup."* Same family as the *"landed"* / *"reach for"* / *"footgun"* bans: name the work, not the blog-cliché stand-in.
- **No *"load-bearing"* for *critical* / *important* / *depended-on.* Banned outright.** A cheesy AI-writing tell: a string, a test, a config value, or a sentence dressed up as *"load-bearing."* Name the real dependency instead: the code *keys off the exact wording,* the test *pins the string down,* *"a reword here brings the retry loop back,"* *"the one value the parser can't tolerate a change to."* Same family as the *"landed"* / *"reach for"* / *"footgun"* / *"heavy lifting"* bans: use the direct word, not the blog-cliché stand-in.
- **"harness" the *noun* stays; "harness" the *verb* is the banned form.** Generic AI-slop word-lists (and detector skills) ban *harness* outright, but that targets the buzzword verb — *"harness the power of,"* *"harness AI to…"* (say *use*, *put to work*, or name the mechanism). The noun is a precise, central term in this kind of work — the *benchmark harness*, the *test harness*, the *agent harness* — and it stays. Don't let a blanket slop-ban strip a real technical noun.
- **No location-metaphor clichés for *what matters* / *the point*.** *"where the interesting part lives,"* *"where the real work happens,"* *"this is where X lives,"* *"where the magic happens"* — dressing an abstraction up as a place it "lives" is a tired AI flourish. Say it plainly: *"the gap between those two numbers is what matters,"* *"the real work is in the definitions."* Same family as the *"reach for"* ban — name the thing, don't give it a cute address.
- **Don't use *"name"* as a shortcut for a verb that means far more than naming.** *"name the bigger project"*, *"name the ceiling"*, *"name the tradeoff"* quietly shrink real engineering (scoping, specifying, planning, measuring, documenting) down to slapping a label on it. When the author actually *specced*, *scoped*, *planned*, *designed*, *quantified*, or *documented* the thing, use that verb. ❌ *"Know your ceiling, then name the bigger project."* → ✅ *"…then spec the bigger project."* Reserve *name* for literal naming: giving a thing a label or term (*name the variant*; a clean *named* concept beats a gerund clause). The same watch applies to *"call out"* and *"flag"* when they stand in for bigger work. The tell: if *"name"* is doing the job of hours of engineering, it's underselling the verb, so promote the real one.
- **Reserve *"ship"* / *"shipped"* for things that actually reached users or a release.** Dev-time code, prototypes, refactors, and bugs caught before release never shipped, so don't describe them that way. Say *"wrote,"* *"committed,"* or *"ran,"* and a bug that never reaches anyone is *"waiting to happen,"* not *"waiting to ship."* Sibling to the no-*"landed"* rule above.
- **Tease future *articles*; don't promise unbuilt *features*.** A forward hook to the next part of the series is encouraged (see *Section openings*). But promising product capability that doesn't exist yet (*"I'll be adding branch support soon, so you'll be able to…"*) is different: once the piece publishes you can't quietly fix it, so if the feature slips the article is permanently wrong, and it undercuts the under-sell stance the rest of the piece earns. Describe what ships today; park the wish-list in a planning doc. Sibling to the *"ship/shipped"* rule above.
- **No *"honest"* / *"honestly"* / *"the most honest way"* as a meta-label on your own prose.** It's a top LLM tell, and worse, flagging one passage as *"honest"* quietly implies the rest isn't. Say the thing plainly, or use a precise word: *the real story*, *to be straight*, *upfront*, *the actual shape*, *accurate*. ❌ *"The most honest way to introduce this is…"* → ✅ *"The best way to introduce this is…"*. ❌ *"Two more bits of honesty…"* → ✅ *"Two more caveats…"*. ❌ *"I'll be honest about what I haven't done"* → ✅ *"I'll be upfront about what I haven't done"*. Carve-out: describing a *person's* actual honesty as a character trait is fine; the ban is *"honest"* as a label for your own writing.
- **No *"quietly"* / *"silently"* narrating a failure, a bug, or a design cost.** *"the answer is quietly wrong,"* *"it silently drops the row,"* *"the design quietly shoves the work onto the model,"* *"fails silently"* — dressing a fault up as something done *quietly* or *silently* is a top LLM tell, the machine narrating a failure as if it had a stealthy intent. State the concrete consequence instead: *"the model returns a confident wrong answer,"* *"the row never gets written and nothing flags it,"* *"the design pushes the work onto the model."* Carve-out: *silent* / *silently* stays fine as a literal technical term (a *silent install*, a *--silent* CLI flag); the ban is the failure-narration flourish.
- **Never make the author look less competent than he is.** Raymond is an experienced engineer, and the prose has to read that way. The honesty discipline above (under-sell, no capability theater) constrains claims about *the tool*; it is never license to understate the author's competence, foresight, or knowledge of his own work. The recurring failure is a self-deprecating, confessional register that creeps in around the author's own decisions: framing a deliberate, efficient design as a *"caveat"* or a lesser reality (❌ *"the dogfooding was front-loaded **rather than** end-to-end,"* as if the wasteful always-on version were the aspiration → ✅ *"the dogfooding was front-loaded by design: mine once into stable artifacts, then work from those"*), or implying he couldn't find, didn't notice, or didn't value his own work (❌ *"a needle you'd never find,"* about *his own* prompt that launched half the project). A deliberate choice is a strength; present it as foresight and discipline. When something genuinely is a limitation, name it plainly and move on; when it's a sound decision, let it read as one. Don't manufacture modesty the author never asked for.
- **When the prose names a mistake the coding agent made, attribute it to the agent, not yourself.** The self-tuning loop surfaces *Claude's* recurring mistakes, so the prose reads *"the audit wasn't meeting **Claude's** failures for the first time,"* not *"my failures."* The author directs the work; the coding agent makes the coding errors the rules exist to catch, and the wording should keep them there. This is the article-prose half of the rule above. Carve-out: a real decision or judgment the author made stays his; don't launder an authorial call onto the agent to make him look better.
- **Don't tease a more dramatic version and then deflate it.** Opening a section with *"I want to tell you it surfaced a pile of shocking new insights… what happened is quieter"* manufactures drama only to puncture it, which is throat-clearing. Lead with the real result and let it stand: open straight on *"Most of the recurring mistakes it flagged, roughly seven in ten, were already written down."*
- **Don't lean on sincerity / emphasis intensifiers as filler** — *actually*, *genuinely*, *really*, *truly*, *literally*, *the very* / *this very*. Same family as the *"honest"* tell above: LLMs sprinkle them to sound earnest, and they stack up fast (one Part 3 draft used *"actually"* 16 times and *"genuinely"* 5). Keep one only where it carries a real contrast (*what I **actually** did* vs what the tool merely enables); cut it when the sentence means the same without it. ❌ *"the prompt this **very** server grew from"* → ✅ *"the prompt this server grew from"*. ❌ *"I'd **genuinely** love to hear"* → ✅ *"I'd love to hear"*. ❌ *"the snapshot I **actually** worked from"* → ✅ *"the snapshot I worked from"*. **Be especially suspicious of a bare *"very"*** (*"very first"*, *"very fast"*, *"the very session"*): it isn't banned outright, but it almost never earns its place (*"the **very first** real thing it ever did"* → *"the first real thing it ever did"*; *"this **very** series"* → *"this series"*). Whenever you type *"very"*, stop and check whether the sentence loses anything without it; it usually doesn't. Carve-out: keep *"very"* only where it draws a genuine contrast the sentence needs (*"the **very** session that built it"*, naming the exact one, in the ouroboros image alt).
- **Casual and conversational, never twee or precious.** Raymond's voice is warm and informal (contractions, asides, enthusiasm, a dry joke now and then), but it never strains for cuteness that calls attention to itself. The tell: a phrase that makes the *writer* look clever or charming instead of serving the point. Watch for **cutesy personification** (a tool, a bug, or a data glitch dressed up as a little character), **precious diminutives**, and **forced whimsy**. ❌ *"That little wart told me more than a clean demo ever could"* (a mis-grouped session dressed up as a cute mascot) → ✅ *"A clean demo could never have shown me this."* Carve-out: **dry / wry wit stays** — a bug that *"cheerfully reported '0 msgs'"* or a server that's *"deliberately boring"* is ironic, not cute, because it lands a real point; and the approved signature phrases (*"I commented the heck out of it"*, *"laziness is the mother of invention!"*) are established voice, not twee. The test: would you say it out loud to a senior colleague without wincing? Keep the warmth, cut the performance.
- **No manufactured aphorisms, slogan-triads, or cute wordplay.** A punchy line that *restates* the surrounding point in tidy parallel structure is an LLM reaching for profundity, not a payoff — a top AI tell. Four shapes to catch: **slogan-triads** (*"AI proposes. The harness scores. The human stays accountable."*; *"Consensus buys coverage; human judgment goes where it matters."*); **nested-quote wordplay** (*"I wanted 'this variant wins' to mean 'across runs.'"*); **cute-clever metaphors** doing a plain noun's job (*"two altitudes"* for placement; *"the unit of persuasion"* for a table); and the **dramatic mic-drop** that re-says the thesis for effect (*"Data did what no debate loop could."*; *"the punchline is boring on purpose"*). Almost always, **cut the flourish** — the surrounding sentences already made the point, so the aphorism is redundant on top of cute. Carve-out: the earned dry aside and the genuine verbless rhythmic beat stay (see the twee/precious rule above, and the verbless-tag carve-out under *"Active voice"*). The test: does the line *add* a concrete idea, or just re-state the last one with more polish? If it restates and preens, cut it.
- **Match the verb to the unit when quoting a metric change.** A *×N factor* is a speedup or a multiplier, so it goes up; an *absolute value* (ms, bytes, seconds) is what drops. Don't say a thing *"dropped 57×"* — it *"sped up ~57×"* (or *"got ~57× faster"*). Reserve *dropped / fell / shrank* for the absolute numbers: *"latency dropped from 1,474 ms to 230 ms"*, *"the payload shrank from 650 KB to 459 KB"*. Mixing the two (*"dropped 57×"*) reads as a unit error to any numerate reader. Also keep the **×-factor honest against the stated before/after**: if the table says 4.5 s → 87 ms, the prose says ~52×, not a rounder-sounding 57× from some other baseline; recompute rather than eyeball.
- *"Mode"* implies a sticky, persistent state the user switches into and stays in. Reserve it for settings that behave that way (Light / Dark / System theme, Emacs / Vim keybindings). For a one-off, per-action choice, name the choice: quoting a search query is a per-query decision (quotes or no quotes), so *"two modes you'll use day-to-day"* mislabels it. Same caution for *"toggle"* / *"switch"* / *"state"* when the thing is really a single decision: the word sets the reader's mental model, so pick one that matches the behavior.
  - Even when the state genuinely IS sticky, prefer a precise domain noun over the generic *"mode"* if one exists. The search **pin** survives panel close and a full page reload, so it clears the stickiness bar, yet it's a search *scope*, and the article already uses "scope" everywhere else. So *"a complementary mode"* → *"a complementary scope"*, *"makes a mode visible"* → *"makes the active scope visible"*, *"the same scoped mode"* → *"scoped the same way"*. *"Mode"* is the fallback you use only when no sharper noun (scope, filter, theme, layout) fits; a sharper noun tells the reader *what kind* of state it is, where "mode" only tells them *that* there is one.
- **Don't put the reader in the driver's seat for steps the agent performs on its own.** When you describe a tool an LLM client composes (MCP tools, multi-step pipelines), write the higher-level question the user actually types, and note that the client chains the steps under the hood. ❌ *"You skim the outline, decide which messages matter, and read them in full."* → ✅ *"You ask a higher-level question, and under the hood Claude pulls the outline, picks the messages that matter, and reads only those."* Keep the by-hand path as the explicit exception, used only when the reader wants the control. A literal tool call can still appear, framed as what the client emits under the hood rather than as something the reader types.
- **Lead with the composed, higher-order capability; frame the individual tools as the means.** When a feature exposes both low-level primitives (the four MCP tools, the separate pipeline steps) and the high-level thing they add up to (ask one question, the agent chains them), put the composition first and label it as the point; demote the primitive list to the building blocks underneath. Burying the payoff in a throwaway aside *after* the list inverts the emphasis. ❌ list the four tools, then tack on *"You almost never ask for those one at a time, though"* as an afterthought → ✅ list the four tools as what it *can* do, then open a new paragraph with the real point: *"More importantly, it lets Claude compose these, so you ask one normal question and it chains the steps for you."* Sibling to the driver's-seat rule above: that one says don't make the reader run the steps; this one says rank the composed capability above the primitives that power it, and signal the ranking (a new paragraph, a *"More importantly"*) rather than leaving the reader to infer it.
- **Name agent behaviors as the agent's, not as a habit the reader must adopt.** When the actor is Claude or the tool, prefer *pattern* (or plainly *what the tool does*) over *habit* / *discipline* / *routine*. "Habit" implies the reader has to change how they work; if the model follows the pattern on its own, say so and call it a pattern. The tell: a section names a behavior the prose itself then admits is automatic ("Claude works outline-first on its own"), so the "habit" framing contradicts the body. ❌ heading *"The Outline-First Habit"* over text explaining that Claude does it unprompted → ✅ *"The Outline-First Pattern."* Carve-out: *habit* is correct when the actor really is the human (the author's *"in-the-moment habit"* of keeping prompts current is a genuine human habit). Third in the agent-framing cluster with the two rules above.

### The "it's not X, it's Y" trope — one-shot example (do not use this construction)

This construction is a top-3 AI-writing tell. Avoid it even when the contrast feels natural. **Just state what it is; drop the negation.**

- ❌ *"The real payoff isn't neatness, it's **learning across silos**."*
- ✅ *"The real payoff is **learning across silos**."*

Other surface forms to avoid with the same fix:

- *"Not X. Y."* (two sentences)
- *"It's less about X and more about Y."*
- *"X is fine, but Y is the point."*

If you genuinely need to negate a prior framing (because the reader is mid-paragraph and needs to unlearn something), just say *"I don't mean X"* once and move on. Don't build paragraphs on the contrast.

---

## Revision passes (run every scan on every draft)

A draft that reads fine on the first pass still hides these. Run the scans below as **separate passes** — each hunts one failure mode, and trying to catch them all in one read misses most of them. Most map to a rule above; the scan is the discipline of actually looking. Run them on **both twins**, and keep shared surfaces (image alt text, attribution, cross-links, every factual claim) parallel between them. Earned across the Part 3 review (2026-06-03), where each pass turned up something the previous passes had read straight past.

A scan that comes back clean is a real result worth reporting — don't invent problems to look thorough, and don't soften or delete a true claim just to satisfy a pass (see *"Active voice"* note on precision-bearing qualifiers).

1. **AI tells.** Em-dashes; *"X, not Y"*; *"Here's the thing/problem"*; *"It's worth noting"*; *"honest"/"honestly"* as a self-label; sincerity intensifiers (*actually / genuinely / really / the very*); *quietly*/*silently* narrating a failure. → Anti-patterns.
2. **Flat or clunky sentences.** Read each sentence *whole*, not word-by-word: limp copulas, dangling clauses, noun-piles, garden-paths. The tell is "correct but no music" (8th-grade drift). Rewrite at the sentence level — never token-swap inside an already-broken frame, which is how a strained clause collapses into nonsense.
3. **Twee / cutesy / manufactured aphorism.** Cute personification, precious diminutives, forced whimsy (the *"little wart"*); plus slogan-triads, nested-quote wordplay, and cute-metaphor mic-drops that just restate the point. Keep dry wit; cut performance. → Anti-patterns.
4. **Clichés / stock phrases.** Tired idioms, and especially any stock phrase used **twice** (*"earns its keep"* ×3, *"baked into"* ×2, *"hard-won"*). Vary or cut. → the *reach-for / landed / footgun / heavy-lifting / bucket / martial+sports* bans.
5. **Passive voice and limp copulas.** Scan every *is/are/was/were/be/been* for a hidden transitive; promote the agent. This catches limp copulas (*"X is a Y that does Z"* → subject does Z) as well as passives proper. Carve-outs: legal boilerplate, verbatim quotes, predicate-adjective states (*"the dogfooding was front-loaded"*). → *"Active voice (critical)."*
6. **Wordiness / redundancy.** *"in order to"* → *"to"*; doubled appositives; a point already made a paragraph earlier; two words doing one word's work.
7. **Jargon a non-expert trips on.** Userdoc twin: **zero** unexplained jargon — gloss it (*"context window"* → *"the space it has left to think"*) or cut it, and link any prerequisite install. Long-form twin: only terms the deep-dive reader actually needs; define on first use.
8. **Sentence rhythm.** Vary length; land a short punch after a long build; avoid runs of same-length sentences, and make sibling paragraphs end in parallel (both on a short line, not one punchy and one trailing). → *"Sentence rhythm."*
9. **Hedges / weak qualifiers.** Cut filler *somewhat / fairly / a bit / kind of / I think*; keep only qualifiers carrying real precision (*"usually,"* when it's genuinely usually-not-always). Never hedge a true claim.
10. **Vague filler nouns/verbs.** *things / stuff / various / useful / good / a good one* → name the concrete thing.
11. **Number & fact consistency.** Cross-check every figure against the others and the real data (the *"25,689 messages"* vs *"twenty-thousand-message"* miss); round consistently across the piece.
12. **Terminology & formatting consistency.** *back end / front end* (nouns) vs *backend / frontend* (adjectives); product names; every identifier / path / flag in backticks; keyboard keys in bold backticks; **H2 Title Case / H3 sentence case**; a run of gerund-led headers stays gerund-led.
13. **Word over-repetition.** Grep distinctive words you've leaned on (*deliberate / exactly / outline-first*) and vary the ones that aren't load-bearing concepts.
14. **Motivate from the user's perspective.** Every section or paragraph about to explain mechanism leads with the user pain or payoff first. → *"Motivate technical detail from the user's perspective first."*
