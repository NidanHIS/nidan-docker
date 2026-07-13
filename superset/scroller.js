(function () {
  'use strict';

  var scrollers = new WeakMap();

  function findScrollables() {
    var found = Array.from(
      document.querySelectorAll('div[role="table"] div[role="presentation"]')
    ).filter(function (el) {
      var s = el.getAttribute("style") || "";
      return (s.indexOf("overflow: auto") !== -1 || s.indexOf("overflow:auto") !== -1)
          && el.scrollHeight > el.clientHeight + 20;
    });
    if (!found.length) {
      found = Array.from(
        document.querySelectorAll(".dataTable, .table-responsive, .ant-table-body")
      ).filter(function (el) {
        return el.scrollHeight > el.clientHeight + 20;
      });
    }
    return found;
  }

  function getStorageKey(el) {
    var card = el.closest(".slice_container, .chart-container, .grid-item, .dashboard-component");
    if (card) {
      var header = card.querySelector(".header-title, .chart-header, h2, h3, [class*=title]");
      if (header && header.textContent) {
        return "nidan_scroll_" + header.textContent.trim().replace(/[^a-z0-9]/gi, "_").toLowerCase();
      }
    }
    return "nidan_scroll_default";
  }

  // Attach clone — only called when scroll is turned ON
  function attachClone(el) {
    if (el.querySelector("._nidan_clone_")) return;
    var tbody = el.querySelector("tbody");
    if (!tbody) return;
    var clone = tbody.cloneNode(true);
    clone.classList.add("_nidan_clone_");
    clone.setAttribute("aria-hidden", "true");
    tbody.parentNode.insertBefore(clone, tbody.nextSibling);
  }

  // Remove clone and snap back to top — called when scroll is turned OFF
  function detachClone(el) {
    var clone = el.querySelector("._nidan_clone_");
    if (clone) clone.parentNode.removeChild(clone);
    el.scrollTop = 0;
  }

  function setupScroller(el) {
    if (scrollers.has(el)) return;

    var storageKey = getStorageKey(el);
    var saved = localStorage.getItem(storageKey + "_speed");
    var speed = saved !== null ? parseInt(saved, 10) : 1;

    var state = { id: null, speed: speed };
    scrollers.set(el, state);

    // NO clone attached here — only attached when scroll starts

    // --- Control UI ---
    var control = document.createElement("div");
    control.className = "nidan-scroll-control";
    control.style.cssText = (
      "position:absolute;bottom:8px;right:8px;z-index:200;"
      + "display:inline-flex;align-items:center;gap:6px;"
      + "font-size:11px;color:#555;font-family:inherit;"
      + "background:rgba(255,255,255,0.93);"
      + "border:1px solid #d9d9d9;border-radius:6px;"
      + "padding:3px 8px;box-shadow:0 1px 4px rgba(0,0,0,0.12);"
    );

    var lbl = document.createElement("span");
    lbl.textContent = "Scroll:";
    lbl.style.cssText = "white-space:nowrap;font-weight:600;";
    control.appendChild(lbl);

    var sel = document.createElement("select");
    sel.style.cssText = (
      "border:1px solid #d9d9d9;border-radius:4px;"
      + "padding:2px 4px;font-size:11px;"
      + "background:#fff;color:#333;cursor:pointer;"
    );
    [
      { text: "Off",    value: 0 },
      { text: "Slow",   value: 1 },
      { text: "Medium", value: 2 },
      { text: "Fast",   value: 4 },
    ].forEach(function (opt) {
      var o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.text;
      if (opt.value === speed) o.selected = true;
      sel.appendChild(o);
    });
    control.appendChild(sel);

    sel.addEventListener("change", function (e) {
      e.stopPropagation();
      var val = parseInt(sel.value, 10);
      localStorage.setItem(storageKey + "_speed", val);
      state.speed = val;
      startScroll(el);
    });

    var anchor = el.closest(".slice_container, .chart-container, .grid-item") || el.parentElement;
    if (anchor) {
      if (window.getComputedStyle(anchor).position === "static") {
        anchor.style.position = "relative";
      }
      anchor.appendChild(control);
    }

    // Pause on hover, resume on leave
    el.addEventListener("mouseenter", function () {
      if (state.id) { clearInterval(state.id); state.id = null; }
    });
    el.addEventListener("mouseleave", function () {
      startScroll(el);
    });

    startScroll(el);
  }

  function startScroll(el) {
    var state = scrollers.get(el);
    if (!state) return;
    if (state.id) { clearInterval(state.id); state.id = null; }

    if (state.speed <= 0) {
      // Scroll turned OFF — remove clone, reset to top
      detachClone(el);
      return;
    }

    // Scroll turned ON — attach clone for seamless loop
    attachClone(el);

    state.id = setInterval(function () {
      el.scrollTop += state.speed;
      var originalHeight = Math.floor(el.scrollHeight / 2);
      if (el.scrollTop >= originalHeight) {
        el.scrollTop -= originalHeight;
      }
    }, 30);
  }

  var _pending = false;
  function scheduleEnhance() {
    if (_pending) return;
    _pending = true;
    setTimeout(function () {
      _pending = false;
      findScrollables().forEach(setupScroller);
    }, 1200);
  }

  new MutationObserver(scheduleEnhance).observe(
    document.body, { childList: true, subtree: true }
  );
  window.addEventListener("load", scheduleEnhance);
  setTimeout(scheduleEnhance, 2500);

}());
