# SwingDeck Technical Specification
## Feature: Multi-Watchlist Selector & Vertical Scaling

---

### 1. Objective & Scope
This specification defines the technical requirements and architecture for introducing **Multi-Watchlist Vertical Scaling** to the SwingDeck dashboard.

The goal is to allow traders to evaluate multiple pre-configured stock watchlists using the **Momentum Continuation** setup via a clean dropdown interface. The application must maintain its zero-latency, EOD-focused side-by-side workspace layout while loading pre-computed evaluation payloads instantly upon watchlist selection.

---

### 2. Architecture & Backend Data Flow

#### 2.1 File Storage Structure
* **Watchlist Definitions:** Individual watchlists are stored as plain text (`.txt`) or CSV (`.csv`) files within a dedicated backend folder (e.g., `watchlists/`).
* **Format:** Each file contains a simple vertical list of valid ticker symbols (one per line) or a single `Ticker` column.
  * Example: `watchlists/tech_growth.txt`
  * Example: `watchlists/core_swing.txt`

#### 2.2 Nightly Execution & Pre-Computation Model
* **Batch Processing:** Upon market close, the backend Python evaluation pipeline sequentially iterates through all available files in the `watchlists/` directory.
* **Payload Storage:** Results for each watchlist are evaluated and saved as pre-computed JSON payloads (e.g., `results_tech_growth.json`, `results_core_swing.json`).
* **Zero Real-Time API Calls:** Selecting a different watchlist from the UI dropdown simply reads and renders the existing pre-computed JSON file. This eliminates rate-limit risks and execution latency during morning reviews.

---

### 3. UI/UX Layout & Component Specifications

The interface must strictly adhere to the approved side-by-side workspace structure without adding unnecessary chrome, terminal widgets, or redundant trade-plan outputs.

#### 3.1 Application Header & Navigation Stack
```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ SwingDeck                                            Screening settings ⚙   │
│                                                                             │
│ [ Momentum Continuation ]                                                   │
│ ─────────────────────────────────────────────────────────────────────────────│
│ Active Watchlist: [ Tech & Growth Leaders (42 tickers)               ▾ ]    │
│                     source: tech_growth.txt                                 │
│                                                                             │
│ EOD data: Oct 24, 2024 · Refreshed: Oct 24, 2024, 18:05 EDT                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ [ 🟢 Qualified · 3 ]     [ 🟡 Developing · 4 ]     [ ⚪ Other monitored · 18 ] │
└─────────────────────────────────────────────────────────────────────────────┘
```

1. **Top Bar:** 
   * Left: Application title (`SwingDeck`).
   * Right: Settings trigger (`Screening settings ⚙`).
2. **Setup Tab Bar:**
   * Active primary tab: `Momentum Continuation`.
   * Structure must allow architectural expansion for future setup tabs without rendering disabled placeholders.
3. **Watchlist Selector Bar:**
   * Placed directly beneath the active setup tab.
   * Features a clean dropdown component: `Active Watchlist: [ Name (n tickers) ▾ ]`.
   * Displays source metadata badge (e.g., `source: filename.txt`).
4. **Data Freshness Bar:**
   * Compact single-line string: `EOD data: [date] · Refreshed: [timestamp]`.
5. **Primary Status Filters:**
   * Three summary cards/chips: `[ 🟢 Qualified · n ]`, `[ 🟡 Developing · n ]`, and `[ ⚪ Other monitored · n ]`.

---

### 4. Interactive Behavior

1. **Dropdown Selection Change:**
   * Triggering a selection in the watchlist dropdown dynamically switches the loaded JSON payload.
   * Updates summary filter counts (`Qualified`, `Developing`, `Other monitored`) instantly.
   * Clears or updates the candidate list pane on the left and selected stock detail pane on the right.
2. **Selection Persistence:**
   * When switching between watchlists, if the previously selected stock exists in the newly chosen list, retain its selection in the inspection panel; otherwise, auto-select the first `Qualified` or `Developing` candidate.

---

### 5. Acceptance Criteria

1. **Dynamic File Discovery:** The backend automatically reads all `.txt`/`.csv` watchlist files in the directory and exposes them in the dropdown UI.
2. **Pre-computed State Rendering:** Changing watchlists renders the candidate list and status counts with zero network API delay.
3. **Layout Consistency:** Top header hierarchy (Title -> Setup Tab -> Watchlist Dropdown -> Status Chips -> Workspace) remains clean and unified across all watchlists.
4. **Clean Workspace:** No trade-plan outputs (entry/stop/target/R:R) or diagnostic bloat are rendered in the candidate list or detail panels.