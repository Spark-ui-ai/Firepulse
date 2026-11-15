# 🔥 FirePulse — Burning Activity Calendar

**FirePulse** is an interactive web dashboard that turns NASA satellite fire detections into a clear, day-by-day picture of burning activity for any region on Earth. Draw a box on the map (or pick a preset region), choose your satellite sensors and a date range, and FirePulse shows:

- 🗺️ **Hotspot map**: individual fire detections, or a heatmap for large datasets
- 📅 **Burning Activity Calendar**: a GitHub-style heatmap of daily hotspot counts
- 📈 **Timeline**: daily detections with a moving-average trend line
- 🛰️ **Sensor comparison**: how much each satellite contributes to total detections
- ⚠️ **Anomaly detection**: days where fire activity rises more than **2 standard deviations** above the period mean

Built for the **2026 NASA Space Apps Challenge**.

---

## 🛰️ Data Source: NASA FIRMS

FirePulse uses the [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (Fire Information for Resource Management System) **Area API**, which provides near-real-time (NRT) active fire detections from polar-orbiting satellites:

| Source ID | Satellite / Instrument | Resolution |
|---|---|---|
| `MODIS_NRT` | MODIS on Terra & Aqua | 1 km |
| `VIIRS_NOAA20_NRT` | VIIRS on NOAA-20 | 375 m |
| `VIIRS_NOAA21_NRT` | VIIRS on NOAA-21 | 375 m |

> **Note:** VIIRS on Suomi NPP (`VIIRS_SNPP_NRT`) is no longer offered in FirePulse because the satellite stopped delivering new data in November 2026. If the proxy receives an S-NPP request, it returns an empty result.

Each detection includes its location, the time it was recorded, brightness temperature, **Fire Radiative Power (FRP)** and a confidence value.

---

## ⚙️ How It Works

```
Browser (firepulse.html)  ──►  server.py (local proxy)  ──►  NASA FIRMS Area API
        ▲                          │  • CSV → JSON                │
        └──────── JSON ────────────┘  • 5-day chunking ◄── CSV ───┘
```

The FIRMS API does not allow browser (CORS) requests, and its Area API only returns CSV. `server.py` is a small proxy written with only the Python standard library. It:

1. Serves the dashboard at `/`.
2. Forwards requests sent to `/firms-api/area/...` on to FIRMS as **CSV** requests.
3. **Splits** date ranges longer than 5 days into 5-day requests, runs them at the same time and merges the results.
4. **Parses the CSV into JSON**, converting numeric fields (latitude, longitude, brightness, FRP and so on) into numbers.

### Anomaly detection

For the selected period, FirePulse computes the mean (μ) and standard deviation (σ) of daily hotspot counts. A day is flagged as anomalous when:

```
z = (count − μ) / σ  >  2
```

Flagged days glow in the calendar and are listed with their z-score and how many times above the daily average they are.

---

## 🔑 Get a Free NASA FIRMS API Key

1. Go to **https://firms.modaps.eosdis.nasa.gov/api/map_key/**
2. Enter your email address and request a key.
3. Copy the **MAP_KEY** from the email (a 32-character string, *not* your Earthdata password).
4. Paste it into the **FIRMS API Key** box in FirePulse.

---

## 🚀 Run Locally

**Requirements:** Python 3.8+ with no third-party packages (`requirements.txt` is intentionally empty).

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
python server.py
```

Then open **http://localhost:8080** in your browser.

1. Paste your FIRMS MAP_KEY.
2. Draw a rectangle on the map or pick a preset (Continental US, Europe, Australia, Amazon Basin, South Asia).
3. Choose your sensors and a date range.
4. Click **Fetch fire data**.

## ☁️ Deploy on Render

This repository includes a `render.yaml` Blueprint. To deploy:

1. Sign in to [Render](https://render.com/) and choose **New → Blueprint**.
2. Connect your GitHub account if prompted, then select `Spark-ui-ai/Firepulse`.
3. Render reads `render.yaml` and creates the FirePulse web service.
4. Wait for the first deploy to finish, then open the generated `onrender.com` URL.

The server reads Render's `PORT` environment variable and exposes `/health` for deployment health checks. No Python packages are required. Enter your own NASA FIRMS MAP_KEY in the dashboard; do not commit it to GitHub.

---

## 📁 Project Structure

```
├── server.py          # Static file server + NASA FIRMS CORS proxy (stdlib only)
├── firepulse.html     # Dashboard (Leaflet, Leaflet.draw, Leaflet.heat, Chart.js via CDN)
├── requirements.txt   # Empty: no dependencies
├── render.yaml        # Render Blueprint deployment config
├── .gitignore
├── SUBMISSION.md
└── README.md
```

## 🧰 Tech Stack

Python `http.server` · Leaflet · Leaflet.draw · Leaflet.heat · Chart.js · Esri World Imagery tiles · NASA FIRMS API

## 🙏 Acknowledgements

Fire data courtesy of **NASA FIRMS** (LANCE / EOSDIS). Imagery © Esri.

## 🤖 AI Usage

Claude (Anthropic) helped with server configuration, documentation and drafting the presentation. All code was reviewed and tested by the author.