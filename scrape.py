# /// script
# requires-python = ">=3.11"
# ///
"""Scrape a time range of a Discord channel from an open Chrome tab of the server.

uv run scrape.py range <channel-url-or-id> <out-dir> --from "2026-10-21 18:00" [--to 2026-10-23] [--name x]

Times are local unless they end in Z or have an offset. A date-only --to includes that whole day.
Output: <out-dir>/<channel>.jsonl (appended, raw) and <channel>.csv (deduplicated, sorted).
For the full history of a channel use scrape/run.sh.
"""
import argparse, json, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from browser import ROOT, guild_id, js, navigate, wait_for

SCRAPE_JS = (ROOT / "scrape" / "scrape.js").read_text()
DRAIN_JS = (ROOT / "scrape" / "drain.js").read_text()
DISCORD_EPOCH_MS = 1420070400000


def snowflake(t: datetime) -> int:
    """Smallest message ID at time t. Discord jumps to the nearest messages even if no message has it."""
    return (int(t.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22


def parse_time(s: str, end: bool = False) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if end and len(s) == 10:  # date only: include the whole day
        t += timedelta(days=1)
    return t.astimezone(timezone.utc)  # naive -> local time


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def channel_id(s: str) -> str:
    cid = s.split("?")[0].rstrip("/").split("/")[-1]
    if not cid.isdigit():
        sys.exit(f"not a channel URL or ID: {s}")
    return cid


def scrape_window(guild: str, cid: str, start: datetime, end: datetime | None, out: Path) -> dict:
    """Jumps to `end` (or the latest message), scrolls to the window edges,
    and appends messages with start <= ts <= end to `out`."""
    tab = guild  # the tab's URL contains the guild ID; navigation changes the channel part
    anchor = str(snowflake(min(end, datetime.now(timezone.utc)))) if end else None
    navigate(tab, f"/channels/{guild}/{cid}" + (f"/{anchor}" if anchor else ""))
    wait_for(tab, f"String(location.pathname.includes('{cid}') && !!document.querySelector('li[id^=\"chat-messages-{cid}-\"]'))",
             lambda v: v == "true", 30)
    time.sleep(1)  # let the jump settle before scrolling
    cfg = {"start": iso(start), "end": iso(end) if end else None}
    js(tab, f"window.__scr = null; window.__cfg = {json.dumps(cfg)}; 'ok'")
    js(tab, SCRAPE_JS)
    kept = seen = 0
    with open(out, "a") as f:
        while True:
            d = json.loads(js(tab, DRAIN_JS))
            for m in d["q"]:
                seen += 1
                if cfg["start"] <= m["ts"] and (not cfg["end"] or m["ts"] <= cfg["end"]):
                    f.write(json.dumps(m) + "\n")
                    kept += 1
            if d["done"]:
                break
            time.sleep(2)
    st = json.loads(js(tab, "JSON.stringify({s: __scr.reachedStart, e: __scr.reachedEnd, h: __scr.hitStart})"))
    return {"kept": kept, "seen": seen, "reached_start": st["s"], "reached_end": st["e"] or not cfg["end"],
            "channel_start": st["h"]}


def remember_channel(out_dir: Path, name: str, cid: str):
    f = out_dir / "channels.json"
    d = json.loads(f.read_text()) if f.exists() else {}
    d[name] = cid
    f.write_text(json.dumps(d, indent=1))


def to_csv(jsonl: Path):
    subprocess.run([sys.executable, "-I", ROOT / "scrape" / "to_csv.py", jsonl, jsonl.with_suffix(".csv")], check=True)


def report(label: str, r: dict):
    warn = []
    if not r["reached_start"]:
        warn.append("did not reach the start time")
    if not r["reached_end"]:
        warn.append("did not reach the end time")
    print(f"{label}: kept {r['kept']} of {r['seen']} loaded"
          + (" (channel starts inside the range)" if r["channel_start"] else "")
          + (f"  WARNING: {', '.join(warn)}; run again to continue" if warn else ""))


def cmd_range(args):
    guild, cid = guild_id(), channel_id(args.channel)
    # no --to means now: jump to the latest message (without a jump target Discord restores the last viewed position)
    start, end = parse_time(args.from_), parse_time(args.to, end=True) if args.to else datetime.now(timezone.utc)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    navigate(guild, f"/channels/{guild}/{cid}")
    name = args.name or wait_for(guild, (ROOT / "scrape" / "name.js").read_text(), lambda v: v and v != cid, 15)
    out = out_dir / f"{name}.jsonl"
    remember_channel(out_dir, name, cid)
    r = scrape_window(guild, cid, start, end, out)
    report(f"#{name} {iso(start)} .. {iso(end)}", r)
    to_csv(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("range")
    r.add_argument("channel"); r.add_argument("out_dir")
    r.add_argument("--from", dest="from_", required=True); r.add_argument("--to"); r.add_argument("--name")
    args = p.parse_args()
    cmd_range(args)
