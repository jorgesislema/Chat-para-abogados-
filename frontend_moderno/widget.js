/* Widget incrustable del asistente (una sola linea):
   <script src="https://TU-SERVIDOR/widget.js" data-base="https://TU-SERVIDOR" defer><\/script>
   Inyecta un iframe con la burbuja (?embed=1). Sin dependencias. */
(function () {
  "use strict";
  var actual = document.currentScript;
  var base = (actual && actual.getAttribute("data-base")) || "";
  if (!base) {
    base = location.origin;
  }
  base = base.replace(/\/$/, "");
  var marco = document.createElement("iframe");
  marco.src = base + "/?embed=1";
  marco.title = "Asistente legal";
  marco.setAttribute("aria-label", "Asistente legal del bufete");
  marco.style.cssText = "position:fixed;right:18px;bottom:18px;width:64px;height:64px;border:none;border-radius:50%;z-index:99999;background:transparent;";
  window.addEventListener("message", function (ev) {
    var d = ev.data || {};
    if (d.fuente !== "legalrag") return;
    if (d.abierto) {
      marco.style.cssText = "position:fixed;right:18px;bottom:18px;width:min(420px,94vw);height:min(640px,88vh);border:1px solid #E2E8F0;border-radius:16px;z-index:99999;background:#fff;box-shadow:0 6px 24px rgba(15,42,74,.2);";
    } else {
      marco.style.cssText = "position:fixed;right:18px;bottom:18px;width:64px;height:64px;border:none;border-radius:50%;z-index:99999;background:transparent;";
    }
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.body.appendChild(marco);
  });
  if (document.readyState !== "loading") {
    document.body.appendChild(marco);
  }
})();
