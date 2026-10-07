N = 11

# 0-based coordinates internally.
CLUES = {
    (0, 1): 8, (0, 2): 4, (0, 5): 11, (0, 9): 5,
    (1, 7): 11,
    (2, 2): 7, (2, 5): 1, (2, 10): 14,
    (3, 0): 28, (3, 3): 51, (3, 6): 1, (3, 8): 6,
    (4, 1): 22, (4, 8): 4, (4, 10): 1,
    (5, 3): 15, (5, 5): 10, (5, 7): 0,
    (6, 0): 11, (6, 2): 11, (6, 9): 9,
    (7, 2): 17, (7, 4): 14, (7, 7): 10, (7, 10): 13,
    (8, 0): 30, (8, 5): 6, (8, 8): 45,
    (9, 3): 10,
    (10, 1): 26, (10, 5): 0, (10, 8): 77, (10, 9): 61,
}

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def in_bounds(cell):
    r, c = cell
    return 0 <= r < N and 0 <= c < N


def neighbors(cell):
    r, c = cell
    for dr, dc in DIRS:
        nxt = (r + dr, c + dc)
        if in_bounds(nxt):
            yield nxt


def label(cell):
    return f"r{cell[0] + 1}c{cell[1] + 1}"


def index(cell):
    return cell[0] * N + cell[1]


def from_index(i):
    return divmod(i, N)
