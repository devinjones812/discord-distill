#!/bin/zsh
# Lists open Discord tabs in Chrome: channel ID, title, URL
osascript <<'AS'
tell application "Google Chrome"
  set out to ""
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t contains "discord.com/channels/" then set out to out & (title of t) & "  " & (URL of t) & linefeed
    end repeat
  end repeat
  return out
end tell
AS
