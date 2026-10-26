# /// script
# requires-python = ">=3.11"
# dependencies = ["openai>=1.60", "python-dotenv"]
# ///
"""Extract goal-relevant info from Discord CSV exports with an LLM, then merge it into a guide.

uv run extract.py extract    projects/<name>/task.toml projects/<name>/data/*.csv
uv run extract.py synthesize projects/<name>/task.toml

Outputs go to projects/<name>/out/: extract/ (per-chunk cache), items.json, findings.json, guide.md.
"""
import asyncio, csv, json, re, sys, tomllib
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from openai import (APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError,
                    RateLimitError)

load_dotenv(Path(__file__).parent / ".env")
client = AsyncOpenAI()

CHUNK_CHARS = 40_000  # ~10k tokens of chat per call
OVERLAP = 15          # messages repeated between chunks so threads aren't cut
CONCURRENCY = 8
RETRY = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


def load_task(path: str) -> dict:
    task = tomllib.loads(Path(path).read_text())
    for key in ("context", "goal", "categories", "guide"):
        if key not in task:
            sys.exit(f"{path}: missing required key '{key}'")
    task.setdefault("extract_model", "gpt-5.4-nano")
    task.setdefault("synth_model", "gpt-5.5")
    if "other" not in task["categories"]:
        task["categories"].append("other")
    task["out"] = Path(path).parent / "out"
    return task


def item_schema(categories: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["items"],
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["category", "claim", "detail", "source_type", "msg_ids", "authors"],
            "properties": {
                "category": {"type": "string", "enum": categories},
                "claim": {"type": "string", "description": "One self-contained statement."},
                "detail": {"type": "string", "description": "Specifics: numbers, exact wording, tools, examples. Empty if none."},
                "source_type": {"type": "string", "enum": ["firsthand", "secondhand", "speculation", "question_unanswered"]},
                "msg_ids": {"type": "array", "items": {"type": "string"}},
                "authors": {"type": "array", "items": {"type": "string"}},
            }}}},
    }


MERGED_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["findings"],
    "properties": {"findings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["finding", "detail", "n_sources", "firsthand", "conflicts", "msg_ids"],
        "properties": {
            "finding": {"type": "string"},
            "detail": {"type": "string"},
            "n_sources": {"type": "integer", "description": "Distinct authors supporting it."},
            "firsthand": {"type": "boolean", "description": "At least one firsthand source."},
            "conflicts": {"type": "string", "description": "Contradicting claims, or empty."},
            "msg_ids": {"type": "array", "items": {"type": "string"}},
        }}}},
}


def extract_prompt(task: dict) -> str:
    return f"""{task['context']}
GOAL: {task['goal']}
Extract EVERY piece of information in the chat that is relevant to the goal.
Include questions people ask about it that nobody answers (source_type=question_unanswered).
Prefer recall over precision, but skip chatter, memes, and off-topic talk.
firsthand = the author reports their own direct experience. Keep exact numbers, names, and wording.
Each line is: [msg_id] timestamp author (↩ reply context): text. Cite msg_ids."""


def merge_prompt(task: dict) -> str:
    return f"""{task['context']}
GOAL: {task['goal']}
You get raw items extracted from chat chunks for one category. Deduplicate and merge them
into distinct findings. Count distinct authors as n_sources. Keep exact numbers and wording.
Note contradictions instead of hiding them. Drop items with no relevance to the goal."""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def line(r: dict) -> str:
    """One message as a prompt line: [msg_id] timestamp author (↩ reply context): text."""
    text = norm(r["content"])[:1500]
    if r.get("attachments"):
        text += f" [attachments: {len(r['attachments'].split())}]"
    if r.get("embed_text"):
        text += f" [embed: {r['embed_text'][:200]!r}]"
    reply = f" (↩ {r['reply_to'][:100]})" if r["reply_to"] else ""
    return f"[{r['id']}] {r['ts'][:16]} {r['author']}{reply}: {text}"


def load_lines(path: Path) -> list[str]:
    return [line(r) for r in csv.DictReader(open(path, encoding="utf-8"))]


def chunk(lines: list[str]) -> list[list[str]]:
    chunks, cur, size = [], [], 0
    for ln in lines:
        if size + len(ln) > CHUNK_CHARS and cur:
            chunks.append(cur)
            cur = cur[-OVERLAP:]
            size = sum(map(len, cur))
        cur.append(ln)
        size += len(ln)
    if cur:
        chunks.append(cur)
    return chunks


async def call_json(model: str, system: str, user: str, schema: dict, name: str, effort: str) -> dict:
    for attempt in range(4):
        try:
            r = await client.responses.create(
                model=model, instructions=system, input=user,
                reasoning={"effort": effort},
                text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
            )
            return json.loads(r.output_text)
        except RETRY as e:
            if attempt == 3:
                raise
            print(f"  retry {name}: {e}", file=sys.stderr)
            await asyncio.sleep(2 ** attempt * 5)


async def call_text(model: str, instructions: str, input: str, effort: str) -> str:
    r = await client.responses.create(model=model, instructions=instructions, input=input, reasoning={"effort": effort})
    return r.output_text


async def extract(task: dict, paths: list[str]):
    d = task["out"] / "extract"
    d.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    prompt, schema = extract_prompt(task), item_schema(task["categories"])

    async def one(channel: str, i: int, n: int, lines: list[str]) -> list[dict]:
        start = lines[0].split("]")[0][1:]  # cache key = channel + first message ID + length
        f = d / f"{channel}_{start}_{len(lines)}.json"
        if f.exists():  # cached; delete out/extract/ after changing the task prompts
            return json.loads(f.read_text())
        async with sem:
            res = await call_json(task["extract_model"], prompt, f"#{channel}\n" + "\n".join(lines),
                                  schema, "extract", "low")
        for it in res["items"]:
            it["channel"] = channel
        f.write_text(json.dumps(res["items"], indent=1))
        print(f"{channel} {i + 1}/{n}: {len(res['items'])} items")
        return res["items"]

    jobs = []
    for p in map(Path, paths):
        chunks = chunk(load_lines(p))
        jobs += [one(p.stem, i, len(chunks), c) for i, c in enumerate(chunks)]
    items = [it for its in await asyncio.gather(*jobs) for it in its]
    (task["out"] / "items.json").write_text(json.dumps(items, indent=1))
    print(f"{len(items)} items -> {task['out'] / 'items.json'}")


async def synthesize(task: dict):
    out = task["out"]
    items = json.loads((out / "items.json").read_text())
    by_cat = defaultdict(list)
    for it in items:
        by_cat[it["category"]].append({k: it[k] for k in ("claim", "detail", "source_type", "authors", "msg_ids")})

    async def merge(cat, its):
        res = await call_json(task["synth_model"], merge_prompt(task), f"Category: {cat}\n{json.dumps(its)}",
                              MERGED_SCHEMA, "merge", "medium")
        print(f"{cat}: {len(its)} items -> {len(res['findings'])} findings")
        return cat, res["findings"]

    merged = dict(await asyncio.gather(*(merge(c, v) for c, v in by_cat.items())))
    (out / "findings.json").write_text(json.dumps(merged, indent=1))

    guide = await call_text(task["synth_model"], f"{task['context']}\nGOAL: {task['goal']}\n{task['guide']}\nDo not invent facts.",
                            json.dumps(merged), "medium")
    (out / "guide.md").write_text(guide)
    print(f"-> {out / 'findings.json'}, {out / 'guide.md'}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("extract", "synthesize"):
        sys.exit(__doc__)
    cmd, task = sys.argv[1], load_task(sys.argv[2])
    asyncio.run(extract(task, sys.argv[3:]) if cmd == "extract" else synthesize(task))
