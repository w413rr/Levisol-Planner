# Levisol Supply Chain Planner

A point-and-click tool that builds Levisol's monthly **production + distribution plan** and
**inventory norms**. No coding needed to operate it.

## What it does
- Reads a month's data file (the case `.xlsx`) and computes inventory norms (safety stock,
  reorder point, days of cover) for every SKU × CFA and SKU × Hub.
- Optimises production (which SKU at which plant, in 25 kL batches), routing
  (plant → hub → CFA) and hub safety stock to **minimise total cost**.
- Shows the cost breakdown, plant utilisation, routing, a network map, and an honest
  list of any unmet demand with its cost.
- Lets you edit inputs (capacities, costs, transport, demand) and **re-run instantly**,
  and compare two scenarios side by side.

## How to run (one time setup)
1. Install Python 3.10+ (https://www.python.org/downloads/).
2. Open a terminal in this folder and run:
   ```
   pip install -r requirements.txt
   ```
3. Start the tool:
   ```
   streamlit run app.py
   ```
   Your browser opens automatically. If not, go to the "Local URL" shown in the terminal.

## How to use it
1. **Left sidebar → Data:** leave blank to use the bundled sample, or upload a new month's
   `.xlsx` (same sheet layout).
2. **Adjust inputs (optional):** change any capacity, cost, transport or demand figure.
3. Press **▶ Run plan.**
4. Read the tabs: Cost · Production · Routing · Map · Inventory norms · Unmet demand · Compare.
5. To compare scenarios: run once, change an input, run again — the previous run becomes the
   baseline in the **Compare scenarios** tab.

## Files
- `app.py` — the user interface (Streamlit).
- `data_loader.py` — reads the Excel data file.
- `norms.py` — inventory-norms calculation (Component 1).
- `optimizer.py` — the production/distribution optimiser (Component 2).
- `coords.py` — map coordinates.
- `Levisol_data.xlsx` — bundled sample data.

## Key modelling choices (defendable assumptions)
- **Batches:** each SKU's *total* monthly output is a whole number of 25 kL batches,
  split freely across plants (honours the batch rule, solves in <1s).
- **Contractual SKUs:** protected by a large penalty multiplier (editable), never a hard
  constraint — so the plan is always feasible, even under a capacity shock.
- **Demand:** met net of opening inventory; shortfalls allowed but penalised.
- **Hub safety stock:** a soft target (penalised shortfall), not a hard floor.
- **Norms:** demand variability from forecast error; lead-time variability from the data;
  fill-rate targets translated to stock via the loss function; hubs use risk pooling.
