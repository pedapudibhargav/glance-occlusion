"""Find more sites with a top-document dismissible overlay.

Earlier scans failed with "Navigation is interrupted by another navigation" —
these sites redirect immediately (geo, consent, A/B). Tolerating that is the
fix: commit to the navigation, then wait for the DOM to settle, rather than
waiting for a load event that a redirect cancels.
"""
import sys
from playwright.sync_api import sync_playwright

CAND = [
 # Second sweep. The eight sites already in eval/overlay_sites.py are
 # excluded, so anything found here is new evidence rather than a re-measure.
 "https://www.telegraph.co.uk/","https://www.metro.co.uk/","https://www.marca.com/",
 "https://www.corriere.it/","https://www.20minutos.es/","https://www.nu.nl/",
 "https://www.vg.no/","https://www.lefigaro.fr/","https://www.liberation.fr/",
 "https://www.faz.net/","https://www.elmundo.es/","https://www.hln.be/",
 "https://www.aftonbladet.se/","https://www.nasdaq.com/","https://www.scmp.com/",
 "https://www.thehindu.com/","https://www.independent.ie/","https://www.bild.de/",
 "https://www.dr.dk/","https://www.expressen.se/","https://www.svd.se/",
 "https://www.lavanguardia.com/","https://www.abc.es/","https://www.publico.pt/",
 "https://www.onet.pl/","https://www.wp.pl/","https://www.idnes.cz/",
 "https://www.blick.ch/","https://www.derstandard.at/","https://www.kurier.at/",
 "https://www.gazzetta.it/","https://www.ilsole24ore.com/","https://www.lesechos.fr/",
 "https://www.ouest-france.fr/","https://www.nzz.ch/","https://www.zeit.de/",
 "https://www.stern.de/","https://www.focus.de/","https://www.standaard.be/",
 "https://www.hurriyet.com.tr/","https://www.tvp.pl/","https://www.lanacion.com.ar/",
]

PROBE = r"""() => {
  const deep=(x,y)=>{let e=document.elementFromPoint(x,y);let g=0;while(e&&e.shadowRoot&&g++<8){const i=e.shadowRoot.elementFromPoint(x,y);if(!i||i===e)break;e=i;}return e;};
  const els=[...document.querySelectorAll('a[href],button,input,select,textarea,[role=button],[tabindex],[onclick]')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;});
  let blocked=0;
  for(const el of els){const r=el.getBoundingClientRect();const cx=r.left+r.width/2,cy=r.top+r.height/2;
    if(cx<0||cy<0||cx>=innerWidth||cy>=innerHeight)continue;
    const h=deep(cx,cy); if(!h||el===h||el.contains(h)||h.contains(el))continue;
    let n=h,top=h,g=0,pos=false;
    while(n&&n!==document.body&&g++<24){const st=getComputedStyle(n);
      if(st.position==='fixed'||st.position==='sticky'||(st.position==='absolute'&&parseInt(st.zIndex,10)>0)){top=n;pos=true;} n=n.parentElement;}
    const dec=top.getAttribute&&(top.getAttribute('aria-modal')==='true'||/dialog/.test(top.getAttribute('role')||''));
    if(pos||dec) blocked++;}
  const d=[...document.querySelectorAll('button,[role=button],input[type=submit]')]
    .filter(e=>{const r=e.getBoundingClientRect(); return r.width>0&&r.height>0 &&
      /accept|agree|allow|continue|got it|zustimmen|akzeptieren|aceptar|accetta|godta|tillad|accepter|akkoord/i.test((e.innerText||e.value||'')+' '+(e.getAttribute('aria-label')||''));})
    .map(e=>(e.innerText||e.value||'').trim().slice(0,20));
  const links=[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')||'')
    .filter(h=>h.startsWith('/') && h.length>4 && h.length<20 && h.split('/').filter(Boolean).length===1);
  return {blocked, dismiss:d.slice(0,2), navs:[...new Set(links)].slice(0,6), total:els.length};
}"""

def tolerant_goto(page, url):
    """Navigate, accepting a redirect cancelling our own navigation."""
    try:
        page.goto(url, wait_until="commit", timeout=30000)
    except Exception as exc:
        if "interrupted" not in str(exc).lower():
            raise
    page.wait_for_timeout(3500)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width":1280,"height":720}, locale="en-GB",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
    pg = ctx.new_page()
    usable = []
    for s in CAND:
        try:
            tolerant_goto(pg, s)
            r = pg.evaluate(PROBE)
        except Exception as e:
            print(f"{s.split('//')[-1][:26]:<28} failed: {str(e)[:40]}")
            continue
        ok = r["blocked"] >= 5 and r["dismiss"] and r["navs"]
        if ok:
            usable.append((s, r))
        print(f"{s.split('//')[-1][:26]:<28} blk={r['blocked']:>3} dismiss={str(r['dismiss'])[:22]:<24} "
              f"navs={str(r['navs'][:3])[:36]}{'  <== USABLE' if ok else ''}")
    ctx.close(); b.close()

print(f"\n{len(usable)} usable new site(s)")
for s, r in usable:
    print(f"  {s}  dismiss={r['dismiss']}  navs={r['navs'][:4]}")
