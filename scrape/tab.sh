#!/bin/zsh
# usage: tab.sh <channel_id> <jsfile> — runs JS in the Chrome tab whose URL contains channel_id
osascript - "$1" "$2" <<'AS'
on run {cid, f}
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if URL of t contains cid then return execute t javascript (read POSIX file f)
      end repeat
    end repeat
  end tell
  error "no tab for " & cid
end run
AS
