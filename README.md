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
