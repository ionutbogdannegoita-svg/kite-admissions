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

  // Protezione dall'uscita (colloquio): un modulo con data-guard modificato fa scattare l'avviso
  // standard del browser se si lascia la pagina da un link; il suo stesso invio lo azzera.
  function markDirty(event) {
    var form = event.target && event.target.form;
    if (form && form.hasAttribute("data-guard")) form.dataset.dirty = "1";
  }
  document.addEventListener("input", markDirty);
  document.addEventListener("change", markDirty);
  window.addEventListener("beforeunload", function (event) {
    if (document.querySelector("form[data-guard][data-dirty='1']:not([data-submitted='1'])")) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  // Scelte rapide della conclusione: copiano azione, data (calcolata dal server) e nota nel
  // prossimo passo. Non salvano nulla: senza JavaScript i campi si compilano a mano.
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-quick]") : null;
    if (!button || !button.form) return;
    var form = button.form;
    function field(name) { return form.querySelector("[name='" + name + "']"); }
    function set(name, value) {
      var input = field(name);
      if (!input) return;
      input.value = value;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    var due = button.dataset.due || "";
    if (button.dataset.dueByTiming) {
      var timing = form.querySelector("[name='timing']:checked");
      button.dataset.dueByTiming.split(";").forEach(function (pair) {
        var parts = pair.split("=");
        if (timing && parts[0] === timing.value) due = parts[1];
      });
    }
    set("step1_action", button.dataset.action || "");
    set("step1_due_on", button.dataset.action ? due : "");
    set("step1_note", button.dataset.note || "");
    if (button.dataset.closeLeads) {
      var obstacle = field("obstacle");
      var reason = obstacle && obstacle.value ? obstacle.options[obstacle.selectedIndex].text : "";
      form.querySelectorAll("[data-close-lead]").forEach(function (box) {
        box.checked = true;
        box.dispatchEvent(new Event("change", { bubbles: true }));
      });
      form.querySelectorAll("[data-close-reason]").forEach(function (input) {
        if (!input.value) input.value = reason;
      });
    }
  });
})();
