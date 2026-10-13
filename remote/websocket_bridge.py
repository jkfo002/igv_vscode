"""websockify entrypoint that keeps URL tokens out of access logs."""

import re
from websockify.websocketproxy import ProxyRequestHandler, websockify_init

original = ProxyRequestHandler.log_message


def redacted(self, fmt, *args):
    message = fmt % args
    message = re.sub(r"([?&]token=)[^\s&\"']+", r"\1[redacted]", message)
    message = re.sub(r"\b[a-f0-9]{64}\b", "[redacted]", message)
    original(self, "%s", message)


ProxyRequestHandler.log_message = redacted

if __name__ == "__main__":
    websockify_init()
