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
- a one-cell state is treated as partial unless the rules actually prove it must remain a singleton
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
pip install -r requirements.txt
python -m pytest -q
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

The candidate layer is exact for the constraints it proves. Recursive look-ahead now builds on top of it to eliminate locally legal possibilities when hypothetical propagation reaches a contradiction. Timeouts are never treated as proofs.


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

The solver currently branches on three kinds of exhaustive choices:

- which adjacent singleton capitol satisfies a clue `1`, but only while that clue is not already satisfied by a proved singleton
- which complete shape a state uses when exactly 2 or 3 legal shapes remain
- whether a carefully chosen unresolved square belongs to a partially drawn state

For state-membership branches, the negative hypothesis is stored internally as a per-state exclusion. That means the solver can reason about “r4c5 is **not** in State 3” without incorrectly assigning that square to some other state.

The default UI search uses depth 2, an 8-second wall-clock budget, and at most 28 recursive nodes. These safeguards prevent a difficult branch from freezing the interactive solver. A timeout is reported as **unknown**, never as a contradiction.

The candidate engine also exposes a natural binary branch cell whenever it proves that both “in this state” and “not in this state” still have legal completions. This is used by the minimum-remaining-values heuristic after direct clue branches.

A one-cell state drawn by the user is now treated as a **partial** state, not automatically as a completed singleton. It becomes a true singleton only when the puzzle rules force that conclusion.


## Fixed-point knowledge model

The solver now separates **knowledge** from the visible painted board. The central implementation is `solver/knowledge.py`.

A partially known state can exist internally with only a capitol or a few certain cells. For example, a clue `0` creates an anchored state whose capitol is certain even though the rest of the shape is unknown.

For every known state the engine tracks:

- cells that certainly belong to it
- cells that certainly do not belong to it
- a certain capitol, when known
- proven minimum and maximum state size
- odd-size parity when a capitol is known
- the number of symmetry placements still compatible with the certain information
- explicit complete candidate shapes when the remaining domain is small enough to enumerate exactly

For every cell the engine tracks:

- whether its state is already forced
- whether it must be a capitol
- its clue-derived minimum state size
- known states it cannot belong to
- known states that have not yet been ruled out
- whether it may still belong to a not-yet-created state

### Propagation loop

**Propagate certain facts** runs the following loop until it reaches a fixed point or the interactive time budget expires:

1. anchor every clue-0 capitol
2. resolve any clue-1 with only one singleton-capitol candidate
3. update state-size and parity constraints
4. prune each state's implicit connected/symmetric candidate domain
5. prove candidate intersections (cells in every legal completion)
6. prove exclusions (cells in no legal completion)
7. when a small candidate domain is completely enumerated, intersect and union those full shapes
8. if exactly one complete state shape remains, mark that shape as forced internally
9. repeat because every new inclusion or exclusion can trigger another deduction

This process never commits an unresolved choice. It operates on a clone of the current browser board and returns proved facts.

### Near-forced outcomes and search

Once fixed-point propagation stalls, recursive search uses **minimum remaining values**. The preferred branch is the smallest currently exhaustive domain:

1. a clue-1 singleton choice
2. a state with exactly 2 or 3 complete candidate shapes
3. a two-way cell-membership question for a constrained state

Each option is assumed only on a hypothetical copy. The entire fixed-point knowledge engine is then run again. An option is eliminated only if that hypothetical branch reaches a proved contradiction. If all but one option are eliminated, the survivor is reported as **forced by contradiction**.

The real browser board is never changed by an unproved guess.

### API

`POST /api/knowledge` returns the current fixed-point knowledge base, including internal state knowledge, cell knowledge, certain updates, stored constraints, and contradiction status.

`POST /api/lookahead` uses that same knowledge engine recursively for hypothetical reasoning.
