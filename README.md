# ffox-test

Control a [jlesage/firefox](https://hub.docker.com/r/jlesage/firefox) container from Python. Two channels, split by job:

| Job | Channel | When it connects |
|---|---|---|
| Browser chrome (open a URL via the address bar) | **RFB** / noVNC | On session enter |
| Page DOM (`click`, `fill`, `evaluate`, …) | **BiDi** | Lazily, on first DOM call |

No Playwright or Selenium.

## Firefox container

BiDi binds to `127.0.0.1` inside the container, so publish it with `socat`:

```bash
docker run -d \
  --name=firefox \
  -p 5770:5800 \
  -p 9222:9223 \
  -e KEEP_APP_RUNNING=1 \
  -e INSTALL_PACKAGES=socat \
  -e FF_CUSTOM_ARGS="--remote-debugging-port=9222 --remote-allow-hosts=* --remote-allow-origins=*" \
  -v /docker/appdata/firefox:/config:rw \
  jlesage/firefox

# after start (also auto-attempted on first BiDi use):
docker exec -d firefox socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222
```

## Usage

```python
from ffox_test import FirefoxSession

async with FirefoxSession() as ff:
    # RFB: address bar
    await ff.navigate("https://example.com/login", ready="#username")
    # BiDi: DOM (connects here if not already via `ready`)
    await ff.fill("#username", "user")
    await ff.fill("#password", "secret")
    await ff.click("button[type=submit]")
```

`navigate(..., ready="css")` is optional — it waits for a selector after the RFB load so BiDi only proceeds once the page is there.
