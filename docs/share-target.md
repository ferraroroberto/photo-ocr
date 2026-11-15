# Share-sheet target

The installed app is an Android share-sheet target for images: share a screenshot or gallery photos to **Photo OCR** and they open as a new take on the Capture tab, ready for **Extract**.

## How a share travels

1. `app/webapp/static/manifest.webmanifest` declares a `share_target`: a multipart `POST` to `/share-target` with the images in the `photos` field.
2. The share sheet sends that POST as a navigation, with no bearer token (the token lives in the page's `localStorage`, which a share cannot reach). So the server never takes the photos.
3. The service worker `app/webapp/static/sw.js`, served at `/sw.js` so its scope covers the whole app, answers the POST in the browser: it parks the files in Cache Storage (`photo-ocr-share`) and redirects to `/?share-target=photos`.
4. On boot the page (`app/webapp/static/share_inbox.js`, wired in `main.js`) strips the outcome from the URL, switches to Capture, reads the parked files back in share order, empties the cache and hands them to the normal Capture upload, which is signed in. A share that waits on the sign-in sheet stays parked until sign-in completes.

The worker handles nothing else: every other request goes to the network, so there is no offline shell and a deploy is never hidden behind a stale cache.

## When the worker is not there

A share that arrives before the worker is installed (the app was installed but never opened since this feature shipped) reaches the server's `POST /share-target`. That route is auth-exempt, reads no body, stores nothing and redirects to `/?share-target=missed`; the page shows a notice to share again. An empty share or one the worker could not read shows its own notice.

## Requirements and limits

- Android Chrome or another browser that supports Web Share Target, with the app installed to the home screen. iOS has no share-target support for web apps.
- Chrome refreshes an installed app's manifest on its own schedule, so an install from before this feature may need a reinstall, or a few days, before **Photo OCR** appears in the share sheet.
- A share larger than the take's photo cap keeps the first photos up to the cap, the same as **Choose photos**.
- The e2e suite blocks service workers by default (a controlled page's requests would bypass `page.route` mocks); `tests/e2e/test_share_target.py` opens its own context that allows them.
