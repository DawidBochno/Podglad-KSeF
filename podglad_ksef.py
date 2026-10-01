#!/usr/bin/env python3
"""Podglad e-faktur KSeF. Zamienia plik XML faktury ustrukturyzowanej
FA(3) / FA(2) na czytelna fakture HTML i PDF (PDF przez przegladarke Edge,
wbudowana w Windows). Wsadowo: caly folder + zestawienie CSV.

Tylko biblioteka standardowa Pythona.

Uruchomienie: python podglad_ksef.py            (GUI)
              python podglad_ksef.py --selftest (test logiki)
"""
import csv
import html
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------- slowniki ----
# wartosci ze schematu FA(3) (CIRFMF/ksef-api, schemat_FA(3)_v1-0E.xsd)

RODZAJ = {
    "VAT": "Faktura VAT", "KOR": "Faktura korygująca", "ZAL": "Faktura zaliczkowa",
    "ROZ": "Faktura rozliczeniowa", "UPR": "Faktura uproszczona",
    "KOR_ZAL": "Faktura korygująca fakturę zaliczkową",
    "KOR_ROZ": "Faktura korygująca fakturę rozliczeniową",
}
FORMA_PLATNOSCI = {"1": "gotówka", "2": "karta", "3": "bon", "4": "czek",
                   "5": "kredyt", "6": "przelew", "7": "płatność mobilna"}
ROLA = {
    "1": "Faktor", "2": "Odbiorca", "3": "Podmiot pierwotny", "4": "Dodatkowy nabywca",
    "5": "Wystawca faktury", "6": "Dokonujący płatności",
    "7": "JST – wystawca", "8": "JST – odbiorca",
    "9": "Członek grupy VAT – wystawca", "10": "Członek grupy VAT – odbiorca", "11": "Pracownik",
}
TYP_KOREKTY = {"1": "w dacie ujęcia faktury pierwotnej",
               "2": "w dacie wystawienia faktury korygującej", "3": "w innej dacie"}
STAWKA = {"0 KR": "0% (kraj)", "0 WDT": "0% (WDT)", "0 EX": "0% (eksport)",
          "zw": "zw", "oo": "oo", "np I": "np", "np II": "np"}
# (pole netto, pole VAT, opis) - podsumowanie wg stawek
SUMY = [
    ("P_13_1", "P_14_1", "23% / 22%"), ("P_13_2", "P_14_2", "8% / 7%"),
    ("P_13_3", "P_14_3", "5%"), ("P_13_4", "P_14_4", "4% / 3% (ryczałt taxi)"),
    ("P_13_5", "P_14_5", "procedura OSS"), ("P_13_6_1", None, "0% (kraj)"),
    ("P_13_6_2", None, "0% (WDT)"), ("P_13_6_3", None, "0% (eksport)"),
    ("P_13_7", None, "zwolnione"), ("P_13_8", None, "np (poza krajem)"),
    ("P_13_9", None, "np (art. 100 ust. 1 pkt 4)"), ("P_13_10", None, "odwrotne obciążenie"),
    ("P_13_11", None, "procedura marży"),
]
ADNOTACJE = [("P_16", "Metoda kasowa"), ("P_17", "Samofakturowanie"),
             ("P_18", "Odwrotne obciążenie"), ("P_18A", "Mechanizm podzielonej płatności"),
             ("P_23", "Procedura uproszczona – transakcja trójstronna")]

# --------------------------------------------------------------- liczby ----


def dec(s):
    try:
        return Decimal((s or "").strip())
    except InvalidOperation:
        return None


def money(s):
    d = dec(s) if isinstance(s, str) else s
    if d is None:
        return s or ""
    return "{:,.2f}".format(d).replace(",", " ").replace(".", ",")


def qty(s):
    d = dec(s)
    if d is None:
        return s or ""
    t = format(d.normalize(), "f") if d == d.to_integral() or d.as_tuple().exponent < 0 else str(d)
    return t.replace(".", ",")


_J = ["", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć"]
_N = ["dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście", "piętnaście",
      "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście"]
_D = ["", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt",
      "sześćdziesiąt", "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt"]
_S = ["", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset",
      "siedemset", "osiemset", "dziewięćset"]
_G = [None, ("tysiąc", "tysiące", "tysięcy"), ("milion", "miliony", "milionów"),
      ("miliard", "miliardy", "miliardów")]


def odmiana(n, formy):
    if n == 1:
        return formy[0]
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return formy[1]
    return formy[2]


def _trojka(n):
    s, r = divmod(n, 100)
    w = [_S[s]]
    if 10 <= r < 20:
        w.append(_N[r - 10])
    else:
        w += [_D[r // 10], _J[r % 10]]
    return [x for x in w if x]


def slownie_int(n):
    if n == 0:
        return "zero"
    out, i = [], 0
    while n:
        n, g = divmod(n, 1000)
        if g:
            if i == 0:
                part = _trojka(g)
            elif g == 1:
                part = [_G[i][0]]  # "tysiąc", nie "jeden tysiąc"
            else:
                part = _trojka(g) + [odmiana(g, _G[i])]
            out = part + out
        i += 1
    return " ".join(out)


def slownie(kwota, waluta="PLN"):
    d = abs(kwota).quantize(Decimal("0.01"))
    zl, gr = int(d), int(d * 100) % 100
    jedn = odmiana(zl, ("złoty", "złote", "złotych")) if waluta == "PLN" else waluta
    minus = "minus " if kwota < 0 else ""
    return "%s%s %s %02d/100" % (minus, slownie_int(zl), jedn, gr)


# ------------------------------------------------------------ numer KSeF ----

KSEF_RE = re.compile(r"\d{10}-\d{8}-[0-9A-F]{12}-[0-9A-F]{2}")


def crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def data_pl(s):
    """2026-01-15 -> 15.01.2026 (takze w tekscie, np. zakres dat)."""
    return re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", r"\3.\2.\1", s or "")


def ksef_ok(nr):
    return bool(KSEF_RE.fullmatch(nr)) and "%02X" % crc8(nr[:32].encode()) == nr[33:]


# ------------------------------------------------------------- parsowanie ----


class El:
    """Cienka nakladka na element XML: e.t("Fa/P_2") -> tekst lub ""."""

    def __init__(self, el):
        self.el = el

    def __bool__(self):
        return self.el is not None

    def t(self, path):
        if self.el is None:
            return ""
        x = self.el.find(path)
        return (x.text or "").strip() if x is not None else ""

    def f(self, path):
        return El(self.el.find(path) if self.el is not None else None)

    def all(self, path):
        return [El(x) for x in self.el.findall(path)] if self.el is not None else []


def load(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise ValueError("plik nie jest poprawnym XML (%s)" % e)
    for el in root.iter():  # FA(2) i FA(3) maja rozne przestrzenie nazw - usuwamy je
        if isinstance(el.tag, str):
            el.tag = el.tag.rsplit("}", 1)[-1]
    if root.tag != "Faktura" or root.find("Fa") is None:
        raise ValueError("to nie jest faktura KSeF (FA) - element glowny: %s" % root.tag)
    return El(root)


def podmiot(p):
    d = p.f("DaneIdentyfikacyjne")
    if d.t("NIP"):
        ident = "NIP: " + d.t("NIP")
    elif d.t("NrVatUE"):
        ident = "VAT UE: %s%s" % (d.t("KodUE"), d.t("NrVatUE"))
    elif d.t("NrID"):
        ident = "ID: %s %s" % (d.t("KodKraju"), d.t("NrID"))
    else:
        ident = "brak identyfikatora podatkowego" if d.t("BrakID") else ""
    adr = [p.t("Adres/AdresL1"), p.t("Adres/AdresL2")]
    if p.t("Adres/KodKraju") not in ("", "PL"):
        adr.append(p.t("Adres/KodKraju"))
    kontakt = [x for k in p.all("DaneKontaktowe") for x in (k.t("Email"), k.t("Telefon")) if x]
    return {"nazwa": d.t("Nazwa"), "ident": ident, "nip": d.t("NIP"), "adres": [a for a in adr if a],
            "kontakt": kontakt, "nr_klienta": p.t("NrKlienta"),
            "rola": ROLA.get(p.t("Rola"), p.t("OpisRoli") or p.t("Rola"))}


def wiersze(fa):
    rows = []
    for w in fa.all("FaWiersz"):
        rows.append(dict(lp=w.t("NrWierszaFa"), nazwa=w.t("P_7"), indeks=w.t("Indeks"), gtin=w.t("GTIN"),
                         jm=w.t("P_8A"), ilosc=w.t("P_8B"), cena_n=w.t("P_9A"), cena_b=w.t("P_9B"),
                         rabat=w.t("P_10"), netto=w.t("P_11"), brutto=w.t("P_11A"),
                         stawka=w.t("P_12"), przed=w.t("StanPrzed") == "1"))
    for w in fa.all("Zamowienie/ZamowienieWiersz"):  # faktura zaliczkowa
        rows.append(dict(lp=w.t("NrWierszaZam"), nazwa=w.t("P_7Z"), indeks=w.t("IndeksZ"), gtin=w.t("GTINZ"),
                         jm=w.t("P_8AZ"), ilosc=w.t("P_8BZ"), cena_n=w.t("P_9AZ"), cena_b="",
                         rabat="", netto=w.t("P_11NettoZ"), brutto="",
                         stawka=w.t("P_12Z"), przed=w.t("StanPrzedZ") == "1"))
    return rows


def parse(path):
    f = load(path)
    fa = f.f("Fa")
    waluta = fa.t("KodWaluty") or "PLN"
    nr_ksef = next(iter(KSEF_RE.findall(os.path.basename(path))), "")
    inv = {
        "plik": os.path.basename(path), "schemat": f.f("Naglowek/KodFormularza").el.get("kodSystemowy", "")
        if f.f("Naglowek/KodFormularza") else "",
        "nr_ksef": nr_ksef, "ksef_ok": ksef_ok(nr_ksef) if nr_ksef else None,
        "rodzaj_kod": fa.t("RodzajFaktury"), "rodzaj": RODZAJ.get(fa.t("RodzajFaktury"), "Faktura"),
        "nr": fa.t("P_2"), "data": data_pl(fa.t("P_1")), "miejsce": fa.t("P_1M"),
        "data_dostawy": data_pl(fa.t("P_6") or " – ".join(
            x for x in (fa.t("OkresFa/P_6_Od"), fa.t("OkresFa/P_6_Do")) if x)),
        "waluta": waluta, "kurs": fa.t("KursWalutyZ"),
        "sprzedawca": podmiot(f.f("Podmiot1")), "nabywca": podmiot(f.f("Podmiot2")),
        "inne": [podmiot(p) for p in f.all("Podmiot3")],
        "wiersze": wiersze(fa), "razem": dec(fa.t("P_15")),
        "przyczyna": fa.t("PrzyczynaKorekty"), "typ_korekty": TYP_KOREKTY.get(fa.t("TypKorekty"), ""),
        "korygowane": [dict(nr=k.t("NrFaKorygowanej"), data=data_pl(k.t("DataWystFaKorygowanej")),
                            ksef=k.t("NrKSeFFaKorygowanej")) for k in fa.all("DaneFaKorygowanej")],
        "opis": [(o.t("Klucz"), o.t("Wartosc")) for o in fa.all("DodatkowyOpis")],
        "stopka": [s.t("StopkaFaktury") for s in f.all("Stopka/Informacje") if s.t("StopkaFaktury")],
        "rejestry": [(k, r.t(k)) for r in f.all("Stopka/Rejestry")
                     for k in ("PelnaNazwa", "KRS", "REGON", "BDO") if r.t(k)],
    }
    sumy = []
    for pn, pv, opis in SUMY:
        n, v = dec(fa.t(pn)), dec(fa.t(pv)) if pv else None
        if n is not None or v is not None:
            sumy.append(dict(opis=opis, netto=n or Decimal(0), vat=v or Decimal(0),
                             vat_pln=dec(fa.t(pv + "W")) if pv else None))
    inv["sumy"] = sumy

    adn = fa.f("Adnotacje")
    lista = [opis for pole, opis in ADNOTACJE if adn.t(pole) == "1"]
    if adn.t("Zwolnienie/P_19") == "1":
        podst = adn.t("Zwolnienie/P_19A") or adn.t("Zwolnienie/P_19B") or adn.t("Zwolnienie/P_19C")
        lista.append("Zwolnienie z VAT" + (": " + podst if podst else ""))
    if adn.t("PMarzy/P_PMarzy") == "1":
        lista.append("Procedura marży")
    if adn.t("NoweSrodkiTransportu/P_22") == "1":
        lista.append("Wewnątrzwspólnotowa dostawa nowych środków transportu")
    inv["adnotacje"] = lista

    pl = fa.f("Platnosc")
    rachunki = []
    for r in pl.all("RachunekBankowy") + pl.all("RachunekBankowyFaktora"):
        nrb = re.sub(r"\s", "", r.t("NrRB"))
        grupy = " ".join([nrb[:2]] + [nrb[i:i + 4] for i in range(2, len(nrb), 4)]) if nrb.isdigit() else nrb
        rachunki.append(" · ".join(x for x in (grupy, r.t("NazwaBanku"), r.t("SWIFT"), r.t("OpisRachunku")) if x))
    terminy = []
    for t in pl.all("TerminPlatnosci"):
        opis = " ".join(x for x in (t.t("TerminOpis/Ilosc"), t.t("TerminOpis/Jednostka"),
                                    t.t("TerminOpis/ZdarzeniePoczatkowe")) if x)
        terminy.append(" ".join(x for x in (data_pl(t.t("Termin")), "(%s)" % opis if opis else "") if x))
    forma = FORMA_PLATNOSCI.get(pl.t("FormaPlatnosci"), "") or pl.t("OpisPlatnosci")
    zaplacono = "zapłacono" + (" " + data_pl(pl.t("DataZaplaty")) if pl.t("DataZaplaty") else "") \
        if pl.t("Zaplacono") == "1" else ("zapłacono częściowo" if pl.t("ZnacznikZaplatyCzesciowej") == "1" else "")
    inv["platnosc"] = dict(forma=forma, terminy=terminy, rachunki=rachunki, zaplacono=zaplacono,
                           skonto=" – ".join(x for x in (pl.t("Skonto/WarunkiSkonta"),
                                                         pl.t("Skonto/WysokoscSkonta")) if x),
                           link=pl.t("LinkDoPlatnosci"))
    inv["do_zaplaty"] = dec(fa.t("Rozliczenie/DoZaplaty"))
    wt = fa.f("WarunkiTransakcji")
    inv["umowy"] = [" z ".join(x for x in (u.t("NrUmowy"), data_pl(u.t("DataUmowy"))) if x) for u in wt.all("Umowy")]
    inv["zamowienia"] = [" z ".join(x for x in (z.t("NrZamowienia"), data_pl(z.t("DataZamowienia"))) if x)
                         for z in wt.all("Zamowienia")]

    ostrz = []
    if nr_ksef and not inv["ksef_ok"]:
        ostrz.append("Numer KSeF z nazwy pliku ma niepoprawną sumę kontrolną.")
    if inv["rodzaj_kod"] == "VAT" and inv["razem"] is not None and sumy:
        suma = sum(s["netto"] + s["vat"] for s in sumy)
        if abs(suma - inv["razem"]) > Decimal("0.01"):
            ostrz.append("Suma kwot ze stawek (%s) różni się od kwoty należności (%s)."
                         % (money(suma), money(inv["razem"])))
    inv["ostrzezenia"] = ostrz
    return inv


# ------------------------------------------------------------------- HTML ----

CSS = """
@page { size: A4; margin: 14mm 12mm; }
* { box-sizing: border-box; }
body { font: 10pt/1.4 "Segoe UI", Arial, sans-serif; color: #111; margin: 0 auto; max-width: 190mm; padding: 8px; }
h1 { font-size: 16pt; margin: 0; } h2 { font-size: 10.5pt; margin: 14px 0 4px; text-transform: uppercase;
  letter-spacing: .04em; color: #444; border-bottom: 1px solid #ccc; padding-bottom: 2px; }
.top { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.meta { text-align: right; } .meta div { white-space: nowrap; }
.muted { color: #666; font-size: 8.5pt; } .ksef { font-family: Consolas, monospace; font-size: 9pt; }
.strony { display: flex; gap: 16px; margin-top: 12px; } .strony > div { flex: 1; border: 1px solid #ddd;
  border-radius: 4px; padding: 8px 10px; } .strony b { display: block; font-size: 10.5pt; }
.lbl { font-size: 8pt; text-transform: uppercase; letter-spacing: .05em; color: #666; }
table { width: 100%; border-collapse: collapse; margin-top: 4px; }
th, td { border: 1px solid #ccc; padding: 3px 5px; vertical-align: top; }
th { background: #f0f0f0; font-size: 8.5pt; text-align: left; }
td.n, th.n { text-align: right; white-space: nowrap; }
tr.przed td { color: #777; font-style: italic; }
tr { page-break-inside: avoid; }
.razem { margin-top: 10px; text-align: right; font-size: 13pt; } .razem small { display: block; font-size: 9pt; color: #444; }
.uwaga { border: 1px solid #c00; background: #fff3f3; color: #900; padding: 6px 10px; margin-top: 10px; border-radius: 4px; }
.kor { border: 1px solid #c80; background: #fffaf0; padding: 6px 10px; margin-top: 10px; border-radius: 4px; }
ul { margin: 2px 0; padding-left: 18px; }
.stopka { margin-top: 18px; border-top: 1px solid #ccc; padding-top: 6px; }
"""


def _e(s):
    return html.escape(str(s or ""))


def _strona(tytul, p):
    if not p["nazwa"] and not p["ident"]:
        return ""
    lines = [_e(x) for x in p["adres"] + [p["ident"]] + p["kontakt"] if x]
    if p["nr_klienta"]:
        lines.append("Nr klienta: " + _e(p["nr_klienta"]))
    return '<div><span class="lbl">%s</span><b>%s</b>%s</div>' % (_e(tytul), _e(p["nazwa"]), "<br>".join(lines))


def render(inv):
    h = ['<!doctype html><html lang="pl"><head><meta charset="utf-8">',
         "<title>%s %s</title><style>%s</style></head><body>" % (_e(inv["rodzaj"]), _e(inv["nr"]), CSS)]
    meta = ["Data wystawienia: <b>%s</b>" % _e(inv["data"])]
    if inv["miejsce"]:
        meta.append("Miejsce: " + _e(inv["miejsce"]))
    if inv["data_dostawy"]:
        meta.append("Data dostawy / usługi: " + _e(inv["data_dostawy"]))
    ksef = ('<div class="ksef">Nr KSeF: %s%s</div>' % (_e(inv["nr_ksef"]), "" if inv["ksef_ok"] else " ⚠")
            if inv["nr_ksef"] else '<div class="muted">Nr KSeF: brak w nazwie pliku</div>')
    h.append('<div class="top"><div><h1>%s</h1><div style="font-size:12pt">nr <b>%s</b></div>%s</div>'
             '<div class="meta">%s</div></div>' % (_e(inv["rodzaj"]), _e(inv["nr"]), ksef,
                                                    "".join("<div>%s</div>" % m for m in meta)))
    for o in inv["ostrzezenia"]:
        h.append('<div class="uwaga">⚠ %s</div>' % _e(o))
    h.append('<div class="strony">%s%s</div>' % (_strona("Sprzedawca", inv["sprzedawca"]),
                                                  _strona("Nabywca", inv["nabywca"])))
    if inv["inne"]:
        h.append('<div class="strony">%s</div>' % "".join(_strona(p["rola"] or "Inny podmiot", p)
                                                          for p in inv["inne"]))
    if inv["rodzaj_kod"].startswith("KOR"):
        kor = ["<b>Korekta</b>"]
        if inv["przyczyna"]:
            kor.append("Przyczyna: " + _e(inv["przyczyna"]))
        if inv["typ_korekty"]:
            kor.append("Skutek: " + _e(inv["typ_korekty"]))
        for k in inv["korygowane"]:
            kor.append("Faktura korygowana: %s z %s%s" % (_e(k["nr"]), _e(k["data"]),
                                                          " (KSeF: %s)" % _e(k["ksef"]) if k["ksef"] else ""))
        h.append('<div class="kor">%s</div>' % "<br>".join(kor))

    rows = inv["wiersze"]
    if rows:
        brutto = any(r["brutto"] for r in rows) and not any(r["netto"] for r in rows)
        rabat = any(r["rabat"] for r in rows)
        h.append("<h2>Pozycje</h2><table><thead><tr><th>Lp</th><th>Nazwa</th><th class=n>Ilość</th><th>J.m.</th>"
                 "<th class=n>Cena %s</th>%s<th class=n>Stawka</th><th class=n>Wartość %s</th></tr></thead><tbody>"
                 % ("brutto" if brutto else "netto", "<th class=n>Rabat</th>" if rabat else "",
                    "brutto" if brutto else "netto"))
        for r in rows:
            extra = " · ".join(x for x in (r["indeks"], r["gtin"] and "GTIN " + r["gtin"]) if x)
            stawka = STAWKA.get(r["stawka"], r["stawka"] + "%" if r["stawka"].isdigit() else r["stawka"])
            h.append('<tr%s><td>%s</td><td>%s%s%s</td><td class=n>%s</td><td>%s</td><td class=n>%s</td>%s'
                     '<td class=n>%s</td><td class=n>%s</td></tr>' % (
                         ' class="przed"' if r["przed"] else "", _e(r["lp"]),
                         "<small>[przed korektą]</small> " if r["przed"] else "", _e(r["nazwa"]),
                         '<br><span class="muted">%s</span>' % _e(extra) if extra else "",
                         _e(qty(r["ilosc"])), _e(r["jm"]),
                         _e(money(r["cena_b"] if brutto else r["cena_n"] or r["cena_b"])),
                         "<td class=n>%s</td>" % _e(money(r["rabat"])) if rabat else "",
                         _e(stawka), _e(money(r["brutto"] if brutto else r["netto"] or r["brutto"]))))
        h.append("</tbody></table>")

    if inv["sumy"]:
        w_pln = any(s["vat_pln"] is not None for s in inv["sumy"])
        h.append("<h2>Podsumowanie VAT</h2><table><tr><th>Stawka</th><th class=n>Netto</th><th class=n>VAT</th>"
                 "<th class=n>Brutto</th>%s</tr>" % ("<th class=n>VAT w PLN</th>" if w_pln else ""))
        for s in inv["sumy"]:
            h.append("<tr><td>%s</td><td class=n>%s</td><td class=n>%s</td><td class=n>%s</td>%s</tr>" % (
                _e(s["opis"]), money(s["netto"]), money(s["vat"]), money(s["netto"] + s["vat"]),
                "<td class=n>%s</td>" % money(s["vat_pln"]) if w_pln else ""))
        tn, tv = sum(s["netto"] for s in inv["sumy"]), sum(s["vat"] for s in inv["sumy"])
        h.append("<tr><th>Razem</th><th class=n>%s</th><th class=n>%s</th><th class=n>%s</th>%s</tr></table>"
                 % (money(tn), money(tv), money(tn + tv), "<th></th>" if w_pln else ""))
    kwota = inv["do_zaplaty"] if inv["do_zaplaty"] is not None else inv["razem"]
    if kwota is not None:
        h.append('<div class="razem">%s: <b>%s %s</b><small>słownie: %s</small>%s</div>' % (
            "Do zapłaty" if inv["do_zaplaty"] is not None else "Kwota należności ogółem",
            money(kwota), _e(inv["waluta"]), _e(slownie(kwota, inv["waluta"])),
            "<small>kurs waluty: %s</small>" % _e(inv["kurs"]) if inv["kurs"] else ""))

    pl = inv["platnosc"]
    pay = [x for x in (pl["forma"] and "Forma: " + pl["forma"],
                       pl["terminy"] and "Termin: " + ", ".join(pl["terminy"]),
                       pl["zaplacono"] and pl["zaplacono"].capitalize(),
                       pl["skonto"] and "Skonto: " + pl["skonto"]) if x]
    pay += ["Rachunek: <span class=ksef>%s</span>" % _e(r) for r in pl["rachunki"]]
    if pl["link"]:
        pay.append("Link do płatności: " + _e(pl["link"]))
    if pay:
        h.append("<h2>Płatność</h2>" + "<br>".join(x if x.startswith("Rachunek") else _e(x) for x in pay))
    if inv["umowy"] or inv["zamowienia"]:
        h.append("<h2>Warunki transakcji</h2>" + "<br>".join(
            ["Umowa: " + _e(u) for u in inv["umowy"]] + ["Zamówienie: " + _e(z) for z in inv["zamowienia"]]))
    if inv["adnotacje"]:
        h.append("<h2>Adnotacje</h2><ul>%s</ul>" % "".join("<li>%s</li>" % _e(a) for a in inv["adnotacje"]))
    if inv["opis"]:
        h.append("<h2>Informacje dodatkowe</h2><table>%s</table>" % "".join(
            "<tr><th style='width:35%%'>%s</th><td>%s</td></tr>" % (_e(k), _e(v)) for k, v in inv["opis"]))
    stopka = [_e(s) for s in inv["stopka"]] + ["%s: %s" % (_e(k), _e(v)) for k, v in inv["rejestry"]]
    h.append('<div class="stopka">%s<div class="muted">Wizualizacja wygenerowana lokalnie z pliku %s (%s). '
             "Oryginałem faktury jest plik XML w KSeF.</div></div></body></html>" % (
                 "<br>".join(stopka) + ("<br>" if stopka else ""), _e(inv["plik"]), _e(inv["schemat"])))
    return "".join(h)


# -------------------------------------------------------------------- PDF ----


def find_edge():
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                 os.environ.get("LOCALAPPDATA")):
        p = os.path.join(base or "", "Microsoft", "Edge", "Application", "msedge.exe")
        if base and os.path.isfile(p):
            return p
    return None


def to_pdf(edge, html_path, pdf_path):
    with tempfile.TemporaryDirectory() as prof:  # osobny profil - nie koliduje z otwartym Edge
        subprocess.run([edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        "--user-data-dir=" + prof, "--print-to-pdf=" + os.path.abspath(pdf_path),
                        "file:///" + os.path.abspath(html_path).replace("\\", "/")],
                       capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not os.path.isfile(pdf_path):
        raise RuntimeError("Edge nie utworzyl pliku PDF")


# ------------------------------------------------------------------- wsad ----

CSV_COLS = ["plik", "nr_ksef", "rodzaj", "nr", "data", "sprzedawca", "nip_sprzedawcy", "nabywca", "nip_nabywcy",
            "netto", "vat", "brutto", "waluta", "termin", "rachunek"]


def run(inp, out_dir, pdf=True, log=print):
    if os.path.isfile(inp):
        files = [inp]
    else:
        files = sorted(os.path.join(inp, f) for f in os.listdir(inp) if f.lower().endswith(".xml"))
    if not files:
        log("Brak plikow XML w: %s" % inp)
        return []
    edge = find_edge() if pdf else None
    if pdf and not edge:
        log("UWAGA: nie znaleziono Microsoft Edge - powstana tylko pliki HTML.")
    os.makedirs(out_dir, exist_ok=True)
    zest, res = [], []
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        log("Przetwarzam: %s" % os.path.basename(f))
        try:
            inv = parse(f)
            dst = os.path.join(out_dir, name + ".html")
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(render(inv))
            if edge:
                to_pdf(edge, dst, os.path.join(out_dir, name + ".pdf"))
            for o in inv["ostrzezenia"]:
                log("  UWAGA: " + o)
            tn = sum((s["netto"] for s in inv["sumy"]), Decimal(0))
            tv = sum((s["vat"] for s in inv["sumy"]), Decimal(0))
            zest.append([inv["plik"], inv["nr_ksef"], inv["rodzaj_kod"], inv["nr"], inv["data"],
                         inv["sprzedawca"]["nazwa"], inv["sprzedawca"]["nip"],
                         inv["nabywca"]["nazwa"], inv["nabywca"]["nip"],
                         # bez odstepu tysiecy - Excel PL rozpozna liczbe i zsumuje kolumne
                         money(tn).replace(" ", ""), money(tv).replace(" ", ""),
                         money(inv["razem"]).replace(" ", "") if inv["razem"] is not None else "",
                         inv["waluta"], ", ".join(inv["platnosc"]["terminy"]),
                         ", ".join(inv["platnosc"]["rachunki"])])
            res.append(dst)
            log("  OK: %s nr %s, %s %s" % (inv["rodzaj"], inv["nr"], money(inv["razem"]), inv["waluta"]))
        except ValueError as e:
            log("  POMINIETO: %s" % e)
        except Exception:
            log("  BLAD:\n" + traceback.format_exc())
    if len(files) > 1 and zest:
        path = os.path.join(out_dir, "zestawienie.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:  # BOM + ';' - Excel PL otwiera od razu
            w = csv.writer(fh, delimiter=";")
            w.writerow(CSV_COLS)
            w.writerows(zest)
        log("Zestawienie: %s" % path)
    log("Zakonczono: %d z %d plikow." % (len(res), len(files)))
    return res


# -------------------------------------------------------------------- GUI ----


def gui():
    import tkinter as tk
    from tkinter import filedialog, ttk, scrolledtext

    root = tk.Tk()
    root.title("Podglad e-faktur KSeF (XML -> PDF/HTML)")
    root.geometry("780x520")
    pad = dict(padx=6, pady=3)

    v_in = tk.StringVar(value=os.path.join(APP_DIR, "INPUT"))
    v_out = tk.StringVar(value=os.path.join(APP_DIR, "OUTPUT"))
    v_pdf = tk.BooleanVar(value=find_edge() is not None)

    f = ttk.Frame(root)
    f.pack(fill="x", **pad)
    ttk.Label(f, text="Plik XML lub folder:").grid(row=0, column=0, sticky="w", **pad)
    ttk.Entry(f, textvariable=v_in, width=60).grid(row=0, column=1, **pad)
    ttk.Button(f, text="Plik...", command=lambda: v_in.set(filedialog.askopenfilename(
        filetypes=[("Faktura KSeF", "*.xml")]) or v_in.get())).grid(row=0, column=2, **pad)
    ttk.Button(f, text="Folder...", command=lambda: v_in.set(
        filedialog.askdirectory() or v_in.get())).grid(row=0, column=3, **pad)
    ttk.Label(f, text="Folder wyjsciowy:").grid(row=1, column=0, sticky="w", **pad)
    ttk.Entry(f, textvariable=v_out, width=60).grid(row=1, column=1, **pad)
    ttk.Button(f, text="Wybierz...", command=lambda: v_out.set(
        filedialog.askdirectory() or v_out.get())).grid(row=1, column=2, **pad)
    ttk.Checkbutton(f, text="Utworz tez PDF (przez Microsoft Edge)", variable=v_pdf).grid(
        row=2, column=1, sticky="w", **pad)

    log_box = scrolledtext.ScrolledText(root, height=18)
    log_box.pack(fill="both", expand=True, **pad)

    def log(msg):
        def put():
            log_box.insert("end", str(msg) + "\n")
            log_box.see("end")
        root.after(0, put)

    bar = ttk.Frame(root)
    bar.pack(pady=6)
    btn = ttk.Button(bar, text="Generuj podglad")
    btn.pack(side="left", padx=4)
    ttk.Button(bar, text="Otworz folder wynikow", command=lambda: os.startfile(v_out.get())
               if os.path.isdir(v_out.get()) else None).pack(side="left", padx=4)

    def start():
        inp, out = v_in.get().strip('" '), v_out.get().strip('" ')
        if not os.path.exists(inp):
            return log("Wskaz istniejacy plik XML lub folder.")
        if not out:
            return log("Wskaz folder wyjsciowy.")
        btn.config(state="disabled")
        log_box.delete("1.0", "end")

        def work():
            try:
                run(inp, out, v_pdf.get(), log)
            finally:
                root.after(0, lambda: btn.config(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    btn.config(command=start)
    if "--selftest" in sys.argv:
        root.after(200, root.destroy)
    root.mainloop()


# --------------------------------------------------------------- selftest ----


def selftest():
    D = Decimal
    assert slownie(D("0.05")) == "zero złotych 05/100"
    assert slownie(D("1")) == "jeden złoty 00/100"
    assert slownie(D("22.10")) == "dwadzieścia dwa złote 10/100"
    assert slownie(D("1234.56")) == "tysiąc dwieście trzydzieści cztery złote 56/100"
    assert slownie(D("12015")) == "dwanaście tysięcy piętnaście złotych 00/100"
    assert slownie(D("2000000")) == "dwa miliony złotych 00/100"
    assert slownie(D("-113.5"), "EUR") == "minus sto trzynaście EUR 50/100"
    assert money("33569.5") == "33 569,50" and qty("7.000") == "7" and qty("1.25") == "1,25"
    assert ksef_ok("5265877635-20250826-0100001AF629-AF")  # przyklad z dokumentacji MF
    assert not ksef_ok("5265877635-20250826-0100001AF629-AE")

    ex = os.path.join(APP_DIR, "przyklad")
    mf = parse(os.path.join(ex, "faktura_MF_FA3.xml"))  # oficjalny przyklad z CIRFMF/ksef-pdf-generator
    assert mf["nr"] == "FA/YUCFO-4342344706/03/2026" and mf["schemat"] == "FA (3)"
    assert len(mf["wiersze"]) == 13 and mf["razem"] == D("33569.50") and not mf["ostrzezenia"]
    assert mf["platnosc"]["forma"] == "płatność mobilna" and mf["sprzedawca"]["nip"] == "5265877635"

    kor_path = os.path.join(ex, "5265877635-20260115-0100001AF629-33_korekta.xml")
    kor = parse(kor_path)
    assert kor["rodzaj"] == "Faktura korygująca" and kor["nr_ksef"] and kor["ksef_ok"] is True
    assert [s["opis"] for s in kor["sumy"]] == ["23% / 22%", "8% / 7%", "zwolnione"]
    assert kor["inne"][0]["rola"] == "JST – odbiorca"
    assert sum(r["przed"] for r in kor["wiersze"]) == 1
    assert kor["platnosc"]["rachunki"][0].startswith("12 1020 1026 0000 0402 0123 4567")
    assert "Mechanizm podzielonej płatności" in kor["adnotacje"]
    assert kor["data"] == "15.01.2026" and kor["platnosc"]["terminy"] == ["29.01.2026"]
    assert kor["korygowane"][0]["data"] == "05.01.2026" and data_pl("2026-01-01 – 2026-01-31") == "01.01.2026 – 31.01.2026"
    page = render(kor)
    for txt in ("Faktura korygująca", "Gmina Przykładowo", "Urząd Gminy Przykładowo", "[przed korektą]",
                "minus", "Umowa: ZP.272.15.2025", "BDO: 000012345"):
        assert txt in page, txt
    assert "<script" not in render(dict(kor, nr="<script>alert(1)</script>"))  # wartosci sa escapowane

    tmp = tempfile.mkdtemp()
    bad = os.path.join(tmp, "upo.xml")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("<Potwierdzenie/>")
    try:
        parse(bad)
        raise AssertionError("UPO nie powinno przejsc jako faktura")
    except ValueError:
        pass

    msgs = []
    res = run(ex, tmp, pdf=find_edge() is not None, log=msgs.append)
    assert len(res) == 2 and os.path.isfile(os.path.join(tmp, "zestawienie.csv")), msgs
    with open(os.path.join(tmp, "zestawienie.csv"), encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    assert rows[0] == CSV_COLS and len(rows) == 3 and rows[2][11] == "33569,50", rows
    if find_edge():
        assert os.path.isfile(os.path.join(tmp, "faktura_MF_FA3.pdf"))
    print("selftest OK" + (" (z PDF przez Edge)" if find_edge() else " (bez PDF - brak Edge)"))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        if "--gui" in sys.argv:
            gui()
    else:
        gui()
