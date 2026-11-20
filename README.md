# Markets Learning Workbench

A local web app for learning what moves markets — stocks, gold, oil, Bitcoin and other crypto.
It streams live and near-live **real** prices, collects the latest **real** news, uses Google Gemini to
break the news down and explain why prices moved, and turns current events into lessons, a glossary
and quizzes with spaced repetition.

**Educational tool — not financial advice.** No buy/sell recommendations, no price targets.

Version 1.1 — all stages (0–7) built, plus the new design and article images. 94 automated tests.

---

## Design and images

- Modern **white and dark-blue** design; the top bar groups pages into **Monitor** (Dashboard, News, Briefing,
  Alerts) and **Learn** (Learn, Glossary, Quiz). The dashboard shows both: market monitoring on top, your
  learning progress, reviews due, next lesson and a "concept to know today" below.
- **Every news item and article has an image.** It is the publisher's own picture — taken from the news feed, or
  from the article page's preview image (only where `robots.txt` allows). Site logos and default pictures that
  many articles share are ignored. When an article has no picture of its own (or it fails to load), a topic
  illustration (gold, oil, crypto, stocks, rates, dollar, central bank…) is shown and **labelled "illustration"**,
  so it is never mistaken for a photo of the story. Images for older articles are filled in every 5 minutes,
  most important first, and immediately when you open an article.

## What you can do

| Page | What it does |
|---|---|
| **Dashboard** `/` | Live price board for 24 assets (price, change with ▲▼ arrows, day range, 1-day sparkline, status badge, source, update time), market open/closed chips, biggest movers with a "Why?" button, top news, upcoming economic events. |
| **Asset page** `/asset/BTC` | Big live price, real-candle chart (1D / 1W / 1M / 6M / 1Y), key numbers, the source chain, **"Why did it move today?"**, news about the asset, concepts to know, quick price alert. |
| **News** `/news` | Live news feed with filters (asset, class, source, time, has-breakdown) and search. New articles are announced while you read. |
| **Article** `/news/123` | Original link, metadata, **AI breakdown** (what happened, why it matters, affected assets with cause→effect chains, facts vs opinions, beginner explanation, concepts), **real price moves since publication**, "Quiz me on this". |
| **Learn** `/learn` | 5 learning paths (Gold, Oil, Crypto, Stocks, Macro) × 4 lessons. Each lesson shows today's real news and live prices as examples. |
| **Glossary** `/glossary` | ~65 concepts (more are added automatically from breakdowns). Each is explained once by AI, stored and editable. Shows how often you've seen it and your mastery. |
| **Quiz** `/quiz` | Modes: Today's news, Concepts & lessons, Review (spaced repetition), Weak topics. Score history and weakest topics. |
| **Briefing** `/briefing` | Daily at 07:00 Jakarta (or on demand): prices table, top 5 stories, 3-sentence "what to watch", calendar, 3 review questions. Past briefings are kept. |
| **Alerts** `/alerts` | Price above / below / move of at least X %. Browser notifications + on-page message. |
| **Demo invest** `/demo` | Practice account with **pretend money (from $1,000 up to $1,000,000,000)**. Buy and sell crypto, gold, silver, oil, gas, stocks and ETFs at the **real live price** (orders fill only while the price is LIVE or DELAYED; a closed market cannot fill). Account value, cash, positions with profit/loss, order history with the price source of every fill, and a value-over-time chart. No fees, spread or slippage (said on the page). Saved in your browser; export/import to move it. Not real money, not advice. |
| **Settings** `/settings` | Watchlist (on/off, reorder, add a US stock/ETF or crypto coin), AI models, daily AI limit, automatic breakdowns per hour, news interval, LAN access, unofficial sources, key status. |
| **Health** `/health` | Every data provider's status (with a switch to test fallback), news feeds, calendar, scheduled jobs, Gemini calls today vs limit, database size. |

---

## The honesty rules this app follows

- **Every number comes from a real source** and is stored in the database. The AI never creates numbers.
- **Number validator:** every AI answer is checked — each number must appear in the data given to the AI
  (article text, prices, % moves), allowing for rounding. If not, the AI is asked once more with a stricter
  prompt; if it still fails, the sentences with invented numbers are removed and the result is flagged.
  Each breakdown shows "Number check: ✔" or "⚠".
- **Status badges:** LIVE (streamed, ≤ 5 s old) · DELAYED · DAILY · MARKET CLOSED · STALE · ERROR · N/A
  (no free source). Delayed data is never called live.
- **Every news item links to the original article.** Breakdowns say whether they used the fetched article
  text (and how many characters) or only the headline and description.
- **"Why did it move?"** must cite stored articles for every driver; otherwise it says
  **"No clear news driver found."** With no tagged news at all, it does not call the AI.
- **Quizzes:** a second AI "checker" answers each question from the source text without the key; only
  questions where it agrees are kept.
- **No mock data** in the running app. Fake data exists only in `tests/`.
- Times are shown in **Jakarta time (UTC+7) on a 12-hour clock** (e.g. 7:36 PM); hover any time to see UTC.

### Status badges

| Badge | Meaning |
|---|---|
| **LIVE** | Streamed, last update at most 5 seconds old. |
| **DELAYED · 15 s** | Checked on a timer, or a stream that had no new trade for a few seconds. The age keeps counting. |
| **DAILY · 18 Nov 2026** | Official data, one value per business day (FRED, US Treasury). |
| **MARKET CLOSED** | Weekend, holiday or outside hours. Hover to see when it closed and when it opens. |
| **STALE · 12 min** | The market should be open but the data is older than expected. |
| **ERROR** | The source is failing and the data is too old (hover for the error). |
| **N/A** | No free source exists ("Not available on free sources"). |

Chips: **fallback** (a backup source is in use), **unofficial** (Yahoo), **proxy** (a stand-in instrument,
e.g. the PAX Gold token for gold's chart).

---

## Setup (Windows, PowerShell)

On this PC everything is already set up. For a fresh setup on another PC, do these steps one at a time.

**Where to type:** in a **PowerShell terminal**. Tip: in File Explorer open the project folder, click the
address bar, type `powershell`, press Enter.

1. Check Python (3.11 or newer; tested with 3.14.2):
   ```powershell
   python --version
   ```
2. Go to the project folder:
   ```powershell
   cd D:\AI\markets-learning-workbench
   ```
3. Create the virtual environment (a private package folder called `.venv`):
   ```powershell
   python -m venv .venv
   ```
4. Install the packages:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
5. Add your keys: open `.env` in Notepad, paste each key after its `=`, save. Never paste keys into a chat.
   ```powershell
   notepad D:\AI\markets-learning-workbench\.env
   ```

## Run

From any folder:

```powershell
python D:\AI\markets-learning-workbench\run.py --open
```

Or double-click **`start.bat`** in the project folder.

On this PC the app runs at **http://127.0.0.1:8001** (`PORT=8001` in `.env`, because your AI Research
Workbench uses 8000). Stop it with **Ctrl+C** in its terminal.

**Phone on the same Wi-Fi:** tick "LAN access" on the Settings page (or set `LAN_ACCESS=true` in `.env`),
restart, and open the address the start window shows. If Windows asks about the firewall, allow
"Private networks" only.

---

## Website (phone and computer, anywhere)

**https://mujtabaahmadi548-007.github.io/markets-learning-workbench/**

GitHub builds the website from this repository with the same code and the same honesty rules
(`build_site.py` run by `.github/workflows/site.yml`):

- A build collects real prices, news, images and (with a Gemini key) AI breakdowns, then publishes the
  pages. Each build starts the next one about 5 minutes after it ends, so the site is **rebuilt about every
  10 minutes**; an hourly schedule restarts the chain if it ever stops. The database is carried from build to
  build in the GitHub Actions cache (not in git).
- **Crypto prices are live in your browser** (Binance stream). Stocks, metals, energy and macro show the
  build's real quote with its real age and a DELAYED/STALE/MARKET CLOSED badge.
- Your **learning progress and alerts are kept in your browser** (each device separately). Settings →
  "Export progress" / "Import progress" copies them between devices.
- Buttons that need the server (Fetch news now, make a breakdown, Why did it move?, Explain this term, make
  quiz questions) only appear in the app on your PC. On the website these things happen automatically
  in the builds: one breakdown about every 15 minutes, "why" for the top 3 movers, the 07:00 briefing,
  new quiz questions.

### API keys for the website (GitHub secrets)

Keys are **never** in the repository. Add them on GitHub: repository **Settings → Secrets and variables →
Actions → New repository secret**:

| Secret name | Needed for |
|---|---|
| `GEMINI_API_KEY` | All AI on the website (without it: no breakdowns, briefing or new questions) |
| `FINNHUB_API_KEY` | US stock quotes and company news. The website uses Finnhub's REST quotes only, never its live stream (the free plan allows one stream per key, and the app on your PC keeps it) |
| `TWELVE_DATA_API_KEY` | optional — official stock charts |
| `COINGECKO_API_KEY` | optional — steadier crypto backup |

Optional repository **variables** (same page, "Variables" tab): `SITE_GEMINI_DAILY_LIMIT` (default 150 —
the website and the app on your PC share the same Gemini free quota), `SITE_AI_TASKS_PER_RUN` (default 2),
`GEMINI_MODEL_FAST`, `GEMINI_MODEL_SMART`.

### Build the website on your PC (for testing)

```powershell
$env:MLW_NO_DOTENV = "1"; $env:DATABASE_PATH = "$env:TEMP\mlw-site\markets.db"; python build_site.py --out "$env:TEMP\mlw-site\_site" --base /markets-learning-workbench --no-ai
```

`MLW_NO_DOTENV=1` keeps the test build from reading your `.env` (so it does not open a second Finnhub
stream next to the running app).

---

## API keys (all free plans)

| `.env` line | Status here | Used for | How to get it |
|---|---|---|---|
| `GEMINI_API_KEY=` | ✔ set | All AI features | https://aistudio.google.com/apikey → Create API key |
| `FINNHUB_API_KEY=` | ✔ set | Live US stocks, live gold/silver/oil (OANDA), company news | https://finnhub.io/register |
| `TWELVE_DATA_API_KEY=` | optional | Official stock charts (otherwise Yahoo, unofficial) | https://twelvedata.com/pricing → Basic (free) |
| `ALPHA_VANTAGE_API_KEY=` | optional | Only used by `scripts\test_providers.py` | https://www.alphavantage.co/support/#api-key |
| `COINGECKO_API_KEY=` | optional | Steadier last-resort crypto backup | CoinGecko developer dashboard → Demo key |

Other `.env` settings: `GEMINI_MODEL_FAST`, `GEMINI_MODEL_SMART`, `GEMINI_DAILY_CALL_LIMIT` (default 300),
`PORT`, `LAN_ACCESS`, `ALLOW_UNOFFICIAL_SOURCES`. Models, the AI limit, automatic breakdowns and the news
interval can also be changed on the Settings page.

### How much AI does it use?

- Automatic: news tagging (1 call per 12 unclear articles, every 20 min), automatic breakdowns (default
  3 per hour), the 07:00 briefing (up to ~5 calls). **Automatic jobs stop at 70 % of the daily limit**,
  keeping the rest for what you click.
- On click: breakdown 1–2 calls, "Why did it move?" 1–2, glossary explanation 1–2, quiz 2–3.
- If the smart model is overloaded (Google answers 503/504), the request is finished with the fast model;
  the model actually used is shown with every result.
- Gemini free quotas are per Google project and reset at midnight Pacific time (15:00 WIB in winter).

---

## Data sources

Checked on 20 Nov 2026 by live tests from this PC and the providers' documentation.

**Your internet provider blocks most crypto exchanges** (MyRepublic, following Indonesia's TrustPositif list):
Binance's main site and stream, Coinbase, Kraken, OKX, Bybit, Bitget, KuCoin, Bitstamp, Bitfinex, Crypto.com,
MEXC, Swissquote. The app does not get around the block; it uses sources that work from your connection.

| Asset | Price sources (in order; next takes over automatically) | Live or delayed | Chart source |
|---|---|---|---|
| BTC, ETH, SOL, BNB, XRP | Binance market-data stream → Gate.io stream → CoinGecko | **LIVE**, in **USDT**; change over 24 h | Binance |
| US stocks, SPY, QQQ | Finnhub trade stream + quote → Twelve Data → Yahoo (unofficial) | **LIVE** in US hours (21:30–04:00 WIB in winter) | Twelve Data with key, else Yahoo |
| Gold, silver | Finnhub OANDA stream → gold-api.com (every 15 s) | **LIVE** | PAX Gold token (**proxy**); silver: COMEX futures via Yahoo (**proxy**) |
| WTI, Brent, natural gas | Finnhub OANDA stream → Yahoo futures (~10 min delayed) → EIA spot via FRED (daily) | **LIVE** when OANDA ticks | Yahoo futures, else EIA daily |
| S&P 500, Nasdaq index | FRED daily (official) → Yahoo | Daily | FRED |
| US Dollar Index (DXY) | Yahoo only — **no free official source** | ~10 min delayed | Yahoo |
| US 10-year yield | US Treasury (official, end of day) → Yahoo `^TNX` | Daily | US Treasury |
| Fed Broad Dollar Index | FRED (official) | Daily values, published weekly | FRED |

**News:** 33 RSS feeds (Cointelegraph, The Block, Decrypt, CoinDesk, OilPrice, EIA, Bloomberg, WSJ,
MarketWatch, CNBC, Investing.com, Seeking Alpha, Benzinga, Guardian, BBC, Federal Reserve, ECB, Yahoo Finance
per stock), 19 Google News searches (incl. Reuters and Kitco, which have no RSS), and Finnhub company news.
Google News links are converted to the publisher's own link when possible.

**Calendar:** BLS release calendar (CPI, jobs report, PPI, JOLTS, ECI) and the Fed's FOMC calendar (official);
ForexFactory (unofficial) as an extra. **No free source for OPEC meeting dates was found.**

## Known limits (honest list)

- **Gold, silver and oil show no day change ("—") for the first 24 hours** the app runs: their live sources
  give no previous close. After 24 h the app shows the change versus the price it recorded itself.
- **Oil: two instruments.** Live price = OANDA spot CFD; backup and chart = NYMEX/ICE futures. Prices differ
  slightly; the source is always named. A quiet OANDA stream is skipped for 5+ minutes (no flip-flopping).
- **Gold's chart is a proxy** (PAX Gold token); silver's chart is futures.
- **Stock charts come from Yahoo (unofficial)** until you add `TWELVE_DATA_API_KEY`.
- **After-hours trades are ignored**; a closed stock shows the official 16:00 New York close.
- **Article text:** fetched only where `robots.txt` allows; Bloomberg, WSJ and MarketWatch block it, so their
  breakdowns are "headline and description only". Some sites return only part of the text (shown in characters).
- **Google News link decoding is unofficial** and may stop working; then the Google link (which still opens
  the article) is shown.
- **Market hours:** NYSE holidays and early closes handled; futures treated as closed on NYSE holidays (approximation).
- **Finnhub allows one stream connection per key:** stop the app before running `scripts\test_providers.py`.
- **The app must be running** for alerts, news collection and the 07:00 briefing (a briefing missed while the
  PC was off can be made with the button).
- Yahoo is unofficial; personal use only. Switch it off on the Settings page (then DXY shows "N/A").

---

## Scripts

| Command | What it does |
|---|---|
| `python scripts\test_gemini.py` | Tests the Gemini connection (3 tiny calls). |
| `python scripts\list_gemini_models.py --probe --write-env` | Lists your models, tests them, saves working fast/smart models to `.env`. |
| `python scripts\test_providers.py` | OK/FAIL table for every data source (stop the app first). |
| `python scripts\backfill_candles.py` | Fills all charts at once (optional). |
| `python scripts\capture_fixtures.py` | Re-records the real sample responses used by tests (developer tool). |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests never read `.env`, never use your keys, never touch your database, and make no network calls.
Gemini is tested against a local fake server (`tests/fake_gemini.py`). Covered: provider parsing (recorded real
responses), fallback, market hours, status badges, number validator, news de-duplication and story grouping,
tagging, breakdown / "why" / quiz flows incl. the checker, spaced repetition, alerts, calendar parsing,
every page rendering.

---

## Database

`data\markets.db` (SQLite, ignored by git). Schema changes use numbered migrations (`app\db\migrations.py`,
now v4); before an upgrade a copy is saved in `data\backups\`, so your progress is never lost.
Changes from the original table list: `lessons.sort_order` (ORDER is reserved); extra honesty fields on prices
(`delay_seconds`, `quote_currency`, `is_proxy`, `is_unofficial`, `error_message`, `prev_close`, `change_basis`);
`assets.provider_symbols` holds the price and chart source chains; articles have `story_cluster_id`,
`original_url`, `ai_tagged`, `body_attempted`, `body_error`, `is_official`; quiz questions have
`option_explanations` and `checker_passed`; extra tables `calendar_events`, `app_settings`, `job_runs`,
`schema_version`. Old data is thinned out daily at 03:30 WIB (minute prices kept 8 days, then hourly).

## Project layout

```
app/
  api/         routes: pages, market data + live WebSocket, news, learning, briefing/alerts/settings, health
  providers/   one file per data source (binance, gateio, coingecko, goldapi, finnhub, twelvedata, yahoo, fred, treasury, rss)
  services/    price engine (feeds), candles, market hours, status badges, news, tagger, breakdowns, explainer,
               learning (+ lessons content, spaced repetition), calendar, briefing, alerts, settings, scheduler
  ai/          Gemini client, shared prompt, output schemas, number validator
  models/ db/  SQLAlchemy models, engine, migrations
templates/ static/   pages, CSS, JS, TradingView Lightweight Charts 4.2.3 (stored locally)
tests/       95 tests + fake Gemini server + recorded real sample responses
scripts/     helper scripts (see above)
```

## Troubleshooting

- **"Port … is already in use"**: another program uses it (often your AI Research Workbench). Close it or change `PORT=` in `.env`.
- **"can't open file … run.py"**: use the full path: `python D:\AI\markets-learning-workbench\run.py --open`.
- **Certificate / SSL errors**: this PC inspects HTTPS traffic; the app uses the Windows certificate store (`truststore`). If it still fails, note the exact error.
- **A crypto source suddenly fails**: your provider may have blocked it; the app falls back automatically. Check `/health`.
- **AI says "rate limit or daily quota reached"**: the free Gemini quota is used up until midnight Pacific time (15:00 WIB). Lower the automatic breakdowns on the Settings page.
- **AI answers are slow**: the smart model is busy; the app switches to the fast model after a failed attempt. You can choose another model on the Settings page.
- **A page looks old after an update**: press Ctrl+F5.
