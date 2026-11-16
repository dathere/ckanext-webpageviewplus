"""Run by check_gotenberg_redirects.sh, on the private network: asks both
Gotenberg instances to screenshot the redirector and its redirects, and
judges each picture by how much of it is black (only the fixture's pages
are)."""
import io
import os
import sys
import time

import requests
from PIL import Image

PREFIX = os.environ["PREFIX"]
REDIRECTOR = "http://{}-redirector:8000/".format(PREFIX)
CODES = ("301", "302", "303", "307", "308")


def wait(base):
    for _ in range(90):
        try:
            if requests.get(base + "/health", timeout=2).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    sys.exit(base + " never came up")


def black(base, url):
    """The share of near-black pixels in Gotenberg's screenshot of url, or
    None when Gotenberg refused it outright."""
    answer = requests.post(base + "/forms/chromium/screenshot/url", timeout=90, files={
        "url": (None, url), "width": (None, "800"), "height": (None, "600"),
        "clip": (None, "true"), "format": (None, "png")})
    if answer.status_code != 200:
        return None
    image = Image.open(io.BytesIO(answer.content)).convert("L")
    histogram = image.histogram()
    return sum(histogram[:40]) / sum(histogram)


def main():
    safe = "http://{}-safe:3000".format(PREFIX)
    unsafe = "http://{}-unsafe:3000".format(PREFIX)
    for base in (safe, unsafe):
        wait(base)
    problems = []

    def expect(name, base, url, reached):
        share = black(base, url)
        got = share is not None and share > 0.9
        print("{:7} {:24} {}".format(name, url[len(REDIRECTOR) - 1:] or "/",
                                     "reached" if got else "refused"))
        if got != reached:
            problems.append("{} {}: expected {}".format(
                name, url, "reached" if reached else "refused"))

    # The redirector is on a public-range network: both must reach it, or
    # every "refused" below would prove nothing.
    expect("safe", safe, REDIRECTOR + "ok", True)
    expect("unsafe", unsafe, REDIRECTOR + "ok", True)
    for code in CODES:
        # The unprotected one follows each redirect to the private page...
        expect("unsafe", unsafe, REDIRECTOR + code, True)
        # ...and the protected one must not.
        expect("safe", safe, REDIRECTOR + code, False)
    if problems:
        sys.exit("FAILED:\n  " + "\n  ".join(problems))
    print("Gotenberg with CHROMIUM_DENY_PRIVATE_IPS refuses every redirect to a private address.")


main()
