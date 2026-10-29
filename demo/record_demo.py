"""Record docs/demo.gif from the dev site (http://localhost:5001), after
demo/seed_features.py and demo/seed_schematics.py have run. Logs in with the
sysadmin from ckan-docker's .env (CKAN_DOCKER_ENV, default
~/GitHub/ckan-docker/.env) in a context that isn't recorded. Run on the host:

    uv run --with playwright --no-project python demo/record_demo.py /tmp/wvp-video

then turn the video it prints into the GIF:

    ffmpeg -ss 1.2 -i VIDEO.webm -vf "fps=6,scale=720:-1:flags=lanczos,split[a][b];\
[a]palettegen=max_colors=80:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
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
    schematic = view_url("wvp-schematics", "Allegheny County dog licences: Data Schematic",
                         "Data Schematic (self-sizing)")
    strict = view_url("wvp-features", "Wikipedia: Open data",
                      "Strict preset, 70% of the window")
    interactive = view_url("wvp-features", "Datawrapper chart",
                           "Interactive preset, fixed 500 px")
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

        def frame_box():
            return page.locator("iframe").first.bounding_box()

        # 1. data-schematic: an uploaded qsv Data Schematic sizes itself to fit.
        page.goto(schematic)
        page.frame_locator("iframe").first.locator(".js-plotly-plot").first.wait_for()
        page.wait_for_timeout(1200)
        caption(page, "Data Schematic preset: 50,000 dog licences as one uploaded qsv page, "
                      "sandboxed and sized to fit")
        page.wait_for_timeout(1800)
        scroll(page, 3300, steps=70, pause=80)
        page.wait_for_timeout(1000)

        # 2. strict: a third-party page with nothing running.
        page.goto(strict)
        page.wait_for_timeout(1500)
        caption(page, "Strict preset: Wikipedia with no scripts, forms or popups")
        scroll(page, 330, steps=10, pause=40)
        page.wait_for_timeout(800)
        box = frame_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        scroll(page, 900, steps=20, pause=60)
        page.wait_for_timeout(1500)

        # 3. interactive: scripts run, but the page has no origin of its own.
        page.goto(interactive)
        page.wait_for_timeout(2500)
        caption(page, "Interactive preset: a Datawrapper chart's scripts run, "
                      "with no access to cookies, storage or CKAN")
        scroll(page, 330, steps=10, pause=40)
        page.wait_for_timeout(600)
        box = frame_box()
        for i in range(36):
            page.mouse.move(box["x"] + box["width"] * (0.15 + 0.7 * i / 35),
                            box["y"] + box["height"] * 0.55)
            page.wait_for_timeout(90)
        page.wait_for_timeout(1200)

        # 4. external: a trusted site, behind an optional consent card whose
        # "Always load" is remembered in this browser.
        page.goto(consent)
        page.wait_for_timeout(1000)
        caption(page, "External preset for trusted sites like YouTube, behind a consent "
                      "card: nothing loads until you choose")
        scroll(page, 330, steps=10, pause=40)
        page.wait_for_timeout(2600)
        page.get_by_role("button", name="Always load from www.youtube-nocookie.com").click()
        page.wait_for_timeout(2500)
        box = frame_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(4000)
        page.goto(consent)
        page.wait_for_timeout(800)
        caption(page, "\"Always load\" is remembered in this browser only, "
                      "with \"Ask me again\" to undo it")
        page.locator("[data-webpageviewplus-remembered]").scroll_into_view_if_needed()
        page.mouse.wheel(0, 120)
        page.wait_for_timeout(3200)

        # 5. The view form recognises well-known services.
        page.goto(form)
        page.wait_for_timeout(800)
        caption(page, "Paste a link from 12 well-known services, from YouTube to Power BI: "
                      "the form suggests the embed and preset")
        field = page.locator("#field-page_url")
        field.scroll_into_view_if_needed()
        page.mouse.wheel(0, 150)
        field.fill("")
        field.press_sequentially("https://vimeo.com/76979871", delay=45)
        suggestion = page.locator("[data-webpageviewplus-service]")
        suggestion.get_by_role("button", name="Use them").wait_for()
        page.wait_for_timeout(1800)
        suggestion.get_by_role("button", name="Use them").click()
        page.wait_for_timeout(3000)

        video = page.video.path()
        ctx.close()
        browser.close()
    print(video)


if __name__ == "__main__":
    main()
