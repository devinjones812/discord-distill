# discord-distill

Scrape Discord channels from an open Chrome tab, then use LLMs to find everything relevant to a goal: a broad overview first, then a detailed doc per topic, then updates with only what's new.

```
            ┌─ scrape/run.sh ──── full channel history ─┐
Chrome tab ─┤                                           ├─▶ data/<channel>.csv
            └─ scrape.py range ── a date/time range ────┘          │
                                                                    ▼
     extract.py (overview: out/guide.md) ─▶ topics.py propose ─▶ select ─▶ write (topics/<name>/doc.md)
                                                                    └──▶ update (new messages → what's new)
```

## Requirements

- macOS, Google Chrome, logged in to Discord in the browser
- Chrome → View → Developer → **Allow JavaScript from Apple Events** (turn on). While it is on, any app that has macOS Automation permission for Chrome can run JavaScript in your logged-in tabs (email, bank). Turn it off when you are done.
- [uv](https://docs.astral.sh/uv/) (it installs the Python dependencies automatically)
- `.env` in the repo root with `OPENAI_API_KEY=...`

**Discord ToS:** scraping with your user account is automation of a user account, which Discord's Terms of Service do not allow. The risk for occasional reading of channels you can already see is low, but it is not zero. Use this at your own risk.

**Why not [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter)?** It is faster, works on any OS, and exports threads and reactions. But on a server you don't admin, it needs your user token: a full-access credential that you copy from the browser and must keep safe. It also calls the API directly at high speed, which is the pattern Discord flags as a self-bot. This tool needs no token, and it reads the normal web client at about the speed of a person who scrolls. Use DiscordChatExporter if you accept those risks.

## Quick start

Write `projects/my-task/task.toml` first (see [Task file](#task-file-projectsnametasktoml)). Then get the messages:

```sh
scrape/tabs.sh    # list open Discord tabs and their URLs

# A. Whole channel. Tab open on the channel, scrolled to the latest message.
scrape/run.sh https://discord.com/channels/<server>/<channel> projects/my-task/data

# B. A time range. Any tab of the server; it navigates by itself.
uv run scrape.py range <channel-url-or-id> projects/my-task/data --from "2026-10-21 18:00" --to 2026-10-23
```

Then the overview, the per-topic docs, and later updates:

```sh
uv run extract.py extract    projects/my-task/task.toml projects/my-task/data/*.csv
uv run extract.py synthesize projects/my-task/task.toml   # out/guide.md
uv run topics.py  propose    projects/my-task/task.toml   # topics.toml; edit it
uv run topics.py  select     projects/my-task/task.toml   # topics/<name>/messages.txt
uv run topics.py  write      projects/my-task/task.toml   # topics/<name>/doc.md
uv run topics.py  update     projects/my-task/task.toml   # later: scrape new messages, report what's new
```

**No search-first mode.** An earlier version used Discord search to scrape only the windows around keyword hits. In the test project, those windows covered 97% of all messages, so it saved almost nothing and was removed.

## Scraping

`scrape/run.sh <channel-url-or-id> <out-dir> [name]`

- Injects `scrape.js` into the tab whose URL contains the channel ID. The script scrolls up until it reaches "This is the start of #channel", and collects each message as it renders.
- `poll.sh` saves new messages to `<name>.jsonl` every 10 s, so a crash or closed tab loses at most 10 s of data. Run the same command again to continue. `to_csv.py` removes duplicates by message ID.
- At the end it writes `<name>.csv`. The default name is the channel name from the tab title.
- Speed: about 15 messages/s per tab. ~5,000 messages take about 5–10 minutes.

**Rules while it runs:**
- Start with the tab at the **latest** message. The scraper only scrolls up, so it misses anything below the starting position.
- Keep each scraping tab **visible, in its own window**. Chrome slows timers in background tabs, and the scrape stalls.
- Do not scroll or switch channels in that tab.
- Messages posted after the scrape starts are not captured.

**CSV columns:** `id, ts, author, content, reply_to, reply_to_id, edited, attachments, embed_text`
- `id` is the Discord snowflake (sortable by time). `ts` is ISO-8601 UTC.
- `author` is filled forward for grouped messages.
- `reply_to` is the quoted text of the replied-to message (up to 200 chars). `reply_to_id` is its message ID (empty in CSVs scraped before Oct 25, 2026; `topics.py` then matches the quote to an earlier message, which resolved ~95% of resolvable replies in testing).
- `attachments` holds space-separated URLs. `embed_text` holds the text of link previews and attachments.
- Not captured: reactions, deleted messages, thread contents (open the thread as its own channel and scrape it separately).

### Time ranges (`scrape.py range`)

- `--from` is required. `--to` is optional; without it, the range ends at the latest message.
- Times are **local** unless they end in `Z` or have an offset (`2026-10-21T18:00Z`, `2026-10-21T18:00-07:00`). A date-only `--to` includes that whole day.
- It jumps straight to the end time: Discord message IDs encode their timestamp, and Discord opens a computed ID at the nearest messages even if no message has that ID. It then scrolls up to `--from` and down to `--to`. Only messages inside the range are saved.
- Tested against a full scrape: a 12-hour range gave 1,309 of 1,309 messages in 39 s, with nothing missing and nothing extra.

### Selectors

If Discord changes its HTML, the selectors in `scrape/scrape.js` (`li[id^="chat-messages-"]`, `message-content-<id>`, `message-username-<id>`) may need updates.

## Extraction

`extract.py` reads a task file, splits each CSV into ~10k-token chunks (15-message overlap), and sends each chunk to a cheap model. The model returns structured items: category, claim, detail, source type (firsthand / secondhand / speculation / unanswered question), message IDs, and authors. A stronger model then merges and deduplicates the items per category, counts distinct authors, notes conflicts, and writes `guide.md`.

### Task file (`projects/<name>/task.toml`)

```toml
context = """Who is talking and why. Example: The chat is from a Discord server for X.
The user wants Y."""

goal = """What to extract. Be specific; list the kinds of facts you want and what is off-topic.
Define 'firsthand' for this task (example: the author says they used the product)."""

categories = ["category_a", "category_b", "other"]   # how items are grouped and merged

guide = """Instructions for the final Markdown guide: sections, ranking, tone."""

# optional
extract_model = "gpt-5.4-nano"   # per-chunk extraction (many calls)
synth_model = "gpt-5.5"          # merge + guide (one call per category + one)
```

See `projects/example/` for a task file and a topics file.

### Outputs (`projects/<name>/out/`)

| File | Content |
|---|---|
| `extract/<channel>_<first-msg-id>_<n>.json` | Items per chunk. **Cached:** delete this folder after you change `context`, `goal`, or `categories`. |
| `items.json` | All items from all chunks |
| `findings.json` | Merged findings per category, with `n_sources`, `firsthand`, `conflicts`, `msg_ids` |
| `guide.md` | The final guide |

**Cost:** about 10k messages cost cents for extraction and less than $1 for the merge.

**Known limits:**
- `n_sources` tends to be too high, because the merge model counts authors loosely. Use it to rank findings, not as an exact count.
- The cheap model marks too few items as `firsthand`.
- Check important claims against the CSV by `msg_ids`.

## Deep dives (`topics.py`)

The overview loses detail: claims are summarized and source counts run high. For accurate docs, a strong model must read the **raw** messages about one topic. `topics.py` picks those messages and writes the doc.

### Topics file (`projects/<name>/topics.toml`)

```toml
[[topics]]
name = "setup-problems"
existing = "docs/setup.md"   # optional: doc that `update` compares against (default topics/<name>/doc.md)
goal = """Narrow and inclusive: exactly which facts belong to this topic. Used to select messages."""
doc = """Instructions for the document: sections, ranking, level of detail, practice questions, etc."""
```

`propose` writes a first version from `out/findings.json`. **Edit it.** For the test project the model proposed 6 overlapping topics; 2 narrow ones worked better.

### Commands

- **`select [--topic X] [--fill 3] [--model M]`**: the cheap model reads every chunk with the topic's narrow goal and returns the relevant message IDs. Then context is added:
  - **reply threads:** each selected message's reply target, and direct replies to it
  - **conversation gaps:** messages between two selected messages that are at most `--fill` apart. Short follow-ups ("same, mine was 2") rarely look on-topic alone.
  - Output: `topics/<name>/messages.txt`. Selected lines start with `* `, context lines with two spaces.
  - Cached per chunk and per goal; changing a goal re-runs only that topic.
- **`write [--topic X]`**: the strong model reads `messages.txt` and writes `topics/<name>/doc.md`, weighing evidence (firsthand vs. rumor, trolls, retractions) and marking its own inferences with "Note:". About 3 min per topic. You can also hand `messages.txt` to Claude Code instead.
- **`update`**: for each channel in `data/`, scrapes from its last message to now (channel IDs from `data/channels.json`, or the sidebar), runs `select` on the new messages only, and asks the strong model what is new compared with the existing doc. Writes `topics/<name>/new-<date>.md` and `topics/new-info-<date>.md`. Tested on a 3-hour subset (234 new messages, one topic): 4.5 min, mostly the strong-model call. The report listed only new details and flagged jokes as noise.

### Tested on a 9,411-message project

12 key facts behind a hand-written topic doc, and how many each selection method kept:

| Method | Facts kept | Messages kept |
|---|---|---|
| Overview items' `msg_ids` (broad goal) | 8/12 | 19% |
| … ±2 neighbors | 12/12 | 53% |
| `select`, nano, no context | 7/12 | 16% |
| `select`, nano, threads + gaps ≤3 (default) | **10/12** | **29%** (~75k tokens) |
| `select`, mini, threads + gaps ≤3 | 11/12 | 49% |

The 2 facts missed by default were minor tips. The generated doc had the right structure and main conclusions, but it was less precise than the hand-written one (for example "a few" instead of an exact count). Check the important claims in `messages.txt` before relying on them.

## Layout

```
README.md
extract.py                 LLM overview: extraction + merge (uv script)
topics.py                  per-topic select / write / update (uv script)
scrape.py                  scrape a time range (uv script)
browser.py                 run JS in a Chrome tab; in-app navigation; sidebar channel IDs
scrape/
  run.sh                   full-history scrape: tab → jsonl → csv
  tabs.sh                  list open Discord tabs
  tab.sh                   run a JS file in the tab whose URL contains a given string
  scrape.js                in-page scroller/collector (up, or up+down to a time window)
  drain.js, poll.sh        save collected messages to disk
  name.js, hitstart.js     helpers (channel name, reached-start check)
  to_csv.py                jsonl → deduplicated, sorted CSV
projects/
  example/                 task.toml, topics.toml
  <name>/                  your projects (git-ignored): data/, out/, topics/
```
