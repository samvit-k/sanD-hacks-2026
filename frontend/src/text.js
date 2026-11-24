// Small text helpers shared by components.

/** "1 paper", "3 papers". */
export const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`