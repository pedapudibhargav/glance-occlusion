"""Sites with a blocking consent overlay dismissible from the top document.

Found by scanning candidates with `scripts/find_overlay_sites.py`. Two
requirements: the overlay must block several elements, and the control that
dismisses it must be in the top document. Sites whose dismiss control lives in
a cross-origin iframe (BBC, the Guardian, Spiegel, the FT, Sky Sports, heise,
Le Figaro) are excluded, because no top-document observation can reach it and
both arms would simply fail.

Navigation to most of these is interrupted by an immediate redirect — geo,
consent, or an A/B split — so `tolerant_goto` commits to the navigation and
then waits for the DOM rather than waiting for a load event the redirect
cancels. Without that, 18 of 24 candidates looked like hard failures.

Each task's target is a navigation link behind the overlay, and success is a
URL check: unambiguous, with nothing judging it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverlaySite:
    key: str
    url: str
    blocked_elements: int
    dismiss_example: str
    targets: tuple[tuple[str, str], ...]  # (label, path fragment to check)


SITES: tuple[OverlaySite, ...] = (
    OverlaySite("cnn", "https://www.cnn.com/", 36, "Agree",
                (("World", "world"), ("Politics", "politic"), ("Business", "business"))),
    OverlaySite("lemonde", "https://www.lemonde.fr/", 25, "Accept",
                (("International", "international"), ("Economie", "economie"),
                 ("Culture", "culture"))),
    OverlaySite("euronews", "https://www.euronews.com/", 171, "Agree and close",
                (("Business", "business"), ("Next", "next"))),
    OverlaySite("mirror", "https://www.mirror.co.uk/", 48, "ACCEPT",
                (("Subscribe", "subscribe"), ("About Us", "about-us"))),
    OverlaySite("rte", "https://www.rte.ie/news/", 26, "Allow All",
                (("Sport", "sport"), ("Radio", "radio"))),
    OverlaySite("irishtimes", "https://www.irishtimes.com/", 39, "I ACCEPT",
                (("Podcasts", "podcasts"), ("Newsletters", "newsletters"))),
    OverlaySite("repubblica", "https://www.repubblica.it/", 23, "ACCETTA",
                (("Rubriche", "rubriche"),)),
    # Second sweep, 2026-10-23. 42 further candidates scanned with
    # scripts/find_overlay_sites.py; these three qualified on the same rule as
    # the rest (several elements blocked, and a dismiss control in the top
    # document). Most of the rejects were blocked but had their dismiss button
    # inside a cross-origin iframe — Marca 59 blocked, Corriere 56, El Mundo
    # 252, Bild 181, Kurier 66, Il Sole 78 — which is the same split
    # data/occlusion_methods_findings.md reports, showing up again in a fresh
    # sample.
    OverlaySite("lavanguardia", "https://www.lavanguardia.com/", 31, "Aceptar y continuar",
                (("Internacional", "internacional"), ("Politica", "politica"),
                 ("Opinion", "opinion"))),
    OverlaySite("abc", "https://www.abc.es/", 44, "Aceptar y continuar",
                (("Espana", "espana"), ("Opinion", "opinion"),
                 ("Internacional", "internacional"), ("Economia", "economia"))),
    OverlaySite("standaard", "https://www.standaard.be/", 17, "Akkoord",
                (("Net binnen", "net-binnen"), ("Digitale krant", "digitalekrant"))),
    # Control: the overlay is present but does not obstruct the navigation, so
    # both arms should reach the target. If the structured arm "wins" here too,
    # something other than the overlay is driving the result.
    OverlaySite("aljazeera", "https://www.aljazeera.com/", 11, "Allow all",
                (("News", "news"), ("Sport", "sport"))),
)


def tolerant_goto(page, url: str, settle_ms: int = 3500) -> None:
    """Navigate, accepting a redirect that cancels our own navigation.

    Most of these sites redirect on first load. Waiting for `domcontentloaded`
    raises "Navigation is interrupted by another navigation"; committing and
    then waiting for the DOM to settle does not.
    """
    try:
        page.goto(url, wait_until="commit", timeout=30000)
    except Exception as exc:
        if "interrupted" not in str(exc).lower():
            raise
    page.wait_for_timeout(settle_ms)
