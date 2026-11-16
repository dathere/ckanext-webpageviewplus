#!/bin/sh
# Does Gotenberg with CHROMIUM_DENY_PRIVATE_IPS=true refuse a page that
# redirects (301, 302, 303, 307, 308) to a private address? Its docs don't
# say, and an allow-list can't be used to test it: an allow-list filters
# every redirect hop itself. So the redirector sits on a Docker network
# with a public-range subnet (11.0.0.0/24, local to Docker), and the page
# it redirects to on an ordinary private one. An unprotected Gotenberg
# beside it shows the check isn't vacuous. Needs Docker; run from anywhere:
#   sh scripts/gotenberg_redirects/check.sh
set -eu
VERSION="${GOTENBERG_VERSION:-9.5.1}"
DIR="$(cd "$(dirname "$0")" && pwd)"
P=wvp-redir

cleanup() {
  docker rm -f "$P-secret" "$P-redirector" "$P-safe" "$P-unsafe" >/dev/null 2>&1 || true
  docker network rm "$P-private" "$P-public" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

docker network create "$P-private" >/dev/null
docker network create --subnet 11.0.0.0/24 "$P-public" >/dev/null
docker run -d --name "$P-secret" --network "$P-private" -v "$DIR:/s:ro" \
  python:3.12-alpine python /s/fixture.py black >/dev/null
docker run -d --name "$P-redirector" --network "$P-public" -v "$DIR:/s:ro" \
  python:3.12-alpine python /s/fixture.py redirect "http://$P-secret:8000/" >/dev/null
docker create --name "$P-safe" --network "$P-private" \
  -e CHROMIUM_DENY_PRIVATE_IPS=true "gotenberg/gotenberg:$VERSION" >/dev/null
docker create --name "$P-unsafe" --network "$P-private" "gotenberg/gotenberg:$VERSION" >/dev/null
for g in safe unsafe; do
  docker network connect "$P-public" "$P-$g"
  docker start "$P-$g" >/dev/null
done
docker run --rm --network "$P-private" -v "$DIR:/s:ro" -e PREFIX="$P" python:3.12-slim \
  sh -c "pip install -q --disable-pip-version-check requests pillow >/dev/null 2>&1 && python /s/check.py"
