// pdf_view_plus: PDF.js viewer options, set before the viewer starts.
// PDF.js dispatches webviewerloaded on its parent's document when it can
// reach it, else on its own. Framed from CKAN's page it can't (another
// origin), and opened on its own its parent is itself.
(function () {
  "use strict";
  var lang = new URLSearchParams(location.search).get("wvp_lang") || "en-us";
  document.addEventListener("webviewerloaded", function () {
    var options = window.PDFViewerApplicationOptions;
    options.set("enableScripting", false); // a PDF's own JavaScript never runs
    options.set("disablePreferences", true); // nothing kept in the browser
    options.set("defaultUrl", "");
    options.set("annotationEditorMode", -1); // read-only: no editing tools
    options.set("disableRange", true); // one request for the whole file
    options.set("disableHistory", true); // the CKAN page owns history
    options.set("externalLinkTarget", 2); // a PDF's links open a new tab, never this frame
    options.set("localeProperties", { lang: lang });
  });

  // The first page as a PNG, for the view form's "The PDF's first page"
  // thumbnail. Only the page framing this viewer may ask: frame-ancestors
  // limits that to CKAN's own pages. The image goes back to it alone.
  var MAX_SIDE = 640;
  var TYPE = "webpageviewplus:pdf-thumbnail";

  function documentLoaded(app) {
    if (app.pdfDocument) return Promise.resolve(app.pdfDocument);
    return new Promise(function (resolve) {
      app.eventBus.on("documentloaded", function () { resolve(app.pdfDocument); }, { once: true });
    });
  }

  function firstPage() {
    var app = window.PDFViewerApplication;
    return app.initializedPromise
      .then(function () { return documentLoaded(app); })
      .then(function (pdf) { return pdf.getPage(1); })
      .then(function (page) {
        var base = page.getViewport({ scale: 1 });
        var scale = MAX_SIDE / Math.max(base.width, base.height);
        var viewport = page.getViewport({ scale: scale });
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(viewport.width);
        canvas.height = Math.round(viewport.height);
        return page.render({ canvas: canvas, viewport: viewport }).promise.then(function () {
          return new Promise(function (resolve, reject) {
            canvas.toBlob(function (blob) {
              if (blob) resolve(blob.arrayBuffer()); else reject(new Error("no image"));
            }, "image/png");
          });
        });
      });
  }

  window.addEventListener("message", function (event) {
    var data = event.data;
    if (event.source !== window.parent || window.parent === window) return;
    if (!data || data.type !== TYPE) return;
    firstPage().then(function (png) {
      event.source.postMessage({ type: TYPE, png: png }, event.origin, [png]);
    }, function () {
      event.source.postMessage({ type: TYPE, error: true }, event.origin);
    });
  });
})();
