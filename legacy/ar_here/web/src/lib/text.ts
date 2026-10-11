/**
 * Joins final + provisional words and keeps only the newest ~two lines.
 * Returns the display string and how many leading characters are final, so
 * the renderer can dim the provisional tail.
 */
export function captionDisplay(text: string, pending: string, maxChars: number) {
  const stable = text.trim()
  const tail = pending.trim()
  const full = stable && tail ? `${stable} ${tail}` : stable || tail
  if (full.length <= maxChars) {
    return { display: full, stableLen: stable.length }
  }
  // Cut at a word boundary and mark the cut with an ellipsis.
  let cut = full.length - maxChars
  const space = full.indexOf(' ', cut)
  cut = space === -1 ? cut : space + 1
  const display = '…' + full.slice(cut)
  const stableLen = 1 + Math.max(0, stable.length - cut)
  return { display, stableLen }
}
