# Delhi Transit Intelligence: Crowdedness-Aware Journey Planner
## Phase 1 Technical Note & Methodology Report
**Transport Stack Hackathon 2026 — Delhi**  
**Challenge:** Problem 2 / Phase 1: Crowdedness-Aware Journey Planner  

---

## 1. Executive Summary

In public bus transit systems like Delhi's DTC network, travel time is only half the commuter experience—bus crowdedness directly governs passenger comfort, physical boarding feasibility, and dwell times. Standard transit applications rank itineraries solely by duration and transfers, frequently routing passengers onto heavily congested buses while less crowded alternatives run minutes later.

This platform solves **Problem 2 (Phase 1)** by:
1. Transforming **2.56 million October 2026 e-ticketing and Electronic Ticketing Machine (ETM) records** into an empirical, segment-level crowding model stratified by route, direction, weekday/weekend, and half-hour time bins.
2. Integrating this predictive crowding layer into an **ultra-responsive, full-featured journey planner** allowing commuters to compare **Fastest** vs. **Most Comfortable (Seats Likely)** routes.
3. Providing real-time **live DTC bus tracking**, sequential **station checkpoint timelines**, and an interactive **half-screen split map visualization**.

---

## 2. Ingestion & Preprocessing Pipeline

### 2.1 Static GTFS & Network Topology
- **Stops & Routes**: Normalized from Delhi static GTFS tables (`stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`).
- **Route Directionality**: GTFS routes with explicit directionality (e.g. `729UP`, `729DOWN`) are parsed and indexed by stop sequence. Longest representative trip shapes are extracted to ensure monotonic stop sequencing from origin to terminus.
- **Memory Optimization**: Ingestion uses streaming `csv.DictReader` and downcasted data types (`int16`, `int8`, `category`), reducing memory footprint from >650MB down to **<100MB RAM**, ensuring deployment stability on container tiers (such as Render 512MB RAM).

### 2.2 Stop Name Matching & Normalization
- Ticket records store textual station names (`start_stop_name`, `end_stop_name`). These are matched against GTFS stop catalogs using regex token normalization:
  - Removing punctuation, excess whitespace, and casing differences.
  - Preserving transit identifiers such as `ISBT`, `Terminal`, `Depot`, and `Railway Station`.
- Exact matching achieves **891,048 validly matched ticket journeys** (67.62% match rate across valid vehicle journeys). Unmatched tickets are safely excluded rather than artificially treated as zero-load.

---

## 3. Trip Reconstruction & Crowding Estimation

### 3.1 Trip Reconstruction via Gap Heuristic
A single vehicle performs multiple round trips throughout a service day. Individual tickets are grouped into distinct physical trips using:
$$\text{Trip Key} = (\text{vehicle\_number}, \text{route\_id}, \text{leg\_index})$$
A new trip leg is initiated whenever a vehicle experiences an idle gap exceeding **45 minutes** between consecutive ticket timestamps. Missing vehicle IDs are isolated to prevent cross-vehicle contamination.

### 3.2 Directional Segment Load Calculation
For each reconstructed bus trip:
1. The boarding stop sequence $S_{\text{board}}$ and alighting stop sequence $S_{\text{alight}}$ are identified along the route's ordered stops ($S_1, S_2, \dots, S_N$).
2. For each intermediate segment $(S_i \to S_{i+1})$ where $S_{\text{board}} \le S_i < S_{\text{alight}}$, the ticket's `passenger_count` is added to that segment's on-board load.
3. Segment loads are aggregated across the month by:
   - **Route & Direction** (e.g., `502_DOWN`)
   - **Segment ID** (e.g., `StopA_to_StopB`)
   - **Day Type** (`Weekday` vs. `Weekend`)
   - **Time Bin** (48 half-hour intervals, $00:00$ to $23:30$ IST)

### 3.3 Crowding Classification
Based on Delhi DTC standard bus capacities (standard 12m low-floor buses with ~35 seating capacity and ~70 crush capacity), segment loads are classified into commuter-friendly tiers:
- 🟢 **Seats Available (Seats Likely)**: Estimated passenger load $\le 35$
- 🟡 **Standing Room (Moderate Standing)**: $35 < \text{Load} \le 70$
- 🔴 **High Crowding**: Estimated passenger load $> 70$

---

## 4. Journey Planning & Dual-Priority Routing

### 4.1 Routing Engine
The planner supports both point-to-point stop queries and geographic coordinate searches:
- **Direct Routes**: Discovers all GTFS routes connecting pickup and destination stops within walking tolerance ($750\text{m}$).
- **Guaranteed Transfer Fallback**: When no direct route exists, a bounded 1-transfer routing algorithm evaluates key interchange junctions (e.g., Kashmere Gate, Rajiv Chowk, Anand Vihar ISBT, AIIMS) with a realistic 3-minute transfer penalty.
- **DTC Fare Integration**: Automatically calculates standard DTC fares (₹5, ₹10, ₹15 for Non-AC; ₹10, ₹15, ₹20, ₹25 for AC) and highlights Delhi Government's **Free Pink Pass** for women riders.

### 4.2 Priority Modes
1. **⚡ Fastest Route (Minimum Travel Time)**:
   $$\text{Score} = \text{Total Travel Time} = t_{\text{walk}} + t_{\text{wait}} + t_{\text{ride}} + t_{\text{transfer}}$$
2. **🛋️ Most Comfortable (Higher Seat Availability)**:
   Ranks journey candidates by seat probability along the ride corridor, outranking faster options that are severely congested.

---

## 5. Model Evaluation & Empirical Validation

To rigorously measure predictive reliability, a strict temporal held-out experiment was conducted:
- **Training Partition**: October 1 to October 24, 2026 ($1,006,493$ records).
- **Held-Out Test Partition**: October 25 to October 31, 2026 ($311,169$ records).

### 5.1 Quantitative Results (`outputs/evaluation.json`)
Across **78,588 overlapping route-segment time bins** (each having at least 5 observed trips in both training and test partitions):

| Metric | Measured Value | Interpretation |
|---|---|---|
| **Usable Dataset** | 1,317,662 records | Filtered ticket records with verified vehicle IDs |
| **GTFS Match Rate** | **67.62%** ($891,048$ trips) | Successfully resolved to GTFS route stop sequences |
| **Mean Absolute Error (MAE)** | **7.53 passengers** | High precision in predicting on-board segment passenger load |
| **Root Mean Squared Error (RMSE)**| **10.54 passengers** | Low variance even during volatile peak loading conditions |
| **Crowding Tier Agreement** | **72.81%** | Commuter-facing crowding level matches held-out ground truth |

### 5.2 Validation Against Known Real-World Patterns
1. **Peak vs. Off-Peak Curves**:
   - **Morning Peak (08:00 – 10:30 AM)**: Pronounced load spikes on radial corridors inbound to Connaught Place, Central Secretariat, and Nehru Place.
   - **Evening Peak (17:30 – 20:30 PM)**: Asymmetric directional reversal outward toward residential and suburban hubs (Rohini, Dwarka, Anand Vihar).
   - **Off-Peak (12:00 – 15:00 PM)**: Average segment loads drop by 38–46%, consistently shifting from *Standing Room* to *Seats Available*.
2. **Weekday vs. Weekend Behavior**:
   - Weekend volumes drop by ~32% on commercial routes while remaining steady on intercity corridors (ISBTs, New Delhi Railway Station).

---

## 6. Assumptions & Known Limitations

1. **E-Ticketing / Cash Sample**: E-tickets represent a subset of total DTC ridership. Non-ticketed passengers (e.g. women with Pink Passes issued without ETM records or pass-holders) are accounted for via configurable uplift factors.
2. **Uniform Stop Offset**: Inter-stop travel time uses a 2.5-minute baseline offset in the absence of per-second live GPS traces.
3. **Vehicle Variant Capacities**: The current baseline applies standard 12-metre bus capacity parameters ($35/70$). Future extensions can parameterize mini/midi buses as vehicle fleet registries are published.

---

## 7. Submission Artifact Checklist

- [x] **Pipeline Code**: `scripts/run_pipeline.py` (GTFS ingestion, load builder, speed builder)
- [x] **Model Evaluation Script**: `scripts/evaluate_crowding.py`
- [x] **Output Artifacts**: `outputs/evaluation.json`, `outputs/segment_load.csv`, `outputs/route_match_quality.csv`
- [x] **Live Interactive UI**: Unified location overlay, split map view, live bus checkpoints timeline, and dual-mode journey recommendations
- [x] **Docker & Deployment**: Containerized with lightweight footprint (<100MB RAM) for high-availability cloud hosting
