# Isaac Kim — Portfolio

Live at **https://isaac-waffle.github.io/portfolio/**

A hand-built static site (no framework) with a live **Forecast Lab**: a scheduled pipeline that
publishes next-5-minute, next-hour and next-session forecasts for 16 stocks and scores every
forecast once its bar closes.

```
site/                 static site deployed to GitHub Pages
  index.html          single-page portfolio
  assets/             css, js, images
  demos/              English, in-browser demos (DEGIRO P&L Analyzer, Woning Scout)
  data/               bundled forecast snapshot (fallback if the live feed is unreachable)
forecast/
  forecast.py         data → features → walk-forward → live forecast → forward-test scoring
.github/workflows/
  deploy.yml          deploys site/ on push to main
  forecast.yml        runs forecast.py every 15 min in US market hours, force-pushes JSON to the `data` branch
```

The page reads `https://raw.githubusercontent.com/isaac-waffle/portfolio/data/forecast.json`.

Run locally:

```bash
pip install -r forecast/requirements.txt
python forecast/forecast.py --out out
python -m http.server --directory site
```

Research demo only: nothing here is investment advice.
