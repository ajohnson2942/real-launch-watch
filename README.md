# Launch Watch

Get a push notification on your phone and desktop before rocket launches
happen — automatically, forever, for $0.

It works by checking [Spaceflight Now's launch schedule](https://spaceflightnow.com/launch-schedule/)
every hour and sending you a notification at whatever lead times you choose
(e.g. 3 days before, 1 day before, 3 hours before). It keeps working for
every future launch with no further setup — you never have to touch it
again after the one-time setup below, unless you want to change your
notification timing.

**Everything here is free**, using only free tiers of free services:

| Piece | What it does | Cost |
|---|---|---|
| GitHub | Hosts the code and runs the checker on a schedule | Free |
| GitHub Actions | Runs the checker every hour, forever | Free (uses a few seconds/hour, way under the free monthly limit) |
| ntfy.sh | Delivers the push notification to your phone/desktop | Free, no account needed |
| GitHub Pages | Hosts a small installable dashboard webpage | Free |

No credit card is required anywhere in this process.

---

## What you'll end up with

1. **Push notifications** on your phone (iOS/Android) and desktop via the
   free **ntfy** app, sent automatically before each launch.
2. An **installable dashboard webpage** ("Launch Watch") you can add to
   your home screen, showing the next launch with a live countdown and the
   full upcoming schedule — this is the part you can share with your dad.

---

## Setup (about 15 minutes, one time)

### Step 1 — Create a free GitHub account
Go to [github.com/signup](https://github.com/signup) if you don't already
have an account. It's free.

### Step 2 — Create a new repository from these files
1. On GitHub, click **New repository** (the green button, or
   [github.com/new](https://github.com/new)).
2. Name it something like `launch-watch`. You can make it **Private** —
   everything will still work.
3. Click **Create repository**.
4. On the new repo's page, click **uploading an existing file** and drag
   in *all* the files and folders from this project (keep the folder
   structure — `.github/workflows/check-launches.yml`, `docs/`, `data/`,
   `scraper.py`, `notifier.py`, `config.json`, `requirements.txt`).
5. Scroll down and click **Commit changes**.

*(If you're comfortable with git/command line, you can `git push` instead
— same result.)*

### Step 3 — Set your notification preferences
1. In your new repo, open `config.json` and click the pencil (✏️) icon to
   edit it right in the browser.
2. Change `"ntfy_topic"` to something unique and hard to guess — this is
   like a private channel name. For example:
   `"dad-rocket-launches-58213"`. **Don't leave it as the default** — pick
   your own random-ish name. Anyone who knows this exact name could send
   fake notifications to it, so make it long and not-obvious (no need to
   write it down anywhere public).
3. Adjust `"lead_times_hours"` to whatever you want. This is a list of
   hours-before-launch. Examples:
   - `[72, 24, 3]` → notified 3 days before, 1 day before, and 3 hours before (the default)
   - `[168, 24, 1]` → a week before, a day before, and an hour before
   - `[24]` → just once, a day before
4. `"rocket_keywords"` controls which launches you get notified about.
   It defaults to `["Falcon 9", "Falcon Heavy", "Starship"]` (SpaceX
   only). Set it to `[]` to get every launch from every provider on the
   schedule.
5. Click **Commit changes**.

### Step 4 — Turn on GitHub Actions (the free scheduler)
1. Go to the **Actions** tab of your repo.
2. If prompted, click **I understand my workflows, go ahead and enable
   them**.
3. Click on **Check launch schedule** in the left sidebar, then click
   **Run workflow** → **Run workflow** to trigger it manually the first
   time (don't wait for the hourly schedule).
4. After a minute, refresh — you should see a green checkmark. Click into
   the run and check the log for a line like `Parsed X launches total`.
   If you see red ✕, click in to read the error (most likely cause: you
   left `ntfy_topic` as the placeholder value — go back to Step 3).

From now on, this runs automatically, every hour, forever — you don't
need to do anything.

### Step 5 — Install ntfy and subscribe to your topic
ntfy is a free, open-source push notification app with no account/signup.

- **iPhone**: install [ntfy from the App Store](https://apps.apple.com/us/app/ntfy/id1625396347)
- **Android**: install [ntfy from Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [F-Droid](https://f-droid.org/packages/io.heckel.ntfy/)
- **Desktop**: no app needed — just open `https://ntfy.sh/YOUR-TOPIC-NAME`
  in a browser and click "Enable notifications," or use the
  [desktop app](https://ntfy.sh) if you'd rather have one

In the app, tap **+ (Subscribe to topic)** and type in the *exact* topic
name you set in `config.json` (e.g. `dad-rocket-launches-58213`). That's
it — no server address to configure, no login. Do this on your dad's
phone too, using the same topic name, and he'll get the same
notifications.

**To test it right now:** trigger the workflow again from Step 4 —
if there's a launch within your configured lead times, you'll get a
notification within a minute or two.

### Step 6 — Turn on GitHub Pages for the dashboard
1. In your repo, go to **Settings → Pages**.
2. Under "Build and deployment," set **Source** to **Deploy from a
   branch**, branch **main**, folder **/docs**. Click **Save**.
3. After a minute, GitHub will show you a URL like
   `https://yourusername.github.io/launch-watch/`. Open it — that's your
   dashboard.

### Step 7 — Add the dashboard to your phone's home screen
- **iPhone (Safari)**: open the dashboard URL → tap the Share icon →
  **Add to Home Screen**.
- **Android (Chrome)**: open the dashboard URL → tap the ⋮ menu →
  **Add to Home Screen** / **Install app**.
- **Desktop (Chrome/Edge)**: open the dashboard URL → click the install
  icon (⊕) in the address bar.

Now it behaves like a normal app icon, opens full-screen, no browser
chrome.

---

## Changing your settings later
Just edit `config.json` in the GitHub website (pencil icon → edit →
commit) any time you want to change lead times, add/remove rocket
filters, or turn the "new launch added" / "time changed" alerts on or
off. The very next scheduled run (within an hour) picks up the change.
No re-installing anything, no coding.

## How it stays accurate for *all* future launches
Spaceflight Now updates their schedule page continuously as new launches
get added, delayed, or confirmed. Every hour, the GitHub Action re-reads
that page from scratch, so new launches show up automatically and delays
are detected (you'll get a "🔄 Launch time changed" notification, and
your lead-time reminders re-arm against the new time). There's no list of
launches to maintain — it's always reading the live page.

## If it ever stops working
The scraper looks for consistent text patterns ("Launch time:",
"Launch site:", the "•" separator, "(NNNN UTC)") rather than exact page
styling, so it should survive minor site tweaks. If Spaceflight Now does
a bigger redesign, the Action log will show `Parsed 0 launches` as a
warning. If you (or I, in a future chat) need to fix the scraper, only
`scraper.py` needs updating — nothing else changes.

## Files in this project
- `scraper.py` — reads and parses the Spaceflight Now schedule
- `notifier.py` — decides what notifications are due and sends them
- `config.json` — **your settings** (edit this one)
- `data/state.json` — internal memory of what's already been sent (don't edit)
- `docs/` — the installable dashboard webpage (GitHub Pages serves this folder)
- `.github/workflows/check-launches.yml` — the free hourly scheduler
