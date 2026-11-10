# Photo upload status

A photo in the Capture strip has a short lifecycle: it is added, uploaded, and ends on the server or failed. The lifecycle is declared once in `app/webapp/static/photo_state.js` (`PhotoStatus`, `derivePhotoStatus`, `setPhotoStatus` and the move table) and registered in `.fleet.toml` as the `photo-upload` state machine. Every reader asks that module; none spells a status or tests a pair of them.

## Why it is declared

The status lived as a bare string on each photo object, written in `uploadPhoto` and compared in a dozen places: Extract's "ready" filter, the header's "Uploading N" and "N failed" parts, the thumbnail's CSS class, the guard that holds a reorder until uploads finish. Each comparison re-spelled the same four names, and `pending || uploading` was written out wherever "still on its way" mattered, so a fifth status would have been handled in some places and missed in others.

## The statuses

The values are also the CSS classes on the thumbnail (`.thumb.pending`, `.uploading`, `.ready`, `.failed`), so nothing in the stylesheet changes.

| Status | Meaning |
|---|---|
| `pending` | added; the upload has not started (a photo with no status reads as this) |
| `uploading` | the POST is in flight |
| `ready` | the server holds the photo; `seq` says where |
| `failed` | the upload failed; `error` says why |

## Legal moves

| From | To |
|---|---|
| `pending` | `uploading` |
| `uploading` | `ready`, `failed` |
| `ready`, `failed` | nothing: retaking makes a new photo, it does not rewind this one |

`setPhotoStatus(photo, next)` is the only writer of `photo.status`. A move the table does not list, or a status string this build does not know, throws `IllegalPhotoTransition` before anything is stored. `derivePhotoStatus(photo)` reads it back; an unknown stored string throws rather than rendering as `pending`.

## Who reads it

| Reader | Uses |
|---|---|
| Extract (`extract.js`), the order sync and the delete path (`capture.js`) | `isPhotoReady` |
| Header context line (`renderHeadline`) | `isPhotoFailed` and `isPhotoUploading` |
| Reorder guard (`movePhoto`) | `isPhotoUploading` |
| Thumbnail class (`renderThumbnails`) | `derivePhotoStatus` |

A new reader calls one of these predicates and extends `PhotoStatus` (and the table) if it needs a mode they lack.

## Proof

`tests/test_client_state_machines.py` drives the module through Node: a ready photo refused a move back to `uploading` and left untouched (the `state-machine-proof: photo-upload` marker), the legal upload path, the failed-is-final and skipped-step refusals, and the derive table including the unknown string.
