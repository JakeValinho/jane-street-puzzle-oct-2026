# Jane Street Puzzle - October 2026

Interactive helper for solving the 11x11 states puzzle.

## Run locally

Open `index.html` in a browser. No build step or dependencies are required.

For a local web server, from the repository folder you can also run:

```bash
python -m http.server 8000
```

Then open `http://localhost:8000`.

## Files

- `index.html` - page structure and controls
- `styles.css` - layout and visual styling
- `js/puzzle.js` - puzzle dimensions, clue locations, and state colours
- `js/game.js` - painting, capitols, symmetry checks, travel-cost logic, validation, save/import/export, and final score calculation

## Controls

- Left-click or drag: paint into the active state
- Left-click a cell already in the active state: remove that cell from the state
- Right-click: erase a cell
- Shift-click a filled cell: make that state active
- `N`: create a new state
- `M`: toggle capitol-marking mode
- `C`: check the current partition against the clues
- `Ctrl/Cmd + Z`: undo
- `Ctrl/Cmd + Shift + Z` or `Ctrl/Cmd + Y`: redo

Each state can be deleted independently from the States panel. Progress is autosaved in browser local storage and can also be exported/imported as JSON.

## Editing the puzzle

The clue grid is defined in `js/puzzle.js` as `CLUES`, using 1-indexed `row,col` keys. Most puzzle-specific changes can be made there without touching the game logic.


## Dynamic Python solver

The browser can now send its **current live board** to a Python deduction engine.

### Setup

```bash
git pull
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

Do not open `index.html` directly when you want the Python solver, because the page needs the local Flask API.

### Stepwise solving

Click **Python next step**. The page sends the exact current assignments and manually marked capitols to `POST /api/analyze`.

The solver returns deductions sorted approximately as:

1. contradictions
2. forced moves
3. locations with the fewest remaining possibilities
4. structural lower bounds and parity facts

Repeated clicks cycle through the deductions available for the current board. As soon as you edit the board, the next click re-analyzes the new position from scratch.

### Rules currently implemented

- clue `0` forces that exact square to be a capitol
- an already drawn one-square state has its sole square as capitol
- clue `1` must be adjacent to a singleton capitol; impossible neighboring candidates are eliminated
- adjacent clue differences imply minimum state-size bounds
- clues two moves apart imply a minimum size for every square adjacent to both clues
- a state with a marked capitol must ultimately have odd size
- two `0` clues cannot belong to the same state
- positive clues cannot already be completed as singleton states
- partially drawn states must remain connectable through their own or unassigned squares

The engine is deliberately rule-based rather than a black-box brute-force solver. New human-style deductions can be added as independent functions in `solver/engine.py` and registered in `RULES`.

### Tests

```bash
pip install pytest
pytest -q
```

The initial tests verify the forced 0-clue capitols, the forced singleton at r6c11, and a basic contradiction involving the two 0 clues.
