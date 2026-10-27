/*
 * ckanext-webpageviewplus: self-sizing for embedded pages.
 *
 * Contract with view.html: each .webpageviewplus-frame iframe carries
 *   data-frame-origin  the origin of its src ("" if unknown)
 *   data-opaque        "true" when sandboxed without allow-same-origin: its
 *                      messages arrive with origin "null", and replies must
 *                      be posted to "*"
 *   data-height-mode   only "auto" frames are resized
 *   data-min-height / data-max-height  clamp, in px
 *
 * Messages a framed page may send (plain objects, posted to "*"):
 *   {type: "webpageviewplus:resize", height: px}   any page
 *   {qsvVizHeight: px}                             qsv Data Schematics
 *   {qsvVizWantViewport: 1}  -> reply {qsvVizViewport: window.innerHeight}
 *                               (also sent unasked when each frame loads)
 *   {qsvVizReveal: "bottom"} / {qsvVizReveal: "top", qsvVizOffset: px}
 * See the qsv wiki, Visualization#embedding.
 *
 * A message counts only if it comes from one of these frames' windows, and
 * its origin is the frame's (or "null" for an opaque frame). Plain script, no
 * dependencies and nothing inline, so a site CSP without 'unsafe-inline' works.
 */
(function () {
  "use strict";

  var frames = [];

  function stateFor(source) {
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].iframe.contentWindow === source) return frames[i];
    }
    return null;
  }

  function intAttr(el, name, fallback) {
    var value = parseInt(el.getAttribute(name), 10);
    return isFinite(value) ? value : fallback;
  }

  function setup(iframe) {
    var state = {
      iframe: iframe,
      origin: iframe.getAttribute("data-frame-origin") || "",
      opaque: iframe.getAttribute("data-opaque") === "true",
      auto: iframe.getAttribute("data-height-mode") === "auto",
      min: intAttr(iframe, "data-min-height", 0),
      max: intAttr(iframe, "data-max-height", 100000),
      height: null
    };
    frames.push(state);
    // A schematic asks for the viewport once, on its load, which can happen
    // before this script runs. So also send it unasked: when the frame
    // finishes loading (its listeners are in place by then) and now. It's
    // just window.innerHeight, harmless to any page.
    iframe.addEventListener("load", function () { replyViewport(state); });
    replyViewport(state);
  }

  function trusted(state, event) {
    if (state.opaque) return event.origin === "null";
    return state.origin !== "" && event.origin === state.origin;
  }

  function resize(state, value) {
    if (!state.auto) return;
    var height = Math.ceil(Number(value));
    if (!isFinite(height) || height <= 0) return;
    height = Math.min(Math.max(height, state.min), state.max);
    if (state.height !== null && Math.abs(height - state.height) <= 1) return;
    var rect = state.iframe.getBoundingClientRect();
    var before = rect.height;
    state.height = height;
    state.iframe.style.height = height + "px";
    // Keep the reader's place when a frame above them changes height.
    if (rect.bottom <= 0) window.scrollBy(0, height - before);
  }

  function replyViewport(state) {
    var target = state.opaque ? "*" : state.origin;
    if (!target || !state.iframe.contentWindow) return;
    state.iframe.contentWindow.postMessage(
      {qsvVizViewport: window.innerHeight}, target);
  }

  function reveal(state, where, offset) {
    var rect = state.iframe.getBoundingClientRect();
    if (where === "bottom") {
      if (rect.bottom > window.innerHeight) {
        window.scrollBy({top: rect.bottom - window.innerHeight, behavior: "instant"});
      }
    } else if (where === "top") {
      var y = Number(offset);
      y = isFinite(y) ? Math.min(Math.max(y, 0), rect.height) : 0;
      window.scrollTo({
        top: window.scrollY + rect.top + y - 80,
        behavior: "instant"
      });
    }
  }

  function onMessage(event) {
    var state = stateFor(event.source);
    if (!state || !trusted(state, event)) return;
    var data = event.data;
    if (data === null || typeof data !== "object") return;

    if (data.type === "webpageviewplus:resize") resize(state, data.height);
    if ("qsvVizHeight" in data) resize(state, data.qsvVizHeight);
    if ("qsvVizWantViewport" in data) replyViewport(state);
    if ("qsvVizReveal" in data) reveal(state, data.qsvVizReveal, data.qsvVizOffset);
  }

  var resizeTimer = null;
  function onWindowResize() {
    if (resizeTimer) return;
    resizeTimer = setTimeout(function () {
      resizeTimer = null;
      // To every frame, not only those seen asking: a schematic may have
      // asked before this script ran. It's just the window height.
      frames.forEach(replyViewport);
    }, 100);
  }

  function stateForWrapper(wrapper) {
    for (var i = 0; i < frames.length; i++) {
      if (wrapper.contains(frames[i].iframe)) return frames[i];
    }
    return null;
  }

  function toggleFullscreen(wrapper) {
    if (document.fullscreenElement === wrapper) {
      document.exitFullscreen();
    } else if (wrapper.requestFullscreen) {
      wrapper.requestFullscreen();
    }
  }

  function onFullscreenChange() {
    var buttons = document.querySelectorAll('[data-webpageviewplus-action="fullscreen"]');
    for (var i = 0; i < buttons.length; i++) {
      var wrapper = buttons[i].closest(".webpageviewplus");
      buttons[i].setAttribute(
        "aria-pressed", String(document.fullscreenElement === wrapper));
    }
  }

  function setupControls() {
    var buttons = document.querySelectorAll("[data-webpageviewplus-action]");
    for (var i = 0; i < buttons.length; i++) {
      var button = buttons[i];
      var action = button.getAttribute("data-webpageviewplus-action");
      // iPhone Safari has no element fullscreen: leave the button hidden.
      if (action === "fullscreen" && !document.fullscreenEnabled) continue;
      button.hidden = false;
      button.addEventListener("click", function (event) {
        var target = event.currentTarget;
        var wrapper = target.closest(".webpageviewplus");
        if (!wrapper) return;
        if (target.getAttribute("data-webpageviewplus-action") === "fullscreen") {
          toggleFullscreen(wrapper);
          return;
        }
        var state = stateForWrapper(wrapper);
        var url = target.getAttribute("data-reload-url");
        if (state && url) {
          state.height = null;
          state.iframe.src = url;
        }
      });
    }
    document.addEventListener("fullscreenchange", onFullscreenChange);
  }

  // View form: warn as the editor types a page URL that refuses framing.
  function setupFrameabilityCheck() {
    var status = document.querySelector("[data-webpageviewplus-frameability]");
    var input = document.getElementById("field-page_url");
    if (!status || !input || !window.fetch) return;
    var timer = null;
    var current = 0;
    function run() {
      var url = input.value.trim();
      var request = ++current;
      status.textContent = "";
      status.classList.remove("alert", "alert-warning");
      if (!url) return;
      var query = "?resource_id=" + encodeURIComponent(status.getAttribute("data-resource-id")) +
        "&url=" + encodeURIComponent(url);
      fetch(status.getAttribute("data-api") + query, {credentials: "same-origin"})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (body) {
          if (request !== current || !body || !body.success) return;
          var result = body.result;
          var text = "";
          if (result.verdict === "blocked") {
            text = status.getAttribute("data-blocked").replace("{reason}", result.reason);
          } else if (result.verdict === "unknown") {
            text = status.getAttribute("data-unknown");
          }
          if (text) {
            status.textContent = text;
            status.classList.add("alert", "alert-warning");
          }
        })
        .catch(function () {});
    }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(run, 700);
    });
    run();
  }

  function init() {
    setupFrameabilityCheck();
    var nodes = document.querySelectorAll("iframe.webpageviewplus-frame");
    for (var i = 0; i < nodes.length; i++) setup(nodes[i]);
    setupControls();
    if (!frames.length) return;
    window.addEventListener("message", onMessage);
    window.addEventListener("resize", onWindowResize);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
