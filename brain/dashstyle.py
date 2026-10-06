"""Offline appearance shared by the default dashboard and its classic fallback."""
from brain import graphview, i18n


def css() -> str:
    light = ("color-scheme:light;--ink:#202624;--dim:#596660;--line:#dde3df;"
             "--bg:#f4f6f3;--card:#ffffff;--g:#16713e;--a:#956000;--r:#be3030;"
             "--grey:#65736d;--blue:#265fb5;--track:#e9eeeb;--series2:#84938c;"
             "--series3:#c3cec7;--on-accent:#fff;--soft:#edf2ee;"
             "--plane:var(--bg);--surface:var(--card);--ink2:var(--dim);"
             "--muted:var(--grey);--grid:var(--line);--ring:var(--line);"
             "--bar:var(--blue);--bar-soft:var(--track);" + graphview.css_vars(False))
    dark = ("color-scheme:dark;--ink:#edf2ee;--dim:#b0bdb5;--line:#35423a;"
            "--bg:#141a17;--card:#1e2822;--g:#73d89a;--a:#f2c36b;--r:#ff9691;"
            "--grey:#a4b3aa;--blue:#8bb7fa;--track:#334038;--series2:#8eaaa0;"
            "--series3:#526b5e;--on-accent:#141a17;--soft:#27342c;" + graphview.css_vars(True))
    return (":root{" + light + "}\n"
            "@media(prefers-color-scheme:dark){:root:not([data-theme=light]){" + dark + "}}\n"
            ":root[data-theme=dark]{" + dark + "}\n" + CONTROLS)


CONTROLS = """
.appearance{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12px;color:var(--dim)}
.appearance select{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:8px;max-width:100%}
:focus-visible{outline:2px solid var(--blue);outline-offset:4px}
@media print{:root,:root[data-theme],:root:not([data-theme=light]){color-scheme:light;--ink:#000;--dim:#333;--grey:#444;--bg:#fff;--card:#fff;
--line:#aaa;--track:#eee;--blue:#222;--g:#222;--a:#222;--r:#222}
.appearance,.langs,.mtoggle{display:none!important}*{animation:none!important}}
@media(forced-colors:active){.card,.lt,.tile{border:1px solid CanvasText}
.edges line{stroke:CanvasText!important}.nd circle{stroke:CanvasText!important}}
"""


def controls() -> str:
    # Catalog values are text, including when they are placed inside an option.
    from html import escape
    options = "".join('<option value="%s" data-t="dash.theme.%s">%s</option>'
                      % (mode, mode, escape(i18n.t("dash.theme." + mode)))
                      for mode in ("system", "light", "dark"))
    return ('<label class="appearance"><span data-t="dash.theme">%s</span>'
            '<select id="theme">%s</select></label>'
            % (escape(i18n.t("dash.theme")), options))


# Run in the head, before paint. Storage can be unavailable for local files.
# CSS still follows the system when scripting or storage is unavailable.
INIT = """
(function(){
 var root=document.documentElement, mode='system';
 try{mode=localStorage.getItem('brain.dash.theme')||'system';}catch(e){}
 if(['system','light','dark'].indexOf(mode)<0)mode='system';
 if(mode!=='system')root.setAttribute('data-theme',mode);
 try{if(localStorage.getItem('brain.dash.motion')==='off')root.classList.add('reduced');}catch(e){}
 document.addEventListener('DOMContentLoaded',function(){
  var select=document.getElementById('theme');if(!select)return;
  select.value=mode;
  select.addEventListener('change',function(){
   mode=select.value;
   if(mode==='system')root.removeAttribute('data-theme');else root.setAttribute('data-theme',mode);
   try{localStorage.setItem('brain.dash.theme',mode);}catch(e){}
  });
 });
})();
"""
