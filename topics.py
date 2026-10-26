# /// script
# requires-python = ">=3.11"
# dependencies = ["openai>=1.60", "python-dotenv"]
# ///
"""Per-topic deep dives: select the messages about each topic, then write a detailed doc from the raw messages.

uv run topics.py propose <task.toml> [--force]     # LLM proposes topics from out/findings.json -> topics.toml (edit it)
uv run topics.py select  <task.toml> [--topic X] [--fill 3] [--model M]
uv run topics.py write   <task.toml> [--topic X]
uv run topics.py update  <task.toml>               # scrape new messages, select them, write what's new per topic

Outputs: <project>/topics/<name>/messages.txt (selected messages + their reply threads), doc.md, new-<date>.md.
"""
import argparse, asyncio, csv, hashlib, json, sys, tomllib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from extract import call_json, call_text, chunk, line, load_task, norm

IDS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ids"],
              "properties": {"ids": {"type": "array", "items": {"type": "string"}}}}
CONCURRENCY = 8


# ---------- data ----------

def load_rows(data: Path) -> list[dict]:
    """All messages from data/*.csv, sorted by time, with reply targets resolved."""
    rows = []
    for f in sorted(data.glob("*.csv")):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            r["channel"] = f.stem
            r.setdefault("reply_to_id", "")  # older CSVs lack this column
            rows.append(r)
    rows.sort(key=lambda r: int(r["id"]))
    resolve_replies(rows)
    return rows


def resolve_replies(rows: list[dict]):
    """Fills reply_to_id for data scraped before it was captured: the reply preview quotes the
    start of the target message, so match it against earlier messages in the same channel."""
    by_ch = defaultdict(list)
    for r in rows:
        by_ch[r["channel"]].append(r)
    for msgs in by_ch.values():
        for i, r in enumerate(msgs):
            if r.get("reply_to_id") or not r["reply_to"]:
                continue
            quote = norm(r["reply_to"])
            for t in reversed(msgs[max(0, i - 3000):i]):
                pref = norm(t["content"])[:40]
                if len(pref) >= 6 and pref in quote:
                    r["reply_to_id"] = t["id"]
                    break


# ---------- topics ----------

def load_topics(task_path: str) -> tuple[dict, list[dict], Path]:
    task = load_task(task_path)
    proj = Path(task_path).parent
    tfile = proj / "topics.toml"
    if not tfile.exists():
        sys.exit(f"{tfile} not found. Run 'propose' first or write it (see README).")
    topics = tomllib.loads(tfile.read_text()).get("topics", [])
    for t in topics:
        for k in ("name", "goal", "doc"):
            if k not in t:
                sys.exit(f"{tfile}: topic {t.get('name', '?')} is missing '{k}'")
    return task, topics, proj


def pick(topics: list[dict], name: str | None) -> list[dict]:
    if not name:
        return topics
    sel = [t for t in topics if t["name"] == name]
    if not sel:
        sys.exit(f"no topic '{name}'. Topics: {[t['name'] for t in topics]}")
    return sel


def select_prompt(task: dict, topic: dict) -> str:
    return f"""{task['context']}
TOPIC: {topic['goal']}
Return the msg_ids of every message that carries information about the TOPIC: facts, firsthand reports,
numbers, names, exact wording, details, corrections, and questions about the topic that someone answers.
Include short replies ("yes", "same", "mine was X") when their reply context (↩) shows they answer a
topic question. Exclude off-topic messages and jokes with no information. When in doubt, include.
Each line is: [msg_id] timestamp author (↩ reply context): text."""


async def select_ids(task: dict, topic: dict, rows: list[dict], cache: Path) -> set[str]:
    """Runs the narrow topic goal over all chunks of `rows`; returns the selected message IDs."""
    key = hashlib.sha1((task["context"] + topic["goal"] + task["extract_model"]).encode()).hexdigest()[:10]
    d = cache / key  # a new goal gets a new cache folder
    d.mkdir(parents=True, exist_ok=True)
    prompt, sem = select_prompt(task, topic), asyncio.Semaphore(CONCURRENCY)
    by_ch = defaultdict(list)
    for r in rows:
        by_ch[r["channel"]].append(line(r))

    async def one(ch: str, i: int, lines: list[str]) -> list[str]:
        start = lines[0].split("]")[0][1:]  # chunk cache key = channel + first message ID
        f = d / f"{ch}_{start}_{len(lines)}.json"
        if f.exists():
            return json.loads(f.read_text())
        async with sem:
            ids = (await call_json(task["extract_model"], prompt, f"#{ch}\n" + "\n".join(lines),
                                   IDS_SCHEMA, "select", "low"))["ids"]
        f.write_text(json.dumps(ids))
        return ids

    jobs = [one(ch, i, c) for ch, ls in by_ch.items() for i, c in enumerate(chunk(ls))]
    valid = {r["id"] for r in rows}
    return {i for ids in await asyncio.gather(*jobs) for i in ids if i in valid}


def with_threads(selected: set[str], rows: list[dict]) -> set[str]:
    """Adds each selected message's reply target and the direct replies to it."""
    ctx = set()
    for r in rows:
        if r["id"] in selected and r.get("reply_to_id"):
            ctx.add(r["reply_to_id"])
        if r.get("reply_to_id") in selected:
            ctx.add(r["id"])
    return ctx - selected


def fill_gaps(selected: set[str], rows: list[dict], gap: int) -> set[str]:
    """Adds messages that sit between two selected messages of the same channel when at most `gap`
    messages separate them: short follow-ups inside a topic conversation rarely look on-topic alone."""
    by_ch = defaultdict(list)
    for r in rows:
        by_ch[r["channel"]].append(r["id"])
    out = set()
    for ids in by_ch.values():
        idx = [n for n, i in enumerate(ids) if i in selected]
        for a, b in zip(idx, idx[1:]):
            if 1 < b - a <= gap + 1:
                out.update(ids[a + 1:b])
    return out


def write_messages(path: Path, topic: dict, rows: list[dict], selected: set[str], ctx: set[str]):
    keep = [r for r in rows if r["id"] in selected or r["id"] in ctx]
    out, ch = [f"# Topic: {topic['name']}", f"# {len(selected)} selected (*), {len(ctx)} context: reply threads and conversation gaps ( ).", ""], None
    for r in sorted(keep, key=lambda r: (r["channel"], int(r["id"]))):
        if r["channel"] != ch:
            ch = r["channel"]
            out += ["", f"## #{ch}"]
        out.append(("* " if r["id"] in selected else "  ") + line(r))
    path.write_text("\n".join(out) + "\n")


async def run_select(task, topic, proj, rows, label="", fill=3) -> Path:
    tdir = proj / "topics" / topic["name"]
    tdir.mkdir(parents=True, exist_ok=True)
    selected = await select_ids(task, topic, rows, proj / "topics" / ".cache")
    all_rows = load_rows(proj / "data") if label else rows
    ctx = (with_threads(selected, all_rows) | fill_gaps(selected, all_rows, fill)) - selected
    out = tdir / f"messages{label}.txt"
    write_messages(out, topic, all_rows, selected, ctx)
    size = len(out.read_text()) // 4
    print(f"{topic['name']}: {len(selected)} selected + {len(ctx)} context of {len(rows)} messages"
          f" ({(len(selected) + len(ctx)) / max(1, len(rows)):.0%}), ~{size:,} tokens -> {out}")
    return out


WRITE_RULES = """Lines starting with '* ' were selected as on-topic; lines starting with two spaces are reply context.
Each line is: [msg_id] timestamp author (↩ reply context): text.
Rules:
- Weigh evidence. Count distinct authors. Firsthand reports (the author did it) outweigh relays, rumors,
  and speculation. Later reports from people with direct experience outweigh earlier guesses.
- Spot jokes, trolls, sarcasm, and retractions, and leave them out or list them as noise with the reason.
- Keep exact numbers, names, signatures, and short exact phrases. Never invent facts.
- Mark your own analysis or inference with "Note:".
- Plain Markdown. No collapsible sections. No message IDs or usernames unless the doc instructions ask for them."""


async def cmd_write(task, topics, proj):
    async def one(t):
        tdir = proj / "topics" / t["name"]
        msgs = tdir / "messages.txt"
        if not msgs.exists():
            sys.exit(f"{msgs} not found. Run 'select' first.")
        doc = await call_text(task["synth_model"], f"{task['context']}\nTOPIC: {t['goal']}\nDOC INSTRUCTIONS: {t['doc']}\n{WRITE_RULES}",
                              msgs.read_text(), "high")
        (tdir / "doc.md").write_text(doc)
        print(f"{t['name']}: -> {tdir / 'doc.md'}")
    await asyncio.gather(*map(one, topics))


# ---------- propose ----------

def toml_str(s: str) -> str:
    return '"""' + s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"') + '"""'


async def cmd_propose(task_path: str, force: bool):
    task = load_task(task_path)
    proj = Path(task_path).parent
    tfile = proj / "topics.toml"
    if tfile.exists() and not force:
        sys.exit(f"{tfile} exists. Use --force to overwrite.")
    findings = proj / "out" / "findings.json"
    if not findings.exists():
        sys.exit(f"{findings} not found. Run extract.py extract + synthesize first.")
    schema = {"type": "object", "additionalProperties": False, "required": ["topics"],
              "properties": {"topics": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["name", "goal", "doc"],
                  "properties": {"name": {"type": "string", "description": "kebab-case"},
                                 "goal": {"type": "string"}, "doc": {"type": "string"}}}}}}
    topics = (await call_json(task["synth_model"], f"""{task['context']}\nGOAL: {task['goal']}
From the merged findings, propose 2-6 topics that each deserve a separate detailed document.
Choose topics by what the user needs to act on, not by the extraction categories.
For each: name (kebab-case); goal = one paragraph listing exactly which facts to collect, used to select
messages, so be specific and inclusive; doc = instructions for the document (sections, ranking, level of detail).""",
                               findings.read_text(), schema, "topics", "medium"))["topics"]
    body = "# Topics for topics.py. Edit freely. Optional per topic: existing = \"docs/x.md\" (doc that update compares against).\n"
    for t in topics:
        body += f"\n[[topics]]\nname = {json.dumps(t['name'])}\ngoal = {toml_str(t['goal'])}\ndoc = {toml_str(t['doc'])}\n"
    tfile.write_text(body)
    print(body)
    print(f"-> {tfile}")


# ---------- update ----------

async def cmd_update(task, topics, proj):
    from browser import guild_id, sidebar_channels
    from scrape import parse_time, scrape_window, to_csv
    data = proj / "data"
    chfile = data / "channels.json"
    channels = json.loads(chfile.read_text()) if chfile.exists() else {}
    guild = guild_id()
    missing = [f.stem for f in data.glob("*.csv") if f.stem not in channels]
    if missing:
        ids = sidebar_channels(guild)
        for name in missing:
            if name not in ids:
                sys.exit(f"channel '{name}' not in the sidebar; add it to {chfile} as {{\"{name}\": \"<id>\"}}")
            channels[name] = ids[name]
        chfile.write_text(json.dumps(channels, indent=1))

    before = {r["id"] for r in load_rows(data)}
    for name, cid in channels.items():
        last = max(r["ts"] for r in csv.DictReader(open(data / f"{name}.csv", encoding="utf-8")))
        r = scrape_window(guild, cid, parse_time(last), datetime.now(timezone.utc), data / f"{name}.jsonl")
        to_csv(data / f"{name}.jsonl")
        print(f"#{name}: {r['kept']} messages since {last[:16]}")
    rows = load_rows(data)
    new = [r for r in rows if r["id"] not in before]
    if not new:
        print("no new messages")
        return

    date = datetime.now().strftime("%Y-%m-%d")
    parts = []
    for t in topics:
        msgs = await run_select(task, t, proj, new, label=f"-new-{date}")
        sel = msgs.read_text()
        existing = proj / t["existing"] if t.get("existing") else proj / "topics" / t["name"] / "doc.md"
        prior = existing.read_text() if existing.exists() else "(no existing doc)"
        text = await call_text(task["synth_model"], f"""{task['context']}\nTOPIC: {t['goal']}
You get the EXISTING DOC and NEW MESSAGES. Report only information that is new or that changes or
contradicts the existing doc. Be very concise: short bullets, most important first. If nothing is new, say so.
{WRITE_RULES}""",
                               f"EXISTING DOC:\n{prior}\n\nNEW MESSAGES:\n{sel}", "high")
        out = proj / "topics" / t["name"] / f"new-{date}.md"
        out.write_text(text)
        parts.append(f"## {t['name']}\n\n{text.strip()}\n")
        print(f"{t['name']}: -> {out}")
    combined = proj / "topics" / f"new-info-{date}.md"
    combined.write_text(f"# New info ({len(new)} new messages, {date})\n\n" + "\n".join(parts))
    print(f"-> {combined}")


async def main(a):
    if a.cmd == "propose":
        return await cmd_propose(a.task, a.force)
    task, topics, proj = load_topics(a.task)
    if a.cmd == "select":
        if a.model:
            task["extract_model"] = a.model
        rows = load_rows(proj / "data")
        for t in pick(topics, a.topic):
            await run_select(task, t, proj, rows, fill=a.fill)
    elif a.cmd == "write":
        await cmd_write(task, pick(topics, a.topic), proj)
    else:
        await cmd_update(task, topics, proj)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("propose", "select", "write", "update"):
        s = sub.add_parser(name)
        s.add_argument("task")
        if name == "propose":
            s.add_argument("--force", action="store_true")
        if name in ("select", "write"):
            s.add_argument("--topic")
        if name == "select":
            s.add_argument("--fill", type=int, default=3, help="keep up to N messages between two selected ones")
            s.add_argument("--model", help="selection model; default: extract_model from task.toml")
    asyncio.run(main(p.parse_args()))
