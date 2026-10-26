"""Run JavaScript in an open Chrome tab via AppleScript (scrape/tab.sh)."""
import json, subprocess, tempfile, time
from pathlib import Path

ROOT = Path(__file__).parent
TAB_SH = ROOT / "scrape" / "tab.sh"
DISCORD = "discord.com/channels/"


def js(tab: str, code: str) -> str:
    """Runs JS in the first Chrome tab whose URL contains `tab`; returns the result as a string.
    AppleScript reads the file as MacRoman, so the code must be ASCII; pass strings through json.dumps."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="ascii") as f:
        f.write(code)
    r = subprocess.run([TAB_SH, tab, f.name], capture_output=True, text=True)
    Path(f.name).unlink()
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def wait_for(tab: str, expr: str, until, timeout: float, every: float = 0.25) -> str:
    end = time.time() + timeout
    while time.time() < end:
        v = js(tab, expr)
        if until(v):
            return v
        time.sleep(every)
    raise TimeoutError(f"timed out waiting for {expr}")


def guild_id(tab: str = DISCORD) -> str:
    return js(tab, "location.pathname").split("/")[2]


def navigate(tab: str, path: str):
    """In-app navigation (no page reload): /channels/<guild>/<channel>[/<message-id>]."""
    js(tab, f"history.pushState(null, '', {json.dumps(path)}); dispatchEvent(new PopStateEvent('popstate', {{state: null}})); 'ok'")


def sidebar_channels(tab: str) -> dict[str, str]:
    """Channel name -> channel ID, from the server's channel list."""
    raw = js(tab, """JSON.stringify([...document.querySelectorAll('a[data-list-item-id^="channels___"]')]
        .map(a => [a.querySelector('[class*="name"]')?.innerText?.trim(), a.href.split('/').pop()]))""")
    return {n: i for n, i in json.loads(raw) if n}
