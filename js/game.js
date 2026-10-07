(function () {
  'use strict';

  var cfg = window.PUZZLE_CONFIG;
  var N = cfg.N;
  var STORAGE_KEY = cfg.STORAGE_KEY;
  var CLUES = cfg.CLUES;
  var BASE_COLORS = cfg.BASE_COLORS;

  var assignments = Array(N * N).fill(null);
  var regions = [];
  var activeRegion = null;
  var nextId = 1;
  var manualCapitols = {};
  var capitolMode = false;
  var undoStack = [];
  var redoStack = [];
  var dragging = false;
  var dragErase = false;
  var lastDistances = null;
  var clueStatus = {};

  var board = document.getElementById('board');
  var stateList = document.getElementById('stateList');
  var summary = document.getElementById('summary');
  var resultPanel = document.getElementById('resultPanel');
  var rowResults = document.getElementById('rowResults');
  var finalAnswer = document.getElementById('finalAnswer');
  var activeInfo = document.getElementById('activeInfo');
  var showIds = document.getElementById('showIds');
  var showDistances = document.getElementById('showDistances');
  var capitolModeBtn = document.getElementById('capitolMode');

  function idx(r, c) { return r * N + c; }
  function rc(i) { return [Math.floor(i / N), i % N]; }
  function key1(r, c) { return String(r + 1) + ',' + String(c + 1); }
  function regionById(id) { return regions.find(function (r) { return r.id === id; }) || null; }
  function regionSize(id) { return assignments.reduce(function (n, v) { return n + (v === id ? 1 : 0); }, 0); }
  function cellsOf(id) {
    var out = [];
    assignments.forEach(function (v, i) { if (v === id) out.push(rc(i)); });
    return out;
  }

  function snapshot() {
    return {
      assignments: assignments.slice(),
      regions: regions.map(function (r) { return { id: r.id, color: r.color }; }),
      activeRegion: activeRegion,
      nextId: nextId,
      manualCapitols: Object.assign({}, manualCapitols)
    };
  }

  function restore(s) {
    assignments = s.assignments.slice();
    regions = (s.regions || []).map(function (r) { return { id: r.id, color: r.color }; });
    activeRegion = s.activeRegion == null ? null : s.activeRegion;
    nextId = s.nextId || 1;
    manualCapitols = Object.assign({}, s.manualCapitols || {});
    invalidate();
    render();
    save();
  }

  function pushUndo() {
    undoStack.push(snapshot());
    if (undoStack.length > 150) undoStack.shift();
    redoStack = [];
  }

  function invalidate() {
    lastDistances = null;
    clueStatus = {};
    resultPanel.hidden = true;
  }

  function colorFor(id) { return BASE_COLORS[(id - 1) % BASE_COLORS.length]; }

  function addRegion() {
    pushUndo();
    activeRegion = nextId++;
    regions.push({ id: activeRegion, color: colorFor(activeRegion) });
    render();
    save();
  }

  function clearCapitolIfMoved(i, oldId) {
    if (oldId != null && Number(manualCapitols[oldId]) === i) delete manualCapitols[oldId];
  }

  function paint(i, id) {
    if (assignments[i] === id) return false;
    clearCapitolIfMoved(i, assignments[i]);
    assignments[i] = id;
    invalidate();
    return true;
  }

  function erase(i) {
    if (assignments[i] == null) return false;
    clearCapitolIfMoved(i, assignments[i]);
    assignments[i] = null;
    invalidate();
    return true;
  }

  function deleteRegion(id) {
    var size = regionSize(id);
    if (size && !window.confirm('Delete State ' + id + ' and unassign its ' + size + ' cell' + (size === 1 ? '' : 's') + '?')) return;
    pushUndo();
    assignments = assignments.map(function (v) { return v === id ? null : v; });
    delete manualCapitols[id];
    regions = regions.filter(function (r) { return r.id !== id; });
    if (activeRegion === id) activeRegion = regions.length ? regions[regions.length - 1].id : null;
    invalidate();
    summary.textContent = 'State ' + id + ' deleted.';
    render();
    save();
  }

  function setCapitolMode(on) {
    capitolMode = on;
    capitolModeBtn.classList.toggle('mode-on', on);
    capitolModeBtn.textContent = on ? 'Mark capitol: ON (M)' : 'Mark capitol (M)';
  }

  function toggleManualCapitol(i) {
    var id = assignments[i];
    if (id == null) {
      summary.textContent = 'Assign that square to a state before marking it as a capitol.';
      return;
    }
    pushUndo();
    activeRegion = id;
    if (Number(manualCapitols[id]) === i) {
      delete manualCapitols[id];
      summary.textContent = 'Removed the capitol mark from State ' + id + '.';
    } else {
      manualCapitols[id] = i;
      var p = rc(i);
      summary.textContent = 'Marked r' + (p[0] + 1) + 'c' + (p[1] + 1) + ' as State ' + id + "'s capitol.";
    }
    invalidate();
    render();
    save();
  }

  function connected(cells) {
    if (!cells.length) return false;
    var set = new Set(cells.map(function (p) { return p[0] + ',' + p[1]; }));
    var seen = new Set([cells[0][0] + ',' + cells[0][1]]);
    var q = [cells[0]];
    for (var qi = 0; qi < q.length; qi++) {
      var p = q[qi];
      [[1,0],[-1,0],[0,1],[0,-1]].forEach(function (d) {
        var k = (p[0] + d[0]) + ',' + (p[1] + d[1]);
        if (set.has(k) && !seen.has(k)) {
          seen.add(k);
          q.push([p[0] + d[0], p[1] + d[1]]);
        }
      });
    }
    return seen.size === cells.length;
  }

  var TRANSFORMS = [
    { name: 'rotate 90°', f: function (r, c) { return [-c, r]; } },
    { name: 'rotate 180°', f: function (r, c) { return [-r, -c]; } },
    { name: 'rotate 270°', f: function (r, c) { return [c, -r]; } },
    { name: 'reflect horizontal', f: function (r, c) { return [-r, c]; } },
    { name: 'reflect vertical', f: function (r, c) { return [r, -c]; } },
    { name: 'reflect main diagonal', f: function (r, c) { return [c, r]; } },
    { name: 'reflect other diagonal', f: function (r, c) { return [-c, -r]; } }
  ];

  function normalize(points) {
    var minR = Math.min.apply(null, points.map(function (p) { return p[0]; }));
    var minC = Math.min.apply(null, points.map(function (p) { return p[1]; }));
    return points.map(function (p) { return [p[0] - minR, p[1] - minC]; });
  }

  function setKey(points) {
    return points.map(function (p) { return p[0] + ',' + p[1]; }).sort().join('|');
  }

  function analyzeRegion(id) {
    var original = cellsOf(id);
    var size = original.length;
    if (!size) return { id: id, size: 0, valid: false, reason: 'empty', hasCapitol: false };
    if (!connected(original)) return { id: id, size: size, valid: false, reason: 'disconnected', hasCapitol: false };

    var norm = normalize(original);
    var target = setKey(norm);
    var symmetries = [];

    TRANSFORMS.forEach(function (t) {
      var raw = norm.map(function (p) { return t.f(p[0], p[1]); });
      var transformed = normalize(raw);
      if (setKey(transformed) !== target) return;
      var fixed = [];
      for (var i = 0; i < norm.length; i++) {
        if (norm[i][0] === transformed[i][0] && norm[i][1] === transformed[i][1]) fixed.push(norm[i]);
      }
      symmetries.push({ name: t.name, fixed: fixed });
    });

    if (!symmetries.length) return { id: id, size: size, valid: false, reason: 'no nontrivial symmetry', hasCapitol: false, symmetries: [] };

    var hasCapitol = false;
    var capitol = null;
    var capitalReason = 'no capitol';
    var oneFixed = symmetries.every(function (s) { return s.fixed.length === 1; });

    if (oneFixed) {
      var cp = symmetries[0].fixed[0];
      var same = symmetries.every(function (s) { return s.fixed[0][0] === cp[0] && s.fixed[0][1] === cp[1]; });
      if (same) {
        var minR = Math.min.apply(null, original.map(function (p) { return p[0]; }));
        var minC = Math.min.apply(null, original.map(function (p) { return p[1]; }));
        capitol = [cp[0] + minR, cp[1] + minC];
        hasCapitol = true;
        capitalReason = 'capitol';
      } else {
        capitalReason = 'symmetries fix different squares';
      }
    } else {
      var witness = symmetries.find(function (s) { return s.fixed.length !== 1; });
      capitalReason = witness.name + ' fixes ' + witness.fixed.length + ' cells';
    }

    return { id: id, size: size, valid: true, reason: 'valid state', hasCapitol: hasCapitol, capitol: capitol, capitalReason: capitalReason, symmetries: symmetries };
  }

  function allAnalyses() {
    return regions.filter(function (r) { return regionSize(r.id) > 0; }).map(function (r) { return analyzeRegion(r.id); });
  }

  function computeDistances(analyses) {
    var sizes = new Map(analyses.map(function (a) { return [a.id, a.size]; }));
    var caps = analyses.filter(function (a) { return a.hasCapitol; }).map(function (a) { return idx(a.capitol[0], a.capitol[1]); });
    var dist = Array(N * N).fill(Infinity);
    var used = Array(N * N).fill(false);
    caps.forEach(function (i) { dist[i] = 0; });

    for (var iter = 0; iter < N * N; iter++) {
      var u = -1;
      var best = Infinity;
      for (var i = 0; i < N * N; i++) {
        if (!used[i] && dist[i] < best) { best = dist[i]; u = i; }
      }
      if (u < 0) break;
      used[u] = true;
      var p = rc(u);
      [[1,0],[-1,0],[0,1],[0,-1]].forEach(function (d) {
        var rr = p[0] + d[0];
        var cc = p[1] + d[1];
        if (rr < 0 || rr >= N || cc < 0 || cc >= N) return;
        var v = idx(rr, cc);
        var a = assignments[u];
        var b = assignments[v];
        var w = a === b ? sizes.get(a) : Math.min(sizes.get(a), sizes.get(b));
        var nd = dist[u] + w;
        if (nd < dist[v]) dist[v] = nd;
      });
    }
    return dist;
  }

  function validateAndCheck() {
    clueStatus = {};
    resultPanel.hidden = true;
    var unassigned = assignments.filter(function (x) { return x == null; }).length;
    if (unassigned) {
      summary.textContent = 'Board incomplete: ' + unassigned + ' cell' + (unassigned === 1 ? '' : 's') + ' still unassigned.\n\nFinish the partition before clue travel costs can be computed.';
      lastDistances = null;
      render();
      return;
    }

    var usedIds = Array.from(new Set(assignments));
    var analyses = usedIds.map(analyzeRegion);
    var invalid = analyses.filter(function (a) { return !a.valid; });
    if (invalid.length) {
      summary.textContent = 'Every state must be connected and have at least one nontrivial rotational or reflectional symmetry. A valid state may have no capitol.\n\n' + invalid.map(function (a) { return 'State ' + a.id + ' (size ' + a.size + '): ' + a.reason; }).join('\n');
      lastDistances = null;
      render();
      return;
    }

    if (!analyses.some(function (a) { return a.hasCapitol; })) {
      summary.textContent = 'The partition has no state capitols, so the clue distances cannot be satisfied.';
      lastDistances = null;
      render();
      return;
    }

    var markNotes = [];
    analyses.forEach(function (a) {
      var marked = manualCapitols[a.id];
      if (marked === undefined && a.hasCapitol) markNotes.push('State ' + a.id + ': capitol not marked');
      else if (marked !== undefined && !a.hasCapitol) markNotes.push('State ' + a.id + ': marked C, but this shape has no capitol');
      else if (marked !== undefined && a.hasCapitol && Number(marked) !== idx(a.capitol[0], a.capitol[1])) markNotes.push('State ' + a.id + ': capitol mark is not on the symmetry-fixed square');
    });

    lastDistances = computeDistances(analyses);
    var good = 0;
    var badLines = [];
    Object.keys(CLUES).forEach(function (k) {
      var parts = k.split(',').map(Number);
      var target = CLUES[k];
      var actual = lastDistances[idx(parts[0] - 1, parts[1] - 1)];
      var ok = actual === target;
      clueStatus[k] = ok ? 'good' : 'bad';
      if (ok) good++;
      else badLines.push('r' + parts[0] + 'c' + parts[1] + ': target ' + target + ', current ' + actual);
    });

    var total = Object.keys(CLUES).length;
    if (good === total) {
      summary.textContent = 'All ' + total + ' clue values match. The partition satisfies the puzzle constraints.' + (markNotes.length ? '\n\nCapitol marks:\n' + markNotes.join('\n') : '\n\nAll player-marked capitols are consistent.');
      showSolutionTotals();
    } else {
      summary.textContent = good + '/' + total + ' clue values match.\n\nMismatches:\n' + badLines.join('\n') + (markNotes.length ? '\n\nCapitol marks:\n' + markNotes.join('\n') : '');
    }
    render();
  }

  function showSolutionTotals() {
    var sizes = new Map();
    Array.from(new Set(assignments)).forEach(function (id) { sizes.set(id, regionSize(id)); });
    var sums = [];
    for (var r = 0; r < N; r++) {
      var s = 0;
      for (var c = 0; c < N; c++) s += sizes.get(assignments[idx(r, c)]);
      sums.push(s);
    }
    var answer = sums.reduce(function (acc, x) { return acc + x * x; }, 0);
    rowResults.innerHTML = sums.map(function (s, i) { return '<div>Row ' + (i + 1) + ': <b>' + s + '</b></div>'; }).join('');
    finalAnswer.textContent = 'Final answer: ' + answer.toLocaleString();
    resultPanel.hidden = false;
  }

  function renderBoard() {
    board.innerHTML = '';
    var analyses = new Map(allAnalyses().map(function (a) { return [a.id, a]; }));
    var sizes = new Map(regions.map(function (r) { return [r.id, regionSize(r.id)]; }));

    for (var r = 0; r < N; r++) {
      for (var c = 0; c < N; c++) {
        var i = idx(r, c);
        var id = assignments[i];
        var cell = document.createElement('div');
        cell.className = 'cell';
        cell.dataset.i = i;

        if (id == null) cell.classList.add('unassigned');
        else {
          var reg = regionById(id);
          if (reg) cell.style.background = reg.color;
          if (id === activeRegion) cell.classList.add('active-region');
        }

        if (c < N - 1 && assignments[i] !== assignments[idx(r, c + 1)]) cell.classList.add('boundary-r');
        if (r < N - 1 && assignments[i] !== assignments[idx(r + 1, c)]) cell.classList.add('boundary-b');

        var clue = CLUES[key1(r, c)];
        if (clue !== undefined) {
          var cd = document.createElement('div');
          cd.className = 'clue';
          cd.textContent = clue;
          cell.appendChild(cd);
          if (clueStatus[key1(r, c)] === 'good') cell.classList.add('clue-good');
          if (clueStatus[key1(r, c)] === 'bad') cell.classList.add('clue-bad');
        }

        if (id != null) {
          var sb = document.createElement('div');
          sb.className = 'size-badge';
          sb.textContent = sizes.get(id);
          cell.appendChild(sb);

          if (showIds.checked) {
            var ib = document.createElement('div');
            ib.className = 'id-badge';
            ib.textContent = 'S' + id;
            cell.appendChild(ib);
          }

          if (Number(manualCapitols[id]) === i) {
            var cap = document.createElement('div');
            cap.className = 'cap-badge';
            cap.textContent = 'C';
            cap.title = 'Player-marked capitol';
            cell.appendChild(cap);
          }
        }

        if (showDistances.checked && lastDistances && Number.isFinite(lastDistances[i])) {
          var db = document.createElement('div');
          db.className = 'dist-badge';
          db.textContent = lastDistances[i];
          db.title = 'Travel cost to nearest capitol';
          cell.appendChild(db);
        }
        board.appendChild(cell);
      }
    }
  }

  function renderStateList() {
    stateList.innerHTML = '';
    var visible = regions.filter(function (r) { return regionSize(r.id) > 0 || r.id === activeRegion; });
    if (!visible.length) {
      stateList.innerHTML = '<div class="tiny">No states yet.</div>';
      activeInfo.textContent = '';
      return;
    }

    visible.forEach(function (reg) {
      var a = analyzeRegion(reg.id);
      var item = document.createElement('div');
      item.className = 'state-item' + (reg.id === activeRegion ? ' active' : '');
      var capText = '';
      if (manualCapitols[reg.id] !== undefined) {
        var p = rc(Number(manualCapitols[reg.id]));
        capText = ' · marked C r' + (p[0] + 1) + 'c' + (p[1] + 1);
      }
      item.innerHTML = '<span class="swatch" style="background:' + reg.color + '"></span>' +
        '<div><b>State ' + reg.id + '</b><div class="state-meta">size ' + a.size + capText + '</div></div>' +
        '<div class="' + (a.valid ? 'status-good' : 'status-bad') + '">' + (a.valid ? 'valid' : '×') + '</div>' +
        '<button class="state-delete danger" type="button" title="Delete State ' + reg.id + '">Delete</button>';
      item.title = a.valid ? 'Connected and symmetric' : a.reason;
      item.addEventListener('click', function () { activeRegion = reg.id; render(); save(); });
      item.querySelector('.state-delete').addEventListener('click', function (ev) { ev.stopPropagation(); deleteRegion(reg.id); });
      stateList.appendChild(item);
    });

    if (activeRegion != null) {
      var a = analyzeRegion(activeRegion);
      activeInfo.innerHTML = 'Active: <b>State ' + activeRegion + '</b> · size ' + a.size + ' · <span class="' + (a.valid ? 'status-good' : 'status-warn') + '">' + (a.valid ? 'connected + symmetric' : a.reason) + '</span>';
    } else activeInfo.textContent = '';
  }

  function render() {
    renderBoard();
    renderStateList();
    document.getElementById('undo').disabled = !undoStack.length;
    document.getElementById('redo').disabled = !redoStack.length;
  }

  board.addEventListener('pointerdown', function (e) {
    var cell = e.target.closest('.cell');
    if (!cell) return;
    e.preventDefault();
    var i = Number(cell.dataset.i);

    if (e.shiftKey && assignments[i] != null) {
      activeRegion = assignments[i];
      dragging = false;
      render();
      save();
      return;
    }

    if (capitolMode && e.button !== 2) {
      dragging = false;
      toggleManualCapitol(i);
      return;
    }

    if (activeRegion == null && e.button !== 2) {
      activeRegion = nextId++;
      regions.push({ id: activeRegion, color: colorFor(activeRegion) });
    }

    pushUndo();
    dragging = true;
    dragErase = e.button === 2 || (e.button === 0 && assignments[i] === activeRegion);
    if (dragErase) erase(i); else paint(i, activeRegion);
    render();
    save();
    if (cell.setPointerCapture) cell.setPointerCapture(e.pointerId);
  });

  board.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    var el = document.elementFromPoint(e.clientX, e.clientY);
    var cell = el && el.closest ? el.closest('.cell') : null;
    if (!cell) return;
    var i = Number(cell.dataset.i);
    var changed = dragErase ? erase(i) : paint(i, activeRegion);
    if (changed) { render(); save(); }
  });

  window.addEventListener('pointerup', function () { dragging = false; });
  board.addEventListener('contextmenu', function (e) { e.preventDefault(); });

  document.getElementById('newState').addEventListener('click', addRegion);
  document.getElementById('check').addEventListener('click', validateAndCheck);
  capitolModeBtn.addEventListener('click', function () { setCapitolMode(!capitolMode); });
  document.getElementById('undo').addEventListener('click', function () {
    if (!undoStack.length) return;
    redoStack.push(snapshot());
    restore(undoStack.pop());
  });
  document.getElementById('redo').addEventListener('click', function () {
    if (!redoStack.length) return;
    undoStack.push(snapshot());
    restore(redoStack.pop());
  });
  document.getElementById('clear').addEventListener('click', function () {
    if (!window.confirm('Clear the entire board?')) return;
    pushUndo();
    assignments = Array(N * N).fill(null);
    regions = [];
    activeRegion = null;
    nextId = 1;
    manualCapitols = {};
    invalidate();
    summary.textContent = 'Board cleared.';
    render();
    save();
  });
  showIds.addEventListener('change', render);
  showDistances.addEventListener('change', render);

  document.addEventListener('keydown', function (e) {
    var tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    var key = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && key === 'z') {
      e.preventDefault();
      document.getElementById(e.shiftKey ? 'redo' : 'undo').click();
    } else if ((e.ctrlKey || e.metaKey) && key === 'y') {
      e.preventDefault();
      document.getElementById('redo').click();
    } else if (key === 'n') addRegion();
    else if (key === 'c') validateAndCheck();
    else if (key === 'm') setCapitolMode(!capitolMode);
  });

  function save() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot())); } catch (err) {}
  }

  function load() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      var s = JSON.parse(raw);
      if (Array.isArray(s.assignments) && s.assignments.length === N * N) {
        assignments = s.assignments;
        regions = s.regions || [];
        activeRegion = s.activeRegion == null ? null : s.activeRegion;
        nextId = s.nextId || 1;
        manualCapitols = Object.assign({}, s.manualCapitols || {});
      }
    } catch (err) {}
  }

  document.getElementById('exportBtn').addEventListener('click', function () {
    var data = JSON.stringify(snapshot());
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(data).then(function () {
        summary.textContent = 'Progress JSON copied to clipboard.';
      }).catch(function () { window.prompt('Copy this progress JSON:', data); });
    } else window.prompt('Copy this progress JSON:', data);
  });

  document.getElementById('importBtn').addEventListener('click', function () {
    var raw = window.prompt('Paste exported progress JSON:');
    if (!raw) return;
    try {
      var s = JSON.parse(raw);
      if (!Array.isArray(s.assignments) || s.assignments.length !== N * N) throw new Error('Bad board size');
      pushUndo();
      restore(s);
      summary.textContent = 'Progress imported.';
    } catch (err) {
      window.alert('Could not import that data.');
    }
  });

  setCapitolMode(false);
  load();
  render();
})();
