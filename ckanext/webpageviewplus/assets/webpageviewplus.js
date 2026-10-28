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
    iframe.addEventListener("load", function () {
      replyViewport(state);
      announceLoaded(iframe);
    });
    replyViewport(state);
    setupNarrow(state);
    setupShield(state);
  }

  // Responsive height: the breakpoint is a site setting, which a container
  // query can't read, so the class is set here and the CSS does the rest.
  function setupNarrow(state) {
    var wrapper = state.iframe.closest(".webpageviewplus");
    var limit = wrapper ? intAttr(wrapper, "data-narrow-width", 0) : 0;
    if (!limit || !window.ResizeObserver) return;
    new ResizeObserver(function () {
      // Next frame: the class changes the wrapper's height, which would
      // re-trigger this observer within the same frame (a loop error).
      window.requestAnimationFrame(function () {
        var narrow = wrapper.clientWidth < limit;
        if (wrapper.classList.contains("webpageviewplus-narrow") !== narrow) {
          wrapper.classList.toggle("webpageviewplus-narrow", narrow);
        }
      });
    }).observe(wrapper);
  }

  // Tap to interact: on a touch screen a frame taller than half the screen
  // would trap the reader's scrolling, so a shield covers it until tapped,
  // and comes back once the frame has left the viewport.
  var coarse = window.matchMedia ? window.matchMedia("(pointer: coarse)") : null;

  function setupShield(state) {
    var wrapper = state.iframe.closest(".webpageviewplus");
    var shield = wrapper && wrapper.querySelector("[data-webpageviewplus-shield]");
    if (!shield || !coarse || !window.IntersectionObserver) return;
    var iframe = state.iframe;
    // The reader tapped through; cleared once the frame leaves the viewport.
    var dismissed = false;
    function update() {
      var eligible = coarse.matches && iframe.offsetHeight > window.innerHeight / 2;
      if (eligible && !dismissed) {
        shield.style.top = iframe.offsetTop + "px";
        shield.style.height = iframe.offsetHeight + "px";
      }
      shield.hidden = !eligible || dismissed;
    }
    shield.addEventListener("click", function () {
      dismissed = true;
      update();
    });
    new IntersectionObserver(function (entries) {
      if (!entries[entries.length - 1].isIntersecting) {
        dismissed = false;
        update();
      }
    }).observe(iframe);
    // Eligibility changes with the frame's height and the window's (e.g. a
    // phone turned sideways).
    if (window.ResizeObserver) new ResizeObserver(update).observe(iframe);
    window.addEventListener("resize", update);
    if (coarse.addEventListener) coarse.addEventListener("change", update);
    update();
  }

  // The view's live region says when its page has loaded. A frame that
  // finished loading before this script ran isn't announced: there's no way
  // to tell for a cross-origin frame. Cleared first, so a Reload is
  // announced again.
  function announceLoaded(iframe) {
    var wrapper = iframe.closest(".webpageviewplus");
    var status = wrapper && wrapper.querySelector("[data-webpageviewplus-status]");
    if (!status) return;
    status.textContent = "";
    setTimeout(function () {
      status.textContent = status.getAttribute("data-loaded") || "";
    }, 100);
  }

  function trusted(state, event) {
    if (state.opaque) return event.origin === "null";
    return state.origin !== "" && event.origin === state.origin;
  }

  // Browsers with scroll anchoring already keep the reader's place when a
  // frame above them changes height; scrolling as well would double it.
  var ANCHORS = !!(window.CSS && CSS.supports && CSS.supports("overflow-anchor", "auto"));

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
    // Keep the reader's place when a frame above them changes height. Measure
    // what was rendered: CSS (the narrow-screen floor) can override the value.
    if (!ANCHORS && rect.bottom <= 0) {
      window.scrollBy({
        top: state.iframe.getBoundingClientRect().height - before,
        behavior: "instant"
      });
    }
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

  var lockedOrientation = false;

  function toggleFullscreen(wrapper) {
    if (document.fullscreenElement === wrapper) {
      document.exitFullscreen();
      return;
    }
    // Measured before fullscreen, which makes every frame screen-shaped.
    var rect = wrapper.querySelector(".webpageviewplus-frame").getBoundingClientRect();
    wrapper.setAttribute("data-wide", String(rect.width > rect.height));
    wrapper.requestFullscreen();
  }

  function onFullscreenChange() {
    var full = document.fullscreenElement;
    var buttons = document.querySelectorAll('[data-webpageviewplus-action="fullscreen"]');
    for (var i = 0; i < buttons.length; i++) {
      if (buttons[i].hasAttribute("aria-pressed")) {
        buttons[i].setAttribute(
          "aria-pressed", String(full === buttons[i].closest(".webpageviewplus")));
      }
    }
    // Wide views turn the phone sideways (Android; iOS has no lock).
    var orientation = window.screen && screen.orientation;
    if (full && full.classList.contains("webpageviewplus") &&
        full.getAttribute("data-wide") === "true" && orientation && orientation.lock) {
      orientation.lock("landscape").then(function () {
        lockedOrientation = true;
      }, function () {});
    } else if (!full && lockedOrientation) {
      lockedOrientation = false;
      try { orientation.unlock(); } catch (e) { /* not supported */ }
    }
  }

  // Expand: where there's no element fullscreen (iPhone Safari), the view
  // fills the viewport in place. The frame isn't moved, which would reload it.
  var expanded = null;

  function expand(wrapper, opener) {
    if (expanded) return;
    var iframe = wrapper.querySelector(".webpageviewplus-frame");
    expanded = {wrapper: wrapper, opener: opener};
    wrapper.classList.add("webpageviewplus-expanded");
    wrapper.setAttribute("role", "dialog");
    wrapper.setAttribute("aria-modal", "true");
    wrapper.setAttribute("aria-label", iframe ? iframe.title : "");
    document.documentElement.classList.add("webpageviewplus-noscroll");
    var close = wrapper.querySelector('[data-webpageviewplus-action="close"]');
    if (close) {
      close.hidden = false;
      close.focus();
    }
  }

  function collapse() {
    if (!expanded) return;
    var wrapper = expanded.wrapper;
    var opener = expanded.opener;
    expanded = null;
    wrapper.classList.remove("webpageviewplus-expanded");
    wrapper.removeAttribute("role");
    wrapper.removeAttribute("aria-modal");
    wrapper.removeAttribute("aria-label");
    document.documentElement.classList.remove("webpageviewplus-noscroll");
    var close = wrapper.querySelector('[data-webpageviewplus-action="close"]');
    if (close) close.hidden = true;
    if (opener) opener.focus();
  }

  function onControl(event) {
    var target = event.currentTarget;
    var wrapper = target.closest(".webpageviewplus");
    if (!wrapper) return;
    var action = target.getAttribute("data-webpageviewplus-action");
    if (action === "fullscreen") {
      if (document.fullscreenEnabled) toggleFullscreen(wrapper);
      else expand(wrapper, target);
    } else if (action === "close") {
      collapse();
    } else if (action === "share") {
      navigator.share({
        title: target.getAttribute("data-share-title") || undefined,
        url: target.getAttribute("data-share-url")
      }).catch(function () { /* cancelled */ });
    } else if (action === "reload") {
      var state = stateForWrapper(wrapper);
      var url = target.getAttribute("data-reload-url");
      if (state && url) {
        state.height = null;
        state.iframe.src = url;
      }
    }
  }

  function setupControls() {
    var buttons = document.querySelectorAll("[data-webpageviewplus-action]");
    for (var i = 0; i < buttons.length; i++) {
      var button = buttons[i];
      var action = button.getAttribute("data-webpageviewplus-action");
      if (action === "share" && !navigator.share) continue;
      if (action === "fullscreen" && !document.fullscreenEnabled) {
        // It opens a dialog rather than toggling.
        button.textContent = button.getAttribute("data-expand-label");
        button.removeAttribute("aria-pressed");
        button.setAttribute("aria-haspopup", "dialog");
      }
      button.addEventListener("click", onControl);
      // Close appears only while expanded. Fullscreen and Reload wait for
      // a frame held back by load on tap (setupLoadOnTap shows them).
      var waiting = button.closest(".webpageviewplus").querySelector("[data-webpageviewplus-card]");
      if (action === "close" || (waiting && (action === "fullscreen" || action === "reload"))) continue;
      button.hidden = false;
    }
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("keydown", function (event) {
      if (expanded && event.key === "Escape") collapse();
    });
    // Keep keyboard focus inside the expanded view.
    document.addEventListener("focusin", function (event) {
      if (!expanded || expanded.wrapper.contains(event.target)) return;
      var close = expanded.wrapper.querySelector('[data-webpageviewplus-action="close"]');
      if (close) close.focus();
    });
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

  // Load on tap: the frame waits in a <template> until the reader asks.
  function setupLoadOnTap() {
    var buttons = document.querySelectorAll("[data-webpageviewplus-load]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function (event) {
        var card = event.currentTarget.closest("[data-webpageviewplus-card]");
        var wrapper = card && card.closest(".webpageviewplus");
        var stage = wrapper && wrapper.querySelector("template[data-webpageviewplus-stage]");
        if (!stage) return;
        card.parentNode.replaceChild(stage.content.cloneNode(true), card);
        stage.parentNode.removeChild(stage);
        var iframe = wrapper.querySelector("iframe.webpageviewplus-frame");
        if (!iframe) return;
        setup(iframe);
        var held = wrapper.querySelectorAll(
          '[data-webpageviewplus-action="fullscreen"], [data-webpageviewplus-action="reload"]');
        for (var j = 0; j < held.length; j++) held[j].hidden = false;
        iframe.focus();
      });
    }
    return buttons.length;
  }

  function init() {
    setupFrameabilityCheck();
    var nodes = document.querySelectorAll("iframe.webpageviewplus-frame");
    for (var i = 0; i < nodes.length; i++) setup(nodes[i]);
    setupControls();
    var deferred = setupLoadOnTap();
    if (!frames.length && !deferred) return;
    window.addEventListener("message", onMessage);
    window.addEventListener("resize", onWindowResize);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
