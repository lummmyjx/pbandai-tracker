# P-Bandai Stock Tracker

Watches P-Bandai Singapore listings and sends you a Telegram alert the moment
one becomes buyable. Runs on your Windows 11 PC with a small dashboard for
adding and removing listings, plus a free cloud backup on GitHub Actions that
keeps watching while your PC is off.

Your two listings are already on the watchlist.

---

## Why it works this way

P-Bandai's storefront is a Vue single-page app. The HTML their server returns
contains no product data at all — stock status is painted in by JavaScript after
the page loads. That means a normal HTTP request (curl, requests, most simple
"website change" scripts) sees an empty shell and can never tell in-stock from
sold-out. So this tracker renders each page in a real headless Chromium and
reads the finished DOM, exactly as your browser would.

---

## Part 1 — Telegram bot (do this first, ~3 minutes)

1. Open Telegram and search for **@BotFather**. Send `/newbot`.
2. Give it any name, then a username ending in `bot`
   (e.g. `jx_pbandai_alerts_bot`).
3. BotFather replies with a token that looks like
   `8123456789:AAH7x...`. Copy it.
4. Search for **@userinfobot**, send it any message, and it replies with your
   numeric **Id**. Copy that too.
5. Find your new bot in Telegram search and send it `/start`. This matters —
   Telegram blocks bots from messaging people who have never opened the chat.

Keep both values handy for step 4 below.

---

## Part 2 — Set up on your PC

1. Install **Python 3.11 or newer** from python.org if you don't have it.
   On the first installer screen, tick **"Add Python to PATH"**.
2. Unzip this folder somewhere permanent, e.g. `C:\Tools\pbandai-tracker`.
   (Not your Downloads folder — the auto-start task will point at this path.)
3. Double-click **`setup.bat`**. It creates a virtual environment, installs the
   packages, and downloads the headless browser. The browser download is a few
   hundred MB, so give it a few minutes.
4. Open the newly created **`config.json`** in Notepad and paste in your two
   Telegram values:

   ```json
   "telegram_bot_token": "8123456789:AAH7x...",
   "telegram_chat_id": "123456789",
   ```

   Save and close.
5. Double-click **`start-tracker.bat`**. Your browser opens to
   `http://127.0.0.1:8765` and checking begins immediately.
6. Click **Send test alert** on the dashboard. If a message lands in Telegram,
   you're done.

Leave the black console window open while the tracker runs — closing it stops
the tracker.

### Start it automatically at login

Double-click **`install-autostart.bat`** once. From then on the tracker starts
silently about 30 seconds after you log in, with no console window, and the
dashboard is always at `http://127.0.0.1:8765`.

To undo it later, run this in Command Prompt:

```
schtasks /Delete /TN "P-Bandai Tracker" /F
```

---

## Part 3 — Free cloud backup (optional but recommended)

Your PC isn't always on, and restocks don't wait. This runs the exact same
checker on GitHub's servers every 5 minutes, forever, for free.

1. Create a **private** repository on GitHub (public works too, but private
   keeps your watchlist to yourself).
2. In that repo, go to **Settings → Secrets and variables → Actions → New
   repository secret** and add two secrets:
   - `TELEGRAM_BOT_TOKEN` — the same token
   - `TELEGRAM_CHAT_ID` — the same chat id
3. Push this folder to the repo. In Command Prompt, from the tracker folder:

   ```
   git init
   git add .
   git commit -m "P-Bandai tracker"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

   `config.json` is git-ignored, so your token never leaves your PC.
4. Open the repo's **Actions** tab and enable workflows if prompted. You should
   see "P-Bandai stock check" start running on its own. Use **Run workflow** to
   trigger one immediately and confirm it works.

After that, the **Sync to cloud backup** button on your dashboard pushes any
watchlist changes up, so both halves watch the same listings. If you'd rather
not use git, you can also edit `watchlist.json` directly in GitHub's web editor.

Two things worth knowing: GitHub's minimum schedule is 5 minutes and runs can be
delayed a few minutes when their queues are busy, so treat the cloud job as a
safety net rather than the fast path. And GitHub pauses scheduled workflows in
repos with no activity for 60 days — the tracker commits `state.json` whenever a
status changes, which normally keeps it alive on its own.

---

## Keeping your token safe

The only secret in this project is your Telegram bot token. Four layers protect
it, and they work whether the repo is public or private.

**1. It lives outside the repo (best option).** Put your token in
`C:\Users\YourName\.pbandai-tracker.json` instead of `config.json`:

```json
{
  "telegram_bot_token": "8123456789:AAH7x...",
  "telegram_chat_id": "123456789"
}
```

A file that isn't in the project folder cannot be committed, by any accident,
ever. Settings in this file override `config.json`, so you can keep everything
else in `config.json` as normal and put only the two secret values here.

**2. `.gitignore` covers it** — `config.json`, `.env`, `*.pem`, `*.key`,
anything with "secret" in the name, and the `debug/` folder.

**3. A pre-commit hook blocks it.** Run **`install-hooks.bat`** once after
`git init`. After that, git refuses any commit containing `config.json`, a
`.env` file, a `debug/` dump, or anything shaped like a Telegram bot token —
even if you force-add it. Try it:

```
git add -f config.json
git commit -m "test"          <-- refused
git reset HEAD config.json
```

The hook is bypassable with `git commit --no-verify`. That's deliberate: it's a
guard against accidents, not a lock against yourself.

**4. Errors are scrubbed.** The token sits inside the Telegram API URL, so a
network failure would normally print it into the console and `state.json`. Every
error message is filtered before it's logged or stored, and anything matching
the shape of a bot token is replaced with `<bot-token>` even if it arrives from
somewhere unexpected.

### If a token ever does get out

Don't try to rewrite git history — it's easier and more reliable to just kill
the token. Telegram → **@BotFather** → `/revoke` → pick your bot. You get a new
token instantly and the old one dies. Update your config file and your GitHub
secret, and you're done. Worst case for a leaked bot token is that a stranger
could send messages *as your bot* into that one chat; they cannot read your
other Telegram conversations or touch your account.

---

## Using it

The dashboard shows every tracked listing with its current status, the last
check time, and the exact text that drove the decision (e.g. `active button:
"ADD TO CART"`) so you can see it isn't guessing.

- **Add** — paste a P-Bandai URL, optionally give it a nickname, hit Add. It
  gets checked within seconds.
- **Remove** — the ✕ on the right of any card.
- **Check now** — forces an immediate pass instead of waiting for the timer.
- **Send test alert** — confirms Telegram still works.

You can also drive it from the command line:

```
.venv\Scripts\python.exe app.py add https://p-bandai.com/sg/item/A1234567890
.venv\Scripts\python.exe app.py remove A1234567890
.venv\Scripts\python.exe app.py list
```

Or just edit `watchlist.json` in Notepad — the tracker picks up changes on the
next pass.

### When an alert fires

You get one Telegram message with the product image, price, and a **🛒 Open
listing** button. It fires on the transition into stock, then goes quiet for 3
hours so a flickering listing can't spam you. If it sells out and comes back
later, you get alerted again.

---

## Knowing it's still alive

A tracker that fails silently is worse than none, because you stop checking
manually. Four things guard against that.

**`STATUS.md`** in the repo — rewritten on every cloud check. Open it on GitHub
and the top line tells you Healthy, Degraded, or Blind, with a table of every
listing, its status, and the exact text that decided it. If "Last check" is more
than about 30 minutes old, the schedule has stopped.

**`history.log`** in the repo — one line per cloud run, with per-item status
codes. Gaps in the timestamps are how you spot missed runs:

```
2026-08-08 09:15:02 UTC  cloud backup   checked=3 alerts=0 unreadable=0 failed=0  A2866726001=OUT A2891018002=OUT A2884010001=IN
```

Your PC keeps its own copy at `debug/local-history.log`, so the two never
fight over the same file.

**A "went blind" Telegram warning.** After 6 consecutive unreadable or failed
checks on a listing, you get one message saying so — then silence until it
recovers. This is what was missing when the PC quietly stopped reading pages:
an unreadable page is not a status, it's the tracker not working, and it now
says so.

**A daily digest at 9am Singapore time** listing every item and its status, so
you know the whole thing is alive even on days when nothing changes. Without it,
"nothing came back in stock" and "this died last week" look identical from your
phone.

Each Actions run also renders the status table on its **Summary** tab, so you
can see the result of a run without digging through logs.

---

## Settings

Everything lives in `config.json`. The ones you might actually want to change:

| Setting | Default | What it does |
|---|---|---|
| `check_interval_seconds` | `60` | Seconds between full passes |
| `jitter_seconds` | `15` | Random extra delay, so requests don't look robotic |
| `alert_cooldown_minutes` | `180` | Minimum gap between alerts for the same item |
| `confirm_reads` | `1` | In-stock reads needed before alerting. Set to `2` if you ever get a false alarm — costs you one cycle of delay |
| `alert_on_any_change` | `false` | Set `true` to also get quiet notes on every status change |
| `dashboard_port` | `8765` | Change if something else already uses that port |
| `headless` | `true` | Set `false` to watch the browser work, for debugging |

Restart the tracker after editing.

---

## If something looks wrong

**A listing shows "UNREADABLE".** The page loaded but no wording was
recognised. The tracker deliberately never treats this as in-stock or
sold-out. It saves the scraped page text to the `debug/` folder — send me one
of those files and I'll adjust the patterns.

**A listing shows "CHECK FAILED" repeatedly.** Usually the listing was removed,
or your connection dropped. After 5 consecutive failures you get one Telegram
warning, not a stream of them.

**P-Bandai changed their button wording.** Edit `rules.json` — it's all plain
regex, no Python involved. Then verify with:

```
.venv\Scripts\python.exe tests\test_detection.py
```

**Nothing arrives in Telegram.** Make sure you sent `/start` to your own bot.
Telegram silently drops messages to users who never opened the chat.

---

## Checking the logic yourself

Two offline test suites ship with it, neither needs network access:

```
.venv\Scripts\python.exe tests\test_detection.py   # 8 fixture pages through real Chromium
.venv\Scripts\python.exe tests\test_flow.py        # alerting, storage, dashboard API
```

`test_detection.py` covers in-stock, pre-order, sold-out, coming-soon, ended,
a hidden button, a disabled button, and an unpainted SPA shell.
`test_flow.py` covers URL normalisation, duplicate rejection, the alert
transition rules, cooldown suppression, error streaks, and every dashboard
endpoint.

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Dashboard, background loop, command line |
| `checker.py` | Headless browser + status classification |
| `engine.py` | One checking pass: state updates and alert decisions |
| `notify.py` | Telegram |
| `store.py` | Reads/writes `watchlist.json` and `state.json` |
| `rules.json` | Detection patterns — tune these, not the code |
| `config.json` | Your settings and token (git-ignored) |
| `watchlist.json` | What's being tracked |
| `state.json` | Last seen status per item, for de-duplicating alerts |

---

## One honest caveat

I built and tested this against local fixture pages, because the environment I
wrote it in has no route to p-bandai.com. The detection logic is verified, but
the exact button labels on P-Bandai's live pages are an educated guess based on
their standard storefront wording.

So on your first run, check the dashboard: each listing should read `sold_out`,
`coming_soon`, `ended`, or `in_stock` with a sensible reason next to it. If
anything says **UNREADABLE**, that's the guess missing — send me the file it
drops in `debug/` and it's a one-line fix to `rules.json`.
