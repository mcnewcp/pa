"""Stop hook: drops the exchange that just ended into the inbox as an `assistant_chat` payload.

Claude Code runs it outside the tool sandbox after each reply, with the hook input on stdin.
It does nothing unless the session's environment turns capture on (PA_CAPTURE_EXCHANGES=1)
and names the inbox directory (PA_CAPTURE_INBOX). See pa_core.exchange_capture.
"""

import os
import sys

from pa_core.exchange_capture import run_stop_hook

sys.exit(run_stop_hook(sys.stdin.buffer.read(), os.environ))
