/* Saving a file through the browser's own download, under a chosen name. */

// Revoking an object URL at once can cancel the download before the browser
// has read the blob, so it is released a moment later.
const OBJECT_URL_RELEASE_MS = 1000

/** Downloads `href` (already authorised by the caller) as `filename`. */
export function saveFileAs(href, filename) {
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.style.display = 'none'
  document.body.append(link)
  link.click()
  link.remove()
}

/** Downloads in-memory content as `filename`. */
export function saveBlobAs(blob, filename) {
  const objectUrl = URL.createObjectURL(blob)
  saveFileAs(objectUrl, filename)
  setTimeout(() => URL.revokeObjectURL(objectUrl), OBJECT_URL_RELEASE_MS)
}
