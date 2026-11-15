# FirePulse — NASA Space Apps 2026 Submission Materials

---

## Part A: 7-Slide Presentation Outline

### Slide 1: Title
**FirePulse: Burning Activity Calendar**
*Turning NASA satellite fire detections into day-by-day insight for any region on Earth*

- Team: Damanpreet Singh Deol · Chandigarh University
- NASA Space Apps Challenge 2026

📸 **[SCREENSHOT: Full dashboard with South Asia selected, heatmap on the map and calendar visible]**

---

### Slide 2: The Problem
**Fire data is abundant but hard to read**

- NASA satellites detect thousands of active fires every day
- Raw FIRMS data comes as CSV rows of coordinates, which are hard for non-experts to interpret
- Key questions go unanswered: *When did burning peak? Which days were unusual? Do different sensors agree?*
- Crop-residue burning, wildfires and deforestation fires all need **temporal** context, not just a map of dots

📸 **[IMAGE: Raw FIRMS CSV snippet beside a cluttered dot map, labelled "Before FirePulse"]**

---

### Slide 3: Our Solution
**Pick an area → pick sensors → see the pattern**

- 🖱️ **Bounding-box selector**: draw a rectangle anywhere on satellite imagery
- 📍 **Preset regions**: Continental US, Europe, Australia, Amazon Basin and South Asia in one click
- 🛰️ **Multi-sensor toggles**: MODIS (Terra & Aqua), VIIRS NOAA-20 and VIIRS NOAA-21
- 📅 Any date range; FirePulse batches the API requests automatically

📸 **[SCREENSHOT: Sidebar with the API key box, sensor toggles and preset list, plus a red rectangle being drawn on the map]**

---

### Slide 4: Burning Activity Calendar
**See an entire season at a glance**

- GitHub-style heatmap: each cell is a day, and darker red means more hotspots
- Hover over a cell for the exact date and hotspot count
- Shows weekly rhythms and seasonal build-up (for example, post-harvest stubble burning in Punjab in Oct–Nov)
- Paired with a **timeline chart** that adds a moving-average trend line

📸 **[SCREENSHOT: Calendar tab with a tooltip showing on one cell]**
📸 **[SCREENSHOT: Timeline tab, bars plus blue trend line]**

---

### Slide 5: Multi-Sensor Comparison
**Different satellites, one picture**

- Doughnut chart and table showing each sensor's share of detections
- VIIRS (375 m) catches smaller fires than MODIS (1 km), and FirePulse makes that difference visible
- Hotspot markers are colour-coded by sensor; click one for FRP, confidence and time
- Suomi NPP was removed after it was retired in November 2026, so the dashboard uses only active satellites

📸 **[SCREENSHOT: "By Sensor" tab doughnut and the sidebar sensor-breakdown table]**

---

### Slide 6: Anomaly Detection
**Automatically flag unusual burning days**

- Computes the mean (μ) and standard deviation (σ) of daily hotspot counts
- Flags any day where **z = (count − μ) / σ > 2**
- Anomalous cells **glow** on the calendar and are listed with their z-score and how many times above the daily average they are
- Can serve as an early-warning cue for fire managers, researchers and policymakers

📸 **[SCREENSHOT: Anomalies tab list, plus a glowing calendar cell]**

---

### Slide 7: How It Works & What's Next
**Lightweight, open and deployable anywhere**

- **Architecture:** Browser → `server_3.py` (hardened Python stdlib proxy) → NASA FIRMS Area API
  - Fixes CORS · converts CSV to JSON · splits ranges into 5-day requests that run in parallel
- **Stack:** Leaflet, Chart.js, Python `http.server`, with no other dependencies
- **Next steps:** air-quality (AQI) overlay, CSV/PDF export, email alerts for anomalies, land-cover context

🔗 GitHub: `<your repo URL>` · 🌐 Live demo: `<your Render URL>`

📸 **[DIAGRAM: Three-box architecture flow]**

---

## Part B: Submission Form Text

### Project Name
FirePulse

### High-Level Summary
FirePulse is an interactive web dashboard that turns NASA FIRMS active-fire satellite data into a "burning activity calendar" for any region on Earth. Users draw a bounding box or pick a preset region, choose MODIS and VIIRS sensors, and instantly see a hotspot map, a day-by-day calendar heatmap, a trend timeline, a sensor comparison and automatically flagged anomalous burning days (more than 2 standard deviations above the mean). It makes near-real-time fire data easy to read for researchers, fire managers, journalists and the public.

### Detailed Project Description
**What it does:**
FirePulse answers *when* and *how unusually* a region is burning, not just *where*. After the user enters a free NASA FIRMS MAP_KEY, selects an area (drawn on Esri satellite imagery or picked from presets such as South Asia, the Amazon Basin, Australia, Europe and the Continental US) and sets a date range, FirePulse retrieves every active-fire detection from the selected satellites and presents:

1. **Hotspot map**: colour-coded markers for small datasets, or a heatmap weighted by Fire Radiative Power (FRP) for large ones.
2. **Burning Activity Calendar**: a calendar heatmap in which each cell's shade shows that day's hotspot count.
3. **Timeline**: daily detection bars with a moving-average trend line.
4. **Sensor comparison**: the share of detections from MODIS (Terra/Aqua), VIIRS NOAA-20 and VIIRS NOAA-21.
5. **Anomaly detection**: days with a z-score above 2 are highlighted and listed with how many times above the daily average they are.

**How it works:**
The front end (`firepulse.html`) is a single page built with Leaflet, Leaflet.draw, Leaflet.heat and Chart.js. The deployed back end (`server_3.py`) uses only Python's standard library. It accepts the FIRMS MAP_KEY only in the `X-FIRMS-MAP-KEY` header, validates request inputs, and applies bounded upstream concurrency, response-size limits and request deadlines. It serves the page and works as a proxy to the FIRMS Area API, which solves the browser CORS restriction. The proxy requests CSV from FIRMS, converts it to JSON with numeric fields properly typed, splits date ranges into bounded upstream chunks, and enforces input validation, request deadlines, response-size limits and concurrency caps. The MAP_KEY is sent in a request header, not in the URL. Retired sources such as Suomi NPP are handled gracefully. The app reads the `PORT` environment variable, so it can be deployed on platforms such as Render with no configuration.

**Why it matters:**
Agricultural residue burning, wildfires and deforestation fires harm air quality, health and ecosystems. Patterns over time, such as seasonal peaks and sudden spikes, are what decision-makers need, yet they are hidden in raw satellite data. FirePulse puts that information one click away, for free, using open NASA data.

**Tools & technologies:** Python 3 (`http.server`, `urllib`, `csv`, `concurrent.futures`), HTML/CSS/JavaScript, Leaflet, Chart.js, Esri World Imagery, NASA FIRMS API.

### Space Agency Data / Resources Used
- **NASA FIRMS (Fire Information for Resource Management System)**: Area API, https://firms.modaps.eosdis.nasa.gov/api/
- **MODIS active fire data** (`MODIS_NRT`, Terra & Aqua, 1 km)
- **VIIRS active fire data** (`VIIRS_NOAA20_NRT` and `VIIRS_NOAA21_NRT`, 375 m)
- Data distributed via NASA LANCE / EOSDIS

### AI Usage Disclosure
We used Claude (an AI assistant by Anthropic) to help configure the Python proxy server: CSV-to-JSON conversion, 5-day request splitting and cloud-hosting setup. We also used it to draft the README and documentation, and to outline the presentation and this submission text. All AI-assisted output was reviewed, tested and edited by the team, and the project concept, design and final decisions are our own.