/* Atlantis Studio — renderizador markdown-lite para el chat de agentes.
 *
 * Standalone (sin dependencias): se carga con <script> antes de app.js.
 * - Escapa HTML PRIMERO (anti-XSS), luego aplica transformaciones.
 * - Bloques cercados ```lang con etiqueta de lenguaje + botón copiar.
 * - Inline code, bold, italic, tachado, listas, encabezados, citas, enlaces http(s).
 * - Exporta { renderMarkdown, getCodeBlock } (UMD: window o module.exports).
 */
(function (root, factory) {
  var lib = factory();
  if (typeof window !== "undefined") {
    window.renderMarkdown = lib.renderMarkdown;
    window.mdGetCodeBlock = lib.getCodeBlock;
  }
  if (typeof globalThis !== "undefined") {
    globalThis.renderMarkdown = lib.renderMarkdown;
    globalThis.mdGetCodeBlock = lib.getCodeBlock;
  }
  if (root) {
    root.renderMarkdown = lib.renderMarkdown;
    root.mdGetCodeBlock = lib.getCodeBlock;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = lib;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var codeRegistry = [];

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function getCodeBlock(i) {
    return codeRegistry[i] || "";
  }

  function fencedHtml(b) {
    var idx = codeRegistry.length;
    if (codeRegistry.length > 500) codeRegistry.shift();
    codeRegistry.push(b.code);
    var lang = b.lang || "código";
    return (
      '<div class="md-code"><div class="md-code-head">' +
      '<span class="md-lang">' + escHtml(lang) + "</span>" +
      '<button type="button" class="md-copy" data-mdcopy="' + idx + '">Copiar</button>' +
      "</div>" +
      '<pre class="md-pre"><code>' + escHtml(b.code) + "</code></pre></div>"
    );
  }

  function inlineMd(t) {
    // Enlaces [texto](https://...) — solo http/https.
    t = t.replace(
      /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>'
    );
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    t = t.replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    return t;
  }

  function blockMd(t) {
    var lines = t.split("\n");
    var html = "";
    var para = [];
    var list = null;
    function flushPara() {
      if (para.length) {
        html += "<p>" + para.join("<br>") + "</p>";
        para = [];
      }
    }
    function flushList() {
      if (list) {
        html += "<" + list.tag + ">" +
          list.items.map(function (i) { return "<li>" + i + "</li>"; }).join("") +
          "</" + list.tag + ">";
        list = null;
      }
    }
    for (var n = 0; n < lines.length; n++) {
      var s = lines[n].trim();
      if (!s) { flushPara(); flushList(); continue; }
      // Placeholder de bloque cercado: emitir tal cual, sin <p>.
      if (/^\u0000F\d+\u0000$/.test(s)) { flushPara(); flushList(); html += s; continue; }
      var m;
      if ((m = s.match(/^(#{1,4})\s+(.*)$/))) {
        flushPara(); flushList();
        var lvl = Math.min(m[1].length + 1, 4);
        html += '<h' + lvl + ' class="md-h">' + m[2] + "</h" + lvl + ">";
        continue;
      }
      if ((m = s.match(/^&gt;\s?(.*)$/))) {
        flushPara(); flushList();
        html += "<blockquote>" + m[1] + "</blockquote>";
        continue;
      }
      if (/^(-{3,}|\*{3,})$/.test(s)) { flushPara(); flushList(); html += "<hr>"; continue; }
      if ((m = s.match(/^([-*+])\s+(.*)$/))) {
        flushPara();
        if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; }
        list.items.push(m[2]);
        continue;
      }
      if ((m = s.match(/^(\d+)[.)]\s+(.*)$/))) {
        flushPara();
        if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; }
        list.items.push(m[2]);
        continue;
      }
      flushList();
      para.push(s);
    }
    flushPara();
    flushList();
    return html;
  }

  function renderMarkdown(src) {
    src = String(src == null ? "" : src);
    var fenced = [];
    // 1. Extraer bloques cercados del texto CRUDO (el código no se transforma).
    var t = src.replace(
      /```([^\n]*)\n([\s\S]*?)(?:\n```[ \t]*|```[ \t]*|$)/g,
      function (m, lang, code) {
        var i = fenced.length;
        fenced.push({ lang: (lang || "").trim().toLowerCase(), code: code.replace(/\s+$/, "") });
        return "\u0000F" + i + "\u0000";
      }
    );
    // 2. Escapar HTML (los placeholders  F.. sobreviven).
    t = escHtml(t); // los placeholders \u0000F..\u0000 sobreviven al escape
    // 3. Extraer inline code.
    var inline = [];
    t = t.replace(/`([^`\n]+)`/g, function (m, code) {
      var i = inline.length;
      inline.push(code);
      return "\u0000I" + i + "\u0000";
    });
    // 4. Inline: bold/italic/links.
    t = inlineMd(t);
    // 5. Bloques: encabezados, listas, citas, párrafos.
    t = blockMd(t);
    // 6. Restaurar inline code.
    t = t.replace(/\u0000I(\d+)\u0000/g, function (m, i) {
      return '<code class="md-inline">' + inline[+i] + "</code>";
    });
    // 7. Restaurar bloques cercados como HTML.
    t = t.replace(/\u0000F(\d+)\u0000/g, function (m, i) {
      return fencedHtml(fenced[+i]);
    });
    return t;
  }

  return { renderMarkdown: renderMarkdown, getCodeBlock: getCodeBlock, escHtml: escHtml };
});
