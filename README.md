# Isaac Kim — Portfolio

Live at **https://isaac-waffle.github.io/portfolio/**

A hand-built static site (no framework) with a live **Forecast Lab**: a scheduled pipeline that
publishes next-5-minute, next-hour and next-session forecasts for 16 stocks and scores every
forecast once its bar closes.

```
site/                 static site deployed to GitHub Pages
  index.html          single-page portfolio
  assets/             css, js, images (home.css = home page look, i18n.js = Korean text for the EN/KO switch)
  demos/              in-browser demos (DEGIRO P&L Analyzer, Woning Scout, Earnings Desk, Wall Street Legends, Market Radar)
  data/               bundled forecast, legends and radar snapshots (fallback if a live feed is unreachable)
forecast/
  forecast.py         data → features → walk-forward → live forecast → forward-test scoring
earnings/
  earnings.py         US earnings calendar + per-ticker briefs (profile, price, past reactions, implied move, news)
  ko_profiles.json    Korean company profiles for the watchlist
.github/workflows/
  deploy.yml          deploys site/ on push to main
  forecast.yml        runs forecast.py every 15 min in US market hours, force-pushes JSON to the `data` branch
  earnings.yml        runs earnings.py every 3 hours, force-pushes JSON to the `earnings-data` branch
  legends.yml         runs apps/legends.py --out every 3 hours, force-pushes JSON to the `legends-data` branch
  radar.yml           runs apps/radar.py --out every 2 hours, force-pushes JSON to the `radar-data` branch
```

The lab reads `https://raw.githubusercontent.com/isaac-waffle/portfolio/data/forecast.json`;
the Earnings Desk reads `calendar.json` and `t/<TICKER>.json` from the `earnings-data` branch
(`demos/earnings.html?data=<path>` points it at a local build instead).
Wall Street Legends reads `legends.json` from the `legends-data` branch; its rules live in one file,
`apps/legends.py`, which also runs the local app. Market Radar (`demos/radar.html`: heatmap, Reddit mentions,
unusual options volume, insider trades) reads `radar.json` from the `radar-data` branch; the same HTML file is
served by the local app `apps/radar.py`. The home page and Wall Street Legends default to English
and switch to Korean (`?lang=ko`, remembered per browser).

Run locally:

```bash
pip install -r forecast/requirements.txt
python forecast/forecast.py --out out
python -m http.server --directory site
```

Research demo only: nothing here is investment advice.
