"""Record docs/demo.gif from the dev site (http://localhost:5001), after
demo/seed_features.py and demo/seed_schematics.py have run. Logs in with the
sysadmin from ckan-docker's .env (CKAN_DOCKER_ENV, default
~/GitHub/ckan-docker/.env) in a context that isn't recorded. Run on the host:

    uv run --with playwright --no-project python demo/record_demo.py /tmp/wvp-video

then turn the video it prints into the GIF:

    ffmpeg -ss 1.2 -i VIDEO.webm -vf "fps=8,scale=800:-1:flags=lanczos,split[a][b];\
[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
      -loop 0 docs/demo.gif
"""
import json
import os
import shutil
import sys
import urllib.request

from playwright.sync_api import sync_playwright

B = "http://localhost:5001"
OUT = sys.argv[1] if len(sys.argv) > 1 else "wvp-video"
W, H = 1280, 800
ENV = dict(l.strip().split("=", 1) for l in open(os.path.expanduser(
    os.environ.get("CKAN_DOCKER_ENV", "~/GitHub/ckan-docker/.env")))
    if "=" in l and not l.startswith("#"))


def api(action, **params):
    q = "&".join("{}={}".format(k, v) for k, v in params.items())
    return json.load(urllib.request.urlopen("{}/api/3/action/{}?{}".format(B, action, q)))["result"]


def view_url(dataset, resource_name, view_title, edit=False):
    pkg = api("package_show", id=dataset)
    res = [r for r in pkg["resources"] if r["name"] == resource_name][0]
    view = [v for v in api("resource_view_list", id=res["id"]) if v["title"] == view_title][0]
    if edit:
        return "{}/dataset/{}/resource/{}/edit_view/{}".format(B, dataset, res["id"], view["id"])
    return "{}/dataset/{}/resource/{}?view_id={}".format(B, dataset, res["id"], view["id"])


CAPTION_JS = """text => {
  let c = document.getElementById('wvp-demo-caption');
  if (!c) {
    c = document.createElement('div'); c.id = 'wvp-demo-caption';
    c.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:14px 24px;' +
      'background:rgba(17,24,39,.92);color:#fff;font:600 20px/1.35 system-ui,sans-serif;' +
      'text-align:center;box-shadow:0 -2px 12px rgba(0,0,0,.25)';
    document.body.appendChild(c);
  }
  c.textContent = text;
}"""


def caption(page, text):
    page.evaluate(CAPTION_JS, text)


def scroll(page, total, steps=40, pause=60):
    for _ in range(steps):
        page.mouse.wheel(0, total / steps)
        page.wait_for_timeout(pause)


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    schematic = view_url("wvp-schematics", "NYC 311 requests: Data Schematic",
                         "Data Schematic (self-sizing)")
    upload = view_url("wvp-features", "Uploaded HTML on the content origin",
                      "Data Schematic preset, self-sizing")
    consent = view_url("wvp-features", "YouTube video", "Ask before loading")
    form = view_url("wvp-features", "Try the view form", "Paste a link to see a suggestion",
                    edit=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        login = browser.new_context()
        page = login.new_page()
        page.goto(B + "/user/login")
        page.fill("#field-login", ENV["CKAN_SYSADMIN_NAME"])
        page.fill("#field-password", ENV["CKAN_SYSADMIN_PASSWORD"])
        page.press("#field-password", "Enter")
        page.wait_for_load_state()
        state = login.storage_state()
        login.close()

        ctx = browser.new_context(viewport={"width": W, "height": H}, storage_state=state,
                                  record_video_dir=OUT, record_video_size={"width": W, "height": H})
        page = ctx.new_page()

        # 1. A Data Schematic sizes itself to fit.
        page.goto(schematic)
        page.frame_locator("iframe").first.locator(".js-plotly-plot").first.wait_for()
        page.wait_for_timeout(1500)
        caption(page, "An uploaded qsv Data Schematic, sandboxed, sized to fit its content")
        page.wait_for_timeout(2000)
        scroll(page, 3200, steps=50, pause=70)
        page.wait_for_timeout(800)
        page.mouse.wheel(0, -10000)
        page.wait_for_timeout(1200)

        # 2. Uploaded HTML runs on the content origin, not CKAN's.
        page.goto(upload)
        page.wait_for_timeout(1500)
        caption(page, "Uploaded HTML runs on a separate content origin: it can't act as CKAN")
        scroll(page, 300, steps=10, pause=40)
        page.wait_for_timeout(3500)

        # 3. Consent before a third-party page loads anything.
        page.goto(consent)
        page.wait_for_timeout(1000)
        caption(page, "Optional consent card: nothing loads from YouTube until you choose")
        scroll(page, 300, steps=10, pause=40)
        page.wait_for_timeout(2500)
        page.get_by_role("button", name="Load").first.click()
        page.wait_for_timeout(3500)

        # 4. The view form recognises well-known services.
        page.goto(form)
        page.wait_for_timeout(800)
        caption(page, "Paste a YouTube, Vimeo, Datawrapper or Flourish link: the form suggests the embed")
        field = page.locator("#field-page_url")
        field.scroll_into_view_if_needed()
        page.mouse.wheel(0, 150)
        field.fill("")
        field.press_sequentially("https://vimeo.com/76979871", delay=45)
        suggestion = page.locator("[data-webpageviewplus-service]")
        suggestion.get_by_role("button", name="Use them").wait_for()
        page.wait_for_timeout(2200)
        suggestion.get_by_role("button", name="Use them").click()
        page.wait_for_timeout(3500)

        video = page.video.path()
        ctx.close()
        browser.close()
    print(video)


if __name__ == "__main__":
    main()
