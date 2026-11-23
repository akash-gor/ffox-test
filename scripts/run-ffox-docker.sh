#!/usr/bin/env bash
# Start jlesage/firefox with WebDriver BiDi exposed on host :9222, then navigate.
set -euo pipefail

# 1. change port to one in allowlist (noVNC / web UI)
PORT=5770  # change to your allowlist

    # run this on your own machine with the above port value subbed in:
    # if you don't have 'aristotle' in your ~/.ssh/config, replace with $USER@{path to pem file}
    # "ssh -L $PORT:localhost:$PORT aristotle" > /dev/null 2>&1

# 2. remove any existing firefox container
cid=$(docker ps -aq --filter "name=^/firefox$") && [ -n "$cid" ] && docker rm -f "$cid"

# 3. run the firefox container (BiDi on 127.0.0.1:9222 inside → socat 9223 → host 9222)
docker run -d \
    --name=firefox \
    -p "${PORT}:5800" \
    -p 9222:9223 \
    -e KEEP_APP_RUNNING=1 \
    -e INSTALL_PACKAGES=socat \
    -e FF_CUSTOM_ARGS="--remote-debugging-port=9222 --remote-allow-hosts=* --remote-allow-origins=*" \
    -v /docker/appdata/firefox:/config:rw \
    jlesage/firefox

# Wait briefly for Firefox + package install, then publish BiDi
# replace: sleep 3
echo "waiting for noVNC on :${PORT} ..."
for i in $(seq 1 60); do
  if curl -sf -o /dev/null "http://127.0.0.1:${PORT}/"; then
    break
  fi
  sleep 1
done
docker exec -d firefox socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 || true

# 4. check if we're in the ffox-test project root
dirname=$(basename "$(pwd)")
if [ "$dirname" != "ffox-test" ]; then
    echo "Not in ffox-test project root, navigate to that directory and run the script again" >&2
    exit 1
fi

sleep 1

# 5. run the test
uv run ffox-test "https://airtable.com/login"
