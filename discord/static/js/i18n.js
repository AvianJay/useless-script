(function (global) {
    "use strict";

    const NO_PLURAL_PREFIXES = /^(zh|ja|ko|th|vi|id|ms)/i;

    function interpolate(template, params) {
        return String(template).replace(/\{([^{}]+)\}/g, function (match, name) {
            return Object.prototype.hasOwnProperty.call(params, name)
                ? String(params[name])
                : match;
        });
    }

    function selectPlural(value, params) {
        const count = Number(params.count);
        const locale = global.I18N_LOCALE || "zh-TW";
        let variant = "other";
        if (count === 0 && value.zero !== undefined) {
            variant = "zero";
        } else if (!NO_PLURAL_PREFIXES.test(locale) && count === 1 && value.one !== undefined) {
            variant = "one";
        }
        if (value[variant] !== undefined) return value[variant];
        if (value.other !== undefined) return value.other;
        return Object.values(value)[0];
    }

    function translate(key, params) {
        const values = global.I18N || {};
        const replacements = params || {};
        let value = values[key];
        if (value === undefined || value === null) return key;
        if (typeof value === "object") value = selectPlural(value, replacements);
        return interpolate(value, replacements);
    }

    function bindLanguageSelectors() {
        document.querySelectorAll("[data-language-selector] select[name='lang']").forEach(function (select) {
            select.addEventListener("change", function () {
                const form = select.closest("form");
                const next = form && form.querySelector("input[name='next']");
                if (!form) return;
                if (next) {
                    next.value = global.location.pathname + global.location.search + global.location.hash;
                }
                if (typeof form.requestSubmit === "function") form.requestSubmit();
                else form.submit();
            });
        });
    }

    global.t = translate;
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindLanguageSelectors);
    } else {
        bindLanguageSelectors();
    }
})(window);
