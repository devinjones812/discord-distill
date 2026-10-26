# Reference

## Scraping

### Full channel: `scrape/run.sh <channel-url-or-id> <out-dir> [name]`

- Injects `scrape.js` into the tab whose URL contains the channel ID. It scrolls up to the start of the channel and collects each message as it renders.
- Saves progress to `<name>.jsonl` every 10 s. Run the same command again to resume.
- Writes `<name>.csv` at the end. The default name is the channel name from the tab title.
- Use `scrape/tabs.sh` to list open Discord tabs.

Rules while it runs:

- Start at the **latest** message. The scraper only scrolls up.
- Keep the tab **visible, in its own window**. Chrome slows background tabs.
- Do not scroll or switch channels in that tab.
- Messages posted after the start are not captured.

### Time range: `uv run scrape.py range <channel> <out-dir> --from T [--to T]`

- Times are local unless they end in `Z` or have an offset. A date-only `--to` includes the whole day.
- Without `--to`, the range ends at the latest message.
- It jumps directly to the end time (Discord message IDs encode timestamps), so it is fast.

### CSV columns

`id, ts, author, content, reply_to, reply_to_id, edited, attachments, embed_text`

- `id`: Discord snowflake (sorts by time). `ts`: ISO-8601 UTC.
- `reply_to`: quoted text of the replied-to message (up to 200 chars). `reply_to_id`: its ID.
- `attachments`: space-separated URLs. `embed_text`: text of link previews.
- Not captured: reactions, deleted messages, threads (scrape a thread as its own channel).

If Discord changes its HTML, update the selectors in `scrape/scrape.js`.

## Overview (`extract.py`)

Splits each CSV into ~10k-token chunks. A cheap model extracts structured items from each chunk. A stronger model merges them per category and writes `guide.md`.

### `task.toml`

```toml
context = """Who is talking and why."""
goal = """What to extract and what is off-topic. Define 'firsthand' for this task."""
categories = ["category_a", "category_b", "other"]
guide = """Instructions for the final guide: sections, ranking, tone."""

# optional
extract_model = "gpt-5.4-nano"
synth_model = "gpt-5.5"
```

### Outputs (`out/`)

| File | Content |
|---|---|
| `extract/*.json` | Items per chunk. Cached: delete after you change `context`, `goal`, or `categories`. |
| `items.json` | All items |
| `findings.json` | Merged findings with source counts, conflicts, and message IDs |
| `guide.md` | Final guide |

Known limits: source counts run high, and the cheap model under-marks `firsthand`. Check important claims against the CSV by message ID.

## Deep dives (`topics.py`)

The overview loses detail. `topics.py` gives a strong model the raw messages for one topic.

### `topics.toml`

```toml
[[topics]]
name = "setup-problems"
goal = """Exactly which facts belong to this topic. Used to select messages."""
doc = """Instructions for the document."""
existing = "docs/setup.md"   # optional: doc that `update` compares against
```

`propose` drafts this file from `findings.json`. Edit it: a few narrow topics work better than many broad ones.

### Commands

- `select [--topic X] [--fill 3] [--model M]`: a cheap model picks relevant messages. It then adds reply targets, direct replies, and short gaps (≤ `--fill` messages) between picks. Output: `topics/<name>/messages.txt` (`* ` marks picks).
- `write [--topic X]`: a strong model writes `topics/<name>/doc.md` from `messages.txt`. It marks its own inferences with "Note:".
- `update`: scrapes each channel from its last message to now, selects new relevant messages, and writes `topics/<name>/new-<date>.md` with only what's new.

In testing on a 9,400-message project, `select` kept 10 of 12 key facts from 29% of the messages. Check important claims in `messages.txt`.
