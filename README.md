# discord-distill

Scrape Discord channels from your open Chrome tab, then use LLMs to turn thousands of messages into short docs about the topics you care about.

1. **Scrape** a channel (full history or a time range) to CSV.
2. **Overview:** extract facts relevant to your goal into `guide.md`.
3. **Deep dives:** pick the raw messages for each topic and write a detailed doc.
4. **Update:** scrape new messages later and report only what's new.

## Requirements

- macOS, Google Chrome, logged in to Discord in the browser
- Chrome → View → Developer → **Allow JavaScript from Apple Events**. This lets any app with Automation permission for Chrome run JavaScript in your tabs, so turn it off when you are done.
- [uv](https://docs.astral.sh/uv/)
- `.env` with `OPENAI_API_KEY=...`

> **Warning:** Automating a user account violates Discord's Terms of Service. Use at your own risk.
>
> Only use it on servers you are a member of, and respect members' privacy.

## Usage

Copy `projects/example/` to `projects/my-task/` and edit `task.toml` to describe your goal.

```sh
# Scrape a whole channel (tab open on the channel, at the latest message)
scrape/run.sh https://discord.com/channels/<server>/<channel> projects/my-task/data

# Or scrape a time range (any tab on the server)
uv run scrape.py range <channel-url> projects/my-task/data --from "2026-10-21 18:00" --to 2026-10-23

# Overview
uv run extract.py extract    projects/my-task/task.toml projects/my-task/data/*.csv
uv run extract.py synthesize projects/my-task/task.toml   # → out/guide.md

# Deep dives
uv run topics.py propose projects/my-task/task.toml   # → topics.toml (edit it)
uv run topics.py select  projects/my-task/task.toml   # → topics/<name>/messages.txt
uv run topics.py write   projects/my-task/task.toml   # → topics/<name>/doc.md

# Later: get new messages and report what changed
uv run topics.py update  projects/my-task/task.toml
```

While a full scrape runs, keep the tab visible in its own window and don't touch it. It does about 15 messages/s and resumes if you run it again.

Cost: extraction for ~10k messages costs cents; the merge costs less than $1.

See [docs/reference.md](docs/reference.md) for file formats, options, and known limits.

## Alternatives

[DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter) is faster, cross-platform, and exports threads and reactions. On servers you don't admin it needs your user token and calls the API directly, which Discord can flag.

## License

[MIT](LICENSE)
