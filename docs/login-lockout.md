# Login lock-out and the guessing alert

`POST /api/login` is the one route a caller can reach with no credential, so it limits guesses and tells the owner when someone is guessing. The policy lives in the vendored `src/login_lockout.py`; the wiring is `app/webapp/routers/auth.py` and `app/webapp/login_alert.py`. The password check, the `503` replies, the token and the success reply are unchanged.

## Policy

- Each client gets five free wrong passwords. Every further one makes that client wait 2 s, doubling to a 300 s cap. The sixth wrong password is therefore the first to earn a wait, and the seventh attempt is the first refused.
- A refused attempt answers `429` with a numeric `Retry-After` before the password is read, and does not add to the count.
- A correct password, or an hour with no failure, clears a client's streak.
- No limit spans clients, so nobody can lock the owner out. State is in memory; a restart clears it.

## Who the client is

Behind the Cloudflare tunnel every public request reaches the app from loopback, so a loopback socket does not mean the PC. `app/webapp/middleware.py` decides:

- **Key:** on a loopback socket, the tunnel's `Cf-Connecting-Ip` header (an unparseable value shares one `tunnel-unknown` bucket); otherwise the socket address. The header is ignored from any non-loopback peer, so a remote caller cannot rotate it to dodge the lock.
- **Exempt:** a loopback socket with no tunnel header, which is the PC itself. It is never locked and its failures are never counted.

The bearer-token gate keeps its existing loopback bypass; only the login route uses this identity.

## The alert

Three wrong passwords in a row from one client raise one alert per streak, with a global five-minute cooldown (held-back alerts are counted into the next). The text names the app, the client address and the count, never a password or the token. It is sent from a thread through the vendored `src/notify` Telegram notifier, so a login never waits on Telegram.

Configure it in the gitignored `config/webapp_config.json`: `telegram_bot_token` and `telegram_alert_chat_id` (the fleet's coding-alerts chat). Either empty leaves the alert in the log only. Neither value is returned by `/api/config`.

## Tests

`tests/test_webapp_login_lockout.py` drives the route with a fake clock and a fake notifier: the `401`s then `429`, the correct password after the wait, one alert per streak, the PC exemption, tunnel keying and the header spoof case.
