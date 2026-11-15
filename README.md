# Claude Code spend dashboard

A local dashboard of what your Claude Code usage costs, per day and per project,
read live from the session logs in `~/.claude/projects`.

```bash
python3 server.py --open      # http://127.0.0.1:8787
```

To use it as a Mac app, run `./install-app.sh` once (needs the Xcode command line tools).
It builds `~/Applications/CC Insights.app`, a native window around the dashboard: opening
it starts the server, quitting it (Cmd+Q or closing the window) stops the server.
If the server is already running, the app uses it and leaves it running.

No dependencies beyond Python 3.10+. The page re-reads the logs every minute (and on
Refresh / window focus); unchanged log files are cached, so a refresh takes ~50 ms.

## What it shows

- **Spend for the period** (Day / Week / Month / Year / All; Day is the default, opening on today) with change vs the previous period,
  today, average per active day, a projected month total and the cache hit rate.
- **Daily spend by project** — stacked columns with a 7-day average, plus a table view.
  The Day view shows spend per hour instead; today is compared with yesterday up to the same hour.
  Click a day or month column to open that day or month.
- **Projects** — spend, share, sessions, tokens, last activity and a 30-day trend.
  Click a project (or its legend entry) to focus the whole dashboard on it.
- **Where the money goes** — split by model and by token type (output, cache writes,
  cache reads, uncached input), with cost per session / call / million tokens.
- **When you spend** — weekday × hour heatmap.
- **Most expensive sessions** — by title, with project, duration and models.

## How costs are computed

`cc_usage.py` reads the `usage` block of every assistant message, de-duplicates the
streamed copies of each API response, and prices it with the table in `PRICES`
(Anthropic list prices; cache writes at 1.25× input for the 5-minute TTL and 2× for
the 1-hour TTL; fast mode at 2×). These match the `totalCostUSD` Claude Code records
itself. Update `PRICES` when new models ship; unknown models fall back to their tier
and are flagged in the footer.

A project is the folder Claude Code was started in. Worktrees under
`.claude/worktrees/` count toward their parent repository.

On a Pro/Max subscription the numbers are the API-equivalent value of your usage,
not what you are billed.

## Renaming and grouping projects

Click **Rename & group** on the Projects card. Give a folder a new name to rename it;
give several folders the same name to combine them into one project. Click the color
button next to a name to pin that project's color, or leave it on Auto. Names and colors
are saved in `config.json` next to `server.py`.

## Project colors

Each period you view colors its eight biggest projects; the rest are grouped as gray
"Other projects". So a project that mattered last spring is still in color when you step
back to last spring. Projects keep the same color wherever they can: a color you pinned
comes first, then the color the project was given when it first appeared (remembered in
the browser). Only when two projects in the same period want the same color does one of
them borrow a free one for that period.

## Days, weeks, months and years

Pick **Day**, **Week** (Monday to Sunday), **Month** or **Year** in the range control and step
through periods with ‹ ›.
The year view charts spend per month. The **Months & years** table lists every month
and year with change vs the previous period; click a row to jump to it.
