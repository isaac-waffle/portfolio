# Isaac Kim — Portfolio

Live at **https://isaac-waffle.github.io/portfolio/**

A hand-built static site (no framework) with a live **Forecast Lab**: a scheduled pipeline that
publishes next-5-minute, next-hour and next-session forecasts for 16 stocks and scores every
forecast once its bar closes.

```
site/                 static site deployed to GitHub Pages
  index.html          single-page portfolio
  assets/             css, js, images
  demos/              in-browser demos (DEGIRO P&L Analyzer, Woning Scout, Earnings Desk)
  data/               bundled forecast snapshot (fallback if the live feed is unreachable)
forecast/
  forecast.py         data → features → walk-forward → live forecast → forward-test scoring
earnings/
  earnings.py         US earnings calendar + per-ticker briefs (profile, price, past reactions, implied move, news)
  ko_profiles.json    Korean company profiles for the watchlist
.github/workflows/
  deploy.yml          deploys site/ on push to main
  forecast.yml        runs forecast.py every 15 min in US market hours, force-pushes JSON to the `data` branch
  earnings.yml        runs earnings.py every 3 hours, force-pushes JSON to the `earnings-data` branch
```

The lab reads `https://raw.githubusercontent.com/isaac-waffle/portfolio/data/forecast.json`;
the Earnings Desk reads `calendar.json` and `t/<TICKER>.json` from the `earnings-data` branch
(`demos/earnings.html?data=<path>` points it at a local build instead).

Run locally:

```bash
pip install -r forecast/requirements.txt
python forecast/forecast.py --out out
python -m http.server --directory site
```

Research demo only: nothing here is investment advice.
