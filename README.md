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


## Constraint-based state pruning

The solver now includes `solver/candidates.py`, which uses OR-Tools CP-SAT to represent legal completions of a currently drawn state without brute-force enumeration.

For the active state, **Prune active state** discards any completion that violates the constraints currently encoded by the solver:

- cannot leave the 11x11 board
- cannot overlap a square already owned by another state
- must contain every square already painted into that state
- must be orthogonally connected
- must be invariant under at least one nontrivial lattice rotation or reflection
- must satisfy the puzzle's full capitol definition after the candidate shape is constructed
- must respect manually marked capitols
- clue-0 squares must be the capitol of their state
- a state cannot contain two clue-0 squares
- clue-1 forced singleton capitols cannot be absorbed into a larger state
- clue-derived minimum state-size bounds are enforced
- capitol-bearing states are constrained to odd size
- a candidate may not eliminate every possible singleton capitol for a clue-1 square
- a candidate may not make another already-started state impossible to connect
- candidate capitols create additional local upper bounds on clue travel distances
- if the candidate completes the board, the solver performs the full multi-source shortest-path calculation and requires every clue to match exactly

The model is **implicit**: it does not need to list millions of polyominoes. To prove that a square is forced into a state, it asks whether *any* legal completion exists with that square excluded. If none exists, the square is forced.

A timeout never creates a fake deduction. Size bounds are displayed only when CP-SAT proves the optimum, and cells are labelled forced only when infeasibility of the alternative is proved.

The current layer is exact for the constraints above. A future global-search layer can go further by eliminating a locally legal state when it cannot coexist with any complete legal partition of the rest of the board.


## Recursive hypothetical propagation

The browser now has a **Recursive look-ahead** button backed by `solver/lookahead.py`.

The search is deliberately human-style:

1. propagate every currently forced consequence
2. choose the unresolved branch with the fewest options
3. temporarily assume one option
4. propagate again under that hypothesis
5. recurse to a limited depth
6. if a branch reaches a proved contradiction, permanently eliminate that branch
7. if every alternative except one is contradictory, report the survivor as forced

The solver currently branches on two kinds of exhaustive choices:

- which adjacent singleton capitol satisfies a clue `1`
- whether a carefully chosen unresolved square belongs to a partially drawn state

For state-membership branches, the negative hypothesis is stored internally as a per-state exclusion. That means the solver can reason about “r4c5 is **not** in State 3” without incorrectly assigning that square to some other state.

The default UI search uses depth 2, an 8-second wall-clock budget, and at most 28 recursive nodes. These safeguards prevent a difficult branch from freezing the interactive solver. A timeout is reported as **unknown**, never as a contradiction.

The candidate engine also exposes a natural binary branch cell whenever it proves that both “in this state” and “not in this state” still have legal completions. This is used by the minimum-remaining-values heuristic after direct clue branches.

A one-cell state drawn by the user is now treated as a **partial** state, not automatically as a completed singleton. It becomes a true singleton only when the puzzle rules force that conclusion.
