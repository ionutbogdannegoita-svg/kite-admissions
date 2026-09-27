// KITE Admissions: poco JavaScript, solo comodità. Ogni protezione vera è sul server.
(function () {
  "use strict";

  // Un solo invio per modulo: il pulsante viene disattivato dopo il primo clic.
  // Il server resta idempotente anche se l'invio arrivasse due volte.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.dataset.submitted === "1") {
      event.preventDefault();
      return;
    }
    form.dataset.submitted = "1";
    window.setTimeout(function () {
      form.querySelectorAll("button[type=submit], input[type=submit]").forEach(function (button) {
        button.classList.add("is-busy");
        button.disabled = true;
      });
    }, 0);
  });

  // Tornando indietro con il browser la pagina può essere riusata: riattiva i pulsanti.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-submitted]").forEach(function (form) {
      delete form.dataset.submitted;
      form.querySelectorAll("button, input[type=submit]").forEach(function (button) {
        button.classList.remove("is-busy");
        button.disabled = false;
      });
    });
  });

  // Selettori che inviano subito il modulo (filtri).
  document.addEventListener("change", function (event) {
    var el = event.target;
    if (el.matches && el.matches("[data-autosubmit]") && el.form) {
      el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    }
  });

  // Mostra/nasconde campi in base a una scelta (es. stato della richiesta).
  function syncToggles() {
    document.querySelectorAll("[data-show-when]").forEach(function (block) {
      var spec = block.getAttribute("data-show-when").split("=");
      var form = block.closest("form");
      if (!form) return;
      var control = form.querySelector("[name='" + spec[0] + "']:checked") || form.querySelector("select[name='" + spec[0] + "']");
      var value = control ? control.value : "";
      var wanted = spec[1].split("|");
      block.classList.toggle("hidden", wanted.indexOf(value) === -1);
    });
  }
  document.addEventListener("change", syncToggles);
  document.addEventListener("DOMContentLoaded", syncToggles);
})();
