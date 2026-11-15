### Claude Code Specification: Watchlist-First UI Hierarchy Refactor

#### 1. Objective
Refactor the SwingDeck Streamlit UI from a setup-first tab model to a **watchlist-first accordion model** based on the attached visual design.

#### 2. Top-Level Header & Watchlist Selector
- **Header:** Title `SwingDeck` (left) and `Settings ⚙` (right).
- **Watchlist Selector:** Single selector bar for active watchlist (e.g., `Tech & Growth Leaders (42 tickers)`), showing current file source badge (e.g., `source: tech_growth.txt`) and EOD metadata line below it.

#### 3. Setup Accordions View (`st.expander`)
- Loop through available trading setups (`Momentum Continuation`, `Pullback Value`, `Breakout Volatility`, etc.).
- **Accordion Header:** Render setup title on the left and colored status summary pills on the right (`🟢 Qualified · n`, `🟡 Developing · n`, `⚪ Monitored · n`).
- **Default Expansion Logic:**
  - Auto-expand accordions that contain `Qualified > 0` or `Developing > 0`.
  - Keep accordions collapsed by default if they contain zero Qualified and zero Developing candidates.
- **Accordion Body:**
  - Inside an expanded accordion, display the candidate review workspace.
  - Tickers matching multiple setups should independently appear within each setup's respective accordion.

#### 4. Selection & Inspection State
- Maintain session state for selected candidate focus within each accordion so expanding/collapsing sections doesn't reset active selection state.