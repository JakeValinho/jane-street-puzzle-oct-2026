from math import ceil

from .puzzle import CLUES, N, neighbors


def derive_cell_min_sizes():
    """Provable lower bounds on the size of the state containing each cell."""
    bounds = {}
    reasons = {}
    items = list(CLUES.items())

    def add(cell, minimum, reason):
        if minimum <= 1:
            return
        if minimum > bounds.get(cell, 1):
            bounds[cell] = minimum
            reasons[cell] = reason

    for i, (a, da) in enumerate(items):
        for b, db in items[i + 1:]:
            diff = abs(da - db)
            if diff == 0:
                continue
            manhattan = abs(a[0] - b[0]) + abs(a[1] - b[1])

            if manhattan == 1:
                add(a, diff, (a, b, da, db, "adjacent"))
                add(b, diff, (a, b, da, db, "adjacent"))
            elif manhattan == 2:
                minimum = ceil(diff / 2)
                for middle in set(neighbors(a)).intersection(neighbors(b)):
                    add(middle, minimum, (a, b, da, db, "two-step"))

    return bounds, reasons


CELL_MIN_SIZES, CELL_MIN_REASONS = derive_cell_min_sizes()


def singleton_capitol_candidates(board, clue_cell):
    """Candidates that could be a one-square capitol adjacent to a clue 1."""
    candidates = []
    for cell in neighbors(clue_cell):
        # A numbered square can be a capitol only when its clue is 0.
        if cell in CLUES and CLUES[cell] != 0:
            continue

        state_id = board.state_at(cell)
        if state_id is not None and board.current_size(state_id) > 1:
            continue

        # If this cell were a singleton capitol, every adjacent numbered
        # square could reach a capitol in cost 1.
        if any(CLUES.get(adj, 0) > 1 for adj in neighbors(cell)):
            continue

        candidates.append(cell)
    return candidates


def is_locked_singleton_capitol(board, cell):
    """
    True only when the knowledge base has proved that this state can contain
    no square other than the supplied cell and has fixed its capitol there.

    A one-cell shape merely painted by the user is still partial, so current
    size == 1 by itself is not enough.
    """
    state_id = board.state_at(cell)
    if state_id is None or board.current_size(state_id) != 1:
        return False
    if board.marked_capitol_cell(state_id) != cell:
        return False

    forbidden = board.forbidden_by_state.get(state_id, set())
    return len(forbidden) >= N * N - 1


def forced_singleton_capitols(board):
    forced = set()
    for cell, value in CLUES.items():
        if value != 1:
            continue
        candidates = singleton_capitol_candidates(board, cell)
        if len(candidates) == 1:
            forced.add(candidates[0])
    return forced
