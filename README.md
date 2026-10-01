# Podgląd e-faktur KSeF (XML → PDF / HTML)

Program **lokalny** — działa w całości na Twoim komputerze i nigdzie nie
wysyła faktur. Zamienia plik XML faktury ustrukturyzowanej z KSeF na
czytelną fakturę do przeczytania, wydrukowania, opisania i podpięcia
pod dokumenty księgowe.

Obsługuje schematy **FA(3)** (obowiązujący od 2026 r.) i **FA(2)**.

## Co pokazuje

- **Nagłówek:** rodzaj faktury (VAT, korygująca, zaliczkowa, rozliczeniowa,
  uproszczona), numer, data i miejsce wystawienia, data dostawy lub okres,
  **numer KSeF**.
- **Sprzedawca, nabywca i inne podmioty** (np. *JST – odbiorca*, faktor,
  dodatkowy nabywca): nazwa, NIP / VAT UE / inny identyfikator, adres,
  kontakt, numer klienta.
- **Korekta:** przyczyna, skutek, numer, data i numer KSeF faktury
  korygowanej. Pozycje „przed korektą” są wyszarzone i oznaczone.
- **Pozycje:** nazwa, indeks, GTIN, ilość, j.m., cena, rabat, stawka,
  wartość (netto albo brutto, zależnie od faktury). Nagłówek tabeli
  powtarza się na każdej stronie PDF.
- **Podsumowanie VAT** według stawek (także 0%, zw, np, oo, marża, OSS),
  a przy walucie obcej również VAT w PLN.
- **Kwota należności ogółem** albo **Do zapłaty** z kwotą słownie, np.
  *tysiąc dwieście trzydzieści cztery złote 56/100*.
- **Płatność:** forma, termin, „zapłacono” z datą, rachunek bankowy
  w czytelnych grupach cyfr, skonto, link do płatności.
- **Adnotacje** (MPP, odwrotne obciążenie, metoda kasowa, zwolnienie
  z podstawą prawną, marża…), **umowy i zamówienia**, informacje
  dodatkowe, stopka, KRS, REGON i BDO.

### Kontrole

- **Numer KSeF** jest odczytywany z nazwy pliku (tak nazywa pliki KSeF
  przy pobieraniu, np. `5265877635-20260115-0100001AF629-33.xml`)
  i sprawdzany **sumą kontrolną CRC-8** według dokumentacji MF. Błędny
  numer daje ostrzeżenie na fakturze.
- **Suma kwot ze stawek** jest porównywana z kwotą należności. Niezgodność
  daje ostrzeżenie na fakturze i w logu.
- Pliki, które nie są fakturą (np. UPO), są pomijane z komunikatem.

## Folder faktur naraz

Wskaż folder. Każdy plik `.xml` dostaje swój `.html` i `.pdf`, a do tego
powstaje **`zestawienie.csv`**: plik, numer KSeF, rodzaj, numer, data,
sprzedawca, NIP, nabywca, netto, VAT, brutto, waluta, termin i rachunek.
Otwiera się bezpośrednio w Excelu, z polskimi znakami i kwotami jako
liczbami, więc kolumny można sumować.

## PDF

PDF jest drukowany przez **Microsoft Edge**, który jest w każdym Windows
10/11. Nic nie trzeba instalować. Edge działa w tle, bez okna, na osobnym
profilu, więc nie przeszkadza w otwartej przeglądarce. Bez Edge powstają
same pliki HTML, które można otworzyć i wydrukować w dowolnej przeglądarce.

## Szybki start

1. `install.bat` — sprawdza Pythona i uruchamia self-test. Program używa
   **tylko biblioteki standardowej Pythona**, nic nie jest doinstalowywane.
2. Wrzuć pliki XML do folderu `INPUT`.
3. `uruchom.bat` → **Generuj podgląd** → **Otwórz folder wyników**.

Wymaga Pythona 3.9+ z opcjami „Add python.exe to PATH” i „tcl/tk and IDLE”.

## Przykłady

Folder [`przyklad/`](przyklad/):
- `faktura_MF_FA3.xml` — oficjalna faktura testowa Ministerstwa Finansów
  z repozytorium [CIRFMF/ksef-pdf-generator](https://github.com/CIRFMF/ksef-pdf-generator)
  (licencja MIT), dane wygenerowane;
- `5265877635-20260115-0100001AF629-33_korekta.xml` — fikcyjna faktura
  korygująca dla gminy: JST jako odbiorca, trzy stawki, pozycja przed
  korektą, MPP, zwolnienie, umowa.

Oba pliki są zgodne z oficjalnym schematem `schemat_FA(3)_v1-0E.xsd`.

## Ograniczenia

- **Wizualizacja nie jest oryginałem faktury.** Oryginałem jest plik XML
  w KSeF, co jest napisane w stopce każdego podglądu.
- Kody QR weryfikacji (KOD I / KOD II), wymagane na wizualizacjach faktur
  przekazywanych poza KSeF, **nie są generowane**. Podgląd służy do
  odczytu i obiegu wewnętrznego.
- Załączniki ustrukturyzowane FA(3) (`Zalacznik`) i szczegóły transportu
  nie są wyświetlane.

## Testy

```bash
python podglad_ksef.py --selftest
```

Test sprawdza kwotę słownie (odmiany, tysiące, miliony, kwoty ujemne,
waluty), sumę kontrolną numeru KSeF z przykładu w dokumentacji MF,
odczyt obu przykładów (pozycje, stawki, korekta, JST, rachunek,
adnotacje, polskie daty), escapowanie HTML, odrzucenie pliku UPO,
zestawienie CSV oraz PDF przez Edge, jeśli Edge jest zainstalowany.
Testy uruchamiają się automatycznie przy każdym PR (GitHub Actions).
