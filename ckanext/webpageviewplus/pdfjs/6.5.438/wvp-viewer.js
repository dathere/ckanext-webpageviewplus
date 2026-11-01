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
    options.set("localeProperties", { lang: lang });
  });
})();
