window.PUZZLE_CONFIG = Object.freeze({
  N: 11,
  STORAGE_KEY: 'state-puzzle-solver-v2',

  // Clues are keyed as "row,col" using 1-indexed coordinates.
  CLUES: Object.freeze({
    '1,2':8, '1,3':4, '1,6':11, '1,10':5,
    '2,8':11,
    '3,3':7, '3,6':1, '3,11':14,
    '4,1':28, '4,4':51, '4,7':1, '4,9':6,
    '5,2':22, '5,9':4, '5,11':1,
    '6,4':15, '6,6':10, '6,8':0,
    '7,1':11, '7,3':11, '7,10':9,
    '8,3':17, '8,5':14, '8,8':10, '8,11':13,
    '9,1':30, '9,6':6, '9,9':45,
    '10,4':10,
    '11,2':26, '11,6':0, '11,9':77, '11,10':61
  }),

  BASE_COLORS: Object.freeze([
    '#ffd6d6','#d6e8ff','#dcf6d6','#eadcff','#fff0c7','#d6f4f2','#f8d8ef','#e7e7c9',
    '#f3d4bd','#d8e1f5','#d9f0e5','#f0ddf7','#fde1c9','#dfead1','#e2ddf8','#f4e5b5',
    '#cfe8f3','#f7d7c4','#d3edcf','#e8d5ea','#f6efc4','#d4e8e8','#efd6df','#dde2c3'
  ])
});
