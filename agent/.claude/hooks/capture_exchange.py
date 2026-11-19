"""The capture hooks' code: holds the owner's prompt, then writes the exchange into the inbox.

In the home VM, the capture helper (/usr/local/libexec/pa-capture) runs it as the capture user,
the inbox's owner, for each of the managed settings' capture hooks: `prompt`, `answer` or
`stop`, with the hook's event on stdin and the inbox and its state directory as options. See
pa_core.exchange_capture.
"""

import sys

from pa_core.exchange_capture import run_helper

sys.exit(run_helper(sys.argv[1:], sys.stdin.buffer))
