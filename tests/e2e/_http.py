"""One keep-alive HTTP session for the e2e tests' direct API calls.

A bare ``requests.get`` opens a socket per call and leaves its loopback port in
TIME_WAIT; one session reuses a connection, so the setup and cleanup calls a
test makes against the disposable webapp cost one socket for the whole run.
"""

from __future__ import annotations

import requests

# The disposable webapp's cert is for the .ts.net name, not loopback.
SESSION = requests.Session()
SESSION.verify = False
