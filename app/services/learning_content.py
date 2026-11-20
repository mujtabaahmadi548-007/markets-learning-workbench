"""Learning paths (lessons) and the starter glossary.

Lessons explain HOW things work (mechanisms), so they contain no market numbers. Each lesson page adds
live examples from real stored news and the current real prices of the related assets.
Glossary entries start with a name and category; the full explanation is generated once (Gemini) when
you open the entry, then stored and editable.
Formatting: blank line = new paragraph, "- " = bullet, **bold**.
"""
from __future__ import annotations

PATHS = [
    ("gold", "Gold basics", "What moves gold, and how to read gold headlines."),
    ("oil", "Oil basics", "Benchmarks, supply and demand, and the futures curve."),
    ("crypto", "Crypto basics", "Why crypto trades differently, and what moves it."),
    ("stocks", "Stocks basics", "How share prices react to earnings, indexes and rates."),
    ("macro", "Macro drivers", "The Fed, inflation, jobs, the dollar and yields - the forces behind everything else."),
]

# (path, key, title, symbols for live examples, concept slugs, content)
LESSONS: list[tuple[str, str, str, list[str], list[str], str]] = [
    # ------------------------------------------------------------------ GOLD
    ("gold", "gold-1", "What sets the gold price", ["XAU"], ["spot-price", "futures-contract", "troy-ounce", "proxy-instrument"], """
Gold trades almost around the clock on weekdays, in many places at once: London's over-the-counter market, futures exchanges such as COMEX in New York, and dealers in Asia. The headline "gold price" usually means the **spot price**: the price for buying gold now, in US dollars per **troy ounce**.

There is more than one "gold price":
- **Spot gold** - the price for immediate delivery. This app shows it from OANDA (a broker quote) or gold-api.com.
- **Gold futures** - contracts to buy gold on a future date. Their price is usually a little above spot because of storage and interest costs.
- **Gold tokens** such as PAX Gold - crypto tokens backed by gold. They track gold closely but are not the same thing (this app labels them PROXY).

When you read a headline, check which one it means. A move in futures overnight and a move in spot can differ slightly. This app always names the instrument behind every number.
"""),
    ("gold", "gold-2", "Gold, the dollar and real yields", ["XAU", "DXY", "US10Y"], ["us-dollar-index", "real-yield", "opportunity-cost", "bond-yield"], """
Gold pays no interest. That single fact explains much of its behaviour.

**Opportunity cost:** when savers can earn more from safe bonds, holding gold (which earns nothing) costs more in lost interest. So when bond yields - especially **real yields** (yield minus expected inflation) - rise, gold often comes under pressure. When real yields fall, gold tends to look more attractive.

**The dollar:** gold is priced in US dollars. When the dollar strengthens, gold becomes more expensive for buyers paying in euros, yen or rupiah, which can reduce demand. That is why a strong US Dollar Index often goes with a weaker gold price, and the reverse.

A typical chain you will see in news:
- Strong US data -> markets expect higher interest rates -> yields rise and the dollar strengthens -> pressure on gold.

These are tendencies, not laws. On some days gold and the dollar rise together, for example when investors are nervous and buy both as safe places to park money.
"""),
    ("gold", "gold-3", "Safe havens and central-bank buying", ["XAU", "XAG"], ["safe-haven", "risk-on-risk-off", "central-bank-gold-buying", "etf-flows"], """
Gold is often called a **safe haven**: something investors buy when they are worried about wars, financial crises or recessions. In "risk-off" moments, money can flow into gold.

But be careful: a scary headline does not automatically mean "gold up". Sometimes investors sell gold to raise cash in a panic, or a crisis pushes the dollar up, which weighs on gold. Good analysis names the channel, not just the event.

Other big buyers matter too:
- **Central banks** buy gold as part of their reserves. Reports of strong central-bank buying support demand over long periods.
- **Gold ETFs** let investors hold gold through the stock market. Inflows (money coming in) add demand; outflows remove it.
- **Jewellery and industry** demand, especially from India and China, matters more for long-run trends than for daily moves.

**Silver** behaves partly like gold (a precious metal) and partly like an industrial metal, so it is usually more volatile.
"""),
    ("gold", "gold-4", "Reading a gold headline step by step", ["XAU"], ["spot-price", "previous-close", "correlation-vs-causation"], """
Use this checklist whenever you see a gold story:

- **Which price?** Spot, futures or a token? Which time zone and session?
- **Compared to what?** "Gold up" since yesterday's close, over 24 hours, or over a week?
- **What channel?** Dollar, yields, safe-haven flows, central banks, or physical demand?
- **Fact or opinion?** "Gold rose" is a fact. "Gold could rally further" is an opinion. This app's breakdowns separate the two.
- **Timing:** did the move start before or after the news? A move that began earlier was probably caused by something else.

Then open the gold page here: compare the live price, the chart and "Why did it move today?". The explainer only cites stored articles and says "No clear news driver found" when the news does not explain the move - which happens more often than headlines suggest.
"""),
    # ------------------------------------------------------------------ OIL
    ("oil", "oil-1", "WTI and Brent: the two oil benchmarks", ["WTI", "BRENT"], ["wti", "brent", "benchmark", "spread"], """
Crude oil is not one price. Traders use **benchmarks** - standard grades whose price others are measured against.

- **WTI (West Texas Intermediate)** is a light, sweet US crude delivered inland at Cushing, Oklahoma. Its main futures trade on NYMEX.
- **Brent** is a blend of North Sea crudes. It is loaded onto ships, so it reflects the seaborne world market. Its futures trade on ICE.

Brent usually trades above WTI. The gap between them (the **spread**) changes with shipping costs, US export capacity and regional supply problems.

In this app the live oil price comes from OANDA's spot quotes, the backup and the chart from front-month futures, and the official reference from the US Energy Information Administration (EIA) daily spot prices, which arrive about a business day late. The source line always tells you which one you are looking at.
"""),
    ("oil", "oil-2", "Supply: OPEC+, shale and disruptions", ["WTI", "BRENT"], ["opec-plus", "supply-and-demand", "sanctions", "geopolitical-risk"], """
Oil prices move when the expected balance between supply and demand changes.

Main supply forces:
- **OPEC+** - OPEC members plus allies such as Russia. When they agree to cut output, prices tend to rise; when they add barrels, prices tend to fall. Markets often move on *expectations* before the meeting.
- **US shale** producers respond to prices over months: higher prices encourage more drilling.
- **Disruptions** - conflicts, attacks on tankers or pipelines, hurricanes, outages - remove supply suddenly.
- **Sanctions** can restrict exports from a producer, or reroute them through longer routes.

Always ask: does this news change the barrels actually available, or only the risk that they might not be? Risk alone can lift prices briefly; real lost supply moves them for longer.
"""),
    ("oil", "oil-3", "Demand and inventories", ["WTI", "BRENT", "NATGAS"], ["inventories", "economic-growth", "supply-and-demand"], """
Demand for oil follows the economy: factories, trucks, planes and petrochemicals. News about growth in the US, China and Europe therefore moves oil.

**Inventories** are the scoreboard. The EIA publishes weekly US crude and fuel stock levels:
- A **draw** (stocks fall) suggests demand is beating supply - often supportive for prices.
- A **build** (stocks rise) suggests the opposite.
What matters is the surprise versus what analysts expected, not the raw number.

**Natural gas** has its own drivers: weather (heating and cooling demand), storage levels before winter, and LNG exports. It is far more volatile than oil and sometimes moves for completely different reasons.
"""),
    ("oil", "oil-4", "Futures, contango and backwardation", ["WTI", "BRENT"], ["futures-contract", "contango", "backwardation", "front-month"], """
Most oil trading happens in **futures contracts** - agreements to buy or sell oil for delivery in a given month. The nearest one is the **front-month** contract, and that is what most "oil price" headlines quote.

Prices for successive months form a **curve**:
- **Backwardation** - near months cost more than later months. It signals a tight market: buyers want oil now.
- **Contango** - later months cost more than near months. It signals plenty of supply: it pays to store oil and sell it later.

When a contract expires, data providers switch to the next month ("roll"). Charts of continuous futures can show a jump on that day which is not a real market move. This app notes when a chart is built from futures so you can keep this in mind.
"""),
    # ------------------------------------------------------------------ CRYPTO
    ("crypto", "crypto-1", "How crypto prices are made", ["BTC", "ETH", "SOL"], ["market-liquidity", "exchange", "volatility", "order-book"], """
Crypto trades every hour of every day on many exchanges at once. There is no single official price: each exchange has its own **order book** where buyers and sellers meet. Arbitrage traders keep the prices close together.

What this means for you:
- **24/7 markets:** there is no "close", so this app measures crypto moves over the last 24 hours.
- **Thin hours:** weekends and quiet hours have less **liquidity**, so big orders can move prices more.
- **Volatility:** crypto routinely moves more in a day than large stocks move in a week.

In Indonesia many exchanges are blocked by internet providers, so this app reads Binance's public market-data feed (still reachable) and Gate.io as a backup.
"""),
    ("crypto", "crypto-2", "Stablecoins and USDT", ["BTC", "ETH"], ["stablecoin", "usdt", "quote-currency"], """
A **stablecoin** is a crypto token designed to hold a steady value, usually one US dollar. **USDT (Tether)** is the largest.

Most crypto trading pairs are quoted in USDT, not in actual dollars: "BTC/USDT" means the price of one bitcoin in USDT. Normally USDT stays very close to one dollar, so the difference is tiny - but it is not guaranteed, and in stressful moments a stablecoin can drift from its peg.

That is why this app shows "USDT" next to Binance prices and "USD" next to CoinGecko prices. Small differences between them are normal and are not a market move.

Stablecoins also matter as a sign of money entering crypto: growing stablecoin supply is often read as more cash waiting on the sidelines.
"""),
    ("crypto", "crypto-3", "Spot Bitcoin ETFs and fund flows", ["BTC"], ["spot-bitcoin-etf", "etf-flows", "institutional-investors"], """
A **spot Bitcoin ETF** is a fund, traded on the stock market, that holds actual bitcoin. It lets pension funds, advisers and ordinary investors get bitcoin exposure through a normal brokerage account.

News often reports **ETF flows**:
- **Inflows** - new money buying fund shares, so the fund buys bitcoin. Supportive for demand.
- **Outflows** - investors redeeming, so the fund sells bitcoin. A source of selling pressure.

Flows are reported after the US trading day, so the news often arrives after the price has already moved. Use the timing check: did bitcoin move before or after the flow report?

Ether ETFs work the same way for Ethereum.
"""),
    ("crypto", "crypto-4", "Crypto, risk appetite and regulation", ["BTC", "ETH", "QQQ"], ["risk-on-risk-off", "correlation-vs-causation", "regulation", "interest-rates"], """
Crypto behaves largely like a **risk asset**. When investors feel confident ("risk-on"), they buy growth stocks and crypto; when they get nervous ("risk-off"), both often fall together. That is why bitcoin sometimes moves with the Nasdaq.

Interest rates matter through the same channel: easier money and lower rates tend to support speculative assets; tighter money tends to hurt them.

Crypto also has its own news drivers:
- **Regulation** - approvals, lawsuits, new laws, bans.
- **Hacks and failures** of exchanges or protocols.
- **Network events** such as upgrades, or bitcoin's halving (a scheduled cut in new supply).

Remember: two things moving together is **correlation**, not proof that one causes the other.
"""),
    # ------------------------------------------------------------------ STOCKS
    ("stocks", "stocks-1", "How a share price is set", ["AAPL", "SPY"], ["market-hours", "previous-close", "after-hours-trading", "bid-ask-spread"], """
A share price is simply the last price at which a buyer and a seller agreed to trade. Millions of orders meet on exchanges like the Nasdaq and the NYSE.

**Market hours:** US stocks trade regularly from 09:30 to 16:00 New York time - that is late evening to early morning in Jakarta. There is also pre-market and after-hours trading with fewer participants, which this app does not count as "open".

**Previous close:** the official last price of the previous regular session. "AAPL down 1%" usually means compared with that close.

**Bid and ask:** the best price someone will pay (bid) and the best price someone will sell (ask). The gap is the **spread**; it is tiny for big companies and wider for small ones.
"""),
    ("stocks", "stocks-2", "Earnings, guidance and expectations", ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"], ["earnings", "eps", "guidance", "consensus-expectations"], """
Every quarter, listed companies report **earnings**: revenue, profit and **earnings per share (EPS)**.

The key idea: prices react to the **surprise**, not the raw result. Analysts publish forecasts; their average is the **consensus**. A company that grows strongly but less than expected can still fall.

**Guidance** - what the company says about the coming quarters - often moves the stock more than the past quarter's numbers, because markets look forward.

When you read an earnings story, look for three things: results versus expectations, the guidance, and what management said about demand. This app's breakdowns separate reported facts from analysts' opinions.
"""),
    ("stocks", "stocks-3", "Indexes and ETFs", ["SPY", "QQQ", "SPX", "IXIC"], ["stock-index", "etf", "market-cap-weighting", "sp-500"], """
A **stock index** tracks a basket of shares to show how "the market" is doing:
- **S&P 500** - large US companies across all sectors.
- **Nasdaq Composite** - everything listed on the Nasdaq, heavy in technology.
- **Nasdaq-100** - the largest non-financial Nasdaq companies.

You cannot buy an index directly, but you can buy an **ETF** that tracks it: **SPY** follows the S&P 500, **QQQ** follows the Nasdaq-100.

Most big indexes are **market-cap weighted**: bigger companies count more. So a large move in a few giants such as NVIDIA, Apple or Microsoft can move the whole index.

In this app the index values come from official daily data (FRED), while SPY and QQQ trade live - a useful way to follow the market during the day.
"""),
    ("stocks", "stocks-4", "Why interest rates move stock prices", ["QQQ", "NVDA", "US10Y"], ["interest-rates", "valuation", "discount-rate", "growth-vs-value"], """
A share is a claim on a company's future profits. To value future money today, investors **discount** it using interest rates.

- When rates and bond yields rise, future profits are worth less today, so valuations tend to fall.
- Companies whose profits lie far in the future - many **growth** and tech stocks - are hit hardest.
- Higher yields also make bonds a more attractive alternative to stocks.

That is why a jump in the 10-year Treasury yield can push the Nasdaq down even on a day with no company news. When you use "Why did it move today?", check the yield line in the data the explainer shows.
"""),
    # ------------------------------------------------------------------ MACRO
    ("macro", "macro-1", "The Fed and interest rates", ["US10Y", "DXY", "SPX"], ["federal-reserve", "fomc", "federal-funds-rate", "rate-expectations"], """
The **Federal Reserve (Fed)** is the US central bank. Its **FOMC** committee meets eight times a year to set the **federal funds rate** - the base interest rate that ripples through mortgages, business loans, bond yields and the dollar.

- **Rate hikes** cool the economy and fight inflation. Usually: yields up, dollar up, pressure on stocks, gold and crypto.
- **Rate cuts** support the economy. Usually the reverse.

Markets trade **expectations**. If a cut is fully expected, the decision itself may barely move prices; the statement, the projections and the chair's press conference about *future* moves matter more. Words like "hawkish" (leaning to tighter policy) and "dovish" (leaning to easier policy) describe that tone.

The Briefing page lists upcoming FOMC meetings from the Fed's official calendar.
"""),
    ("macro", "macro-2", "Inflation data: CPI and PCE", ["US10Y", "DXY", "XAU"], ["inflation", "cpi", "pce", "core-inflation"], """
**Inflation** is the rate at which prices rise. The Fed aims to keep it low and stable, so inflation reports can move every market.

- **CPI (Consumer Price Index)** - published monthly by the Bureau of Labor Statistics. The most watched release.
- **PCE** - the Fed's preferred measure, published by the Bureau of Economic Analysis.
- **Core** versions remove food and energy, which jump around.

The market reacts to the **surprise**: a "hot" (higher than expected) reading raises the chance of higher rates -> yields and the dollar tend to rise; a "cool" reading does the opposite.

The Briefing page shows upcoming CPI release dates from the official BLS calendar.
"""),
    ("macro", "macro-3", "Jobs data and the economy", ["US10Y", "DXY", "SPX"], ["nonfarm-payrolls", "unemployment-rate", "recession"], """
The monthly US **Employment Situation** report (often called the jobs report) includes **nonfarm payrolls** - how many jobs were added - and the **unemployment rate**.

- A strong labour market can mean higher wages and inflation pressure -> markets expect tighter Fed policy.
- A weak one raises recession worries -> markets expect rate cuts, and safe havens may gain.

Again, the surprise versus expectations matters most, and revisions to earlier months can change the story.

Good and bad news can be confusing: sometimes "bad" economic news lifts stocks because it makes rate cuts more likely. Always trace the chain through rates.
"""),
    ("macro", "macro-4", "The dollar and Treasury yields: the hub", ["DXY", "US10Y", "XAU", "WTI", "BTC"], ["us-dollar-index", "bond-yield", "treasury-securities", "risk-on-risk-off"], """
Two prices connect almost every market: the **US dollar** and **US Treasury yields**.

- **Treasury yields** are the interest the US government pays on its bonds. The 10-year yield is the benchmark for long-term borrowing costs worldwide. Bond prices and yields move in opposite directions.
- The **US Dollar Index (DXY)** measures the dollar against a basket of major currencies.

Typical chains:
- Yields up -> dollar up -> pressure on gold, oil and emerging markets.
- Yields up -> future profits discounted more -> pressure on tech stocks and crypto.
- Fear -> investors buy Treasuries and dollars -> yields can fall while the dollar rises.

When an asset moves and its own news is quiet, check these two first. In this app, the official 10-year yield comes from the US Treasury (end of day); the DXY comes only from an unofficial source, because no free official source publishes it.
"""),
]

# Starter glossary: (slug, name, category). Explanations are generated once, when first opened.
SEED_CONCEPTS: list[tuple[str, str, str]] = [
    ("spot-price", "Spot price", "trading"), ("futures-contract", "Futures contract", "trading"),
    ("front-month", "Front-month contract", "trading"), ("contango", "Contango", "commodities"),
    ("backwardation", "Backwardation", "commodities"), ("troy-ounce", "Troy ounce", "metals"),
    ("proxy-instrument", "Proxy instrument", "trading"), ("previous-close", "Previous close", "trading"),
    ("market-hours", "Market hours", "stocks"), ("after-hours-trading", "After-hours trading", "stocks"),
    ("bid-ask-spread", "Bid-ask spread", "trading"), ("order-book", "Order book", "trading"),
    ("market-liquidity", "Market liquidity", "trading"), ("volatility", "Volatility", "trading"),
    ("exchange", "Exchange", "trading"), ("quote-currency", "Quote currency", "currency"),
    ("us-dollar-index", "US Dollar Index (DXY)", "currency"), ("real-yield", "Real yield", "rates"),
    ("bond-yield", "Bond yield", "rates"), ("treasury-securities", "Treasury securities", "rates"),
    ("opportunity-cost", "Opportunity cost", "general"), ("safe-haven", "Safe haven", "general"),
    ("risk-on-risk-off", "Risk-on / risk-off", "general"), ("central-bank-gold-buying", "Central-bank gold buying", "metals"),
    ("etf-flows", "ETF flows", "stocks"), ("etf", "ETF (exchange-traded fund)", "stocks"),
    ("wti", "WTI crude", "energy"), ("brent", "Brent crude", "energy"), ("benchmark", "Benchmark price", "commodities"),
    ("spread", "Price spread", "trading"), ("opec-plus", "OPEC+", "energy"), ("supply-and-demand", "Supply and demand", "general"),
    ("sanctions", "Sanctions", "macro"), ("geopolitical-risk", "Geopolitical risk", "macro"),
    ("inventories", "Inventories (stockpiles)", "energy"), ("economic-growth", "Economic growth", "macro"),
    ("stablecoin", "Stablecoin", "crypto"), ("usdt", "USDT (Tether)", "crypto"),
    ("spot-bitcoin-etf", "Spot Bitcoin ETF", "crypto"), ("institutional-investors", "Institutional investors", "general"),
    ("regulation", "Regulation", "general"), ("interest-rates", "Interest rates", "rates"),
    ("correlation-vs-causation", "Correlation vs causation", "general"), ("earnings", "Earnings", "stocks"),
    ("eps", "Earnings per share (EPS)", "stocks"), ("guidance", "Guidance", "stocks"),
    ("consensus-expectations", "Consensus expectations", "stocks"), ("stock-index", "Stock index", "stocks"),
    ("market-cap-weighting", "Market-cap weighting", "stocks"), ("sp-500", "S&P 500", "stocks"),
    ("valuation", "Valuation", "stocks"), ("discount-rate", "Discount rate", "rates"),
    ("growth-vs-value", "Growth vs value stocks", "stocks"), ("federal-reserve", "Federal Reserve (Fed)", "macro"),
    ("fomc", "FOMC", "macro"), ("federal-funds-rate", "Federal funds rate", "rates"),
    ("rate-expectations", "Rate expectations", "rates"), ("inflation", "Inflation", "macro"), ("cpi", "CPI", "macro"),
    ("pce", "PCE inflation", "macro"), ("core-inflation", "Core inflation", "macro"),
    ("nonfarm-payrolls", "Nonfarm payrolls", "macro"), ("unemployment-rate", "Unemployment rate", "macro"),
    ("recession", "Recession", "macro"),
]
