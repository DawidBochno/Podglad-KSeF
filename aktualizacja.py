"""Sprawdzanie i pobieranie aktualizacji programu z GitHuba.

Ten sam plik jest w kazdym repo "Programow pomocniczych" - zmieniac wszedzie
naraz (wzorzec: _wspolne/aktualizacja.py w folderze glownym).

Jak dziala: pobiera z GitHub API liste plikow galezi glownej z ich hashami
gita (sha1 "blob") i porownuje z plikami na dysku. Nie trzeba pilnowac
numerow wersji. Pobierane sa tylko zmienione pliki, z adresu przypietego do
konkretnego commita (bez cache'owanych, nieaktualnych kopii), a kazdy plik
jest sprawdzany hashem przed zapisem.

Wysylane jest tylko zapytanie o liste plikow - zadne dokumenty ani dane.
Wylaczenie: pusty plik NIE_AKTUALIZUJ obok programu.
Kopia z gita (folder .git) nie jest aktualizowana - tam sluzy "git pull".
Wersja .exe tylko informuje o nowej wersji (exe nie ma w repo).
"""
import hashlib
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
DIR = Path(sys.executable if FROZEN else __file__).resolve().parent
SKIP = (".github/", ".gitignore", ".gitattributes")
DATA = ("input/", "output/", "przyklad/")  # tylko dogrywane, gdy ich brak
HEADERS = {"User-Agent": "Programy-pomocnicze-aktualizacja"}


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def blob_sha(data):
    """Hash pliku tak, jak liczy go git. Tekst w repo ma konce linii LF,
    na dysku Windows bywa CRLF - dla tekstu (brak bajtu 0) ujednolicamy."""
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def changed(tree, folder=DIR):
    """Pliki z repo, ktorych na dysku brak albo maja inna tresc."""
    out = []
    for e in tree:
        p = e["path"]
        if e["type"] != "blob" or p.startswith(SKIP):
            continue
        f = folder / p
        if not f.is_file():
            out.append(e)
        elif p.lower().startswith(DATA):
            continue  # pliki przykladow uzytkownik mogl zmienic - nie nadpisywac
        elif blob_sha(f.read_bytes()) != e["sha"]:
            out.append(e)
    return out


def latest(repo, branch, path=None):
    """Ostatni commit galezi (opcjonalnie: ostatni zmieniajacy plik path)."""
    q = "?sha=%s&per_page=1" % branch + ("&path=" + urllib.parse.quote(path) if path else "")
    return json.loads(_get("https://api.github.com/repos/%s/commits%s" % (repo, q)))[0]


def check(repo, branch):
    """Zwraca (sha commita, lista zmienionych plikow)."""
    sha = latest(repo, branch)["sha"]
    tree = json.loads(_get("https://api.github.com/repos/%s/git/trees/%s?recursive=1"
                           % (repo, sha)))["tree"]
    return sha, changed(tree)


def download(repo, sha, entries, folder=DIR, get=_get):
    root = folder.resolve()
    for e in entries:
        dst = (root / e["path"]).resolve()
        if root not in dst.parents:
            raise ValueError("niedozwolona sciezka: %s" % e["path"])
        data = get("https://raw.githubusercontent.com/%s/%s/%s"
                   % (repo, sha, urllib.parse.quote(e["path"])), 120)
        if blob_sha(data) != e["sha"]:
            raise ValueError("uszkodzony plik: %s" % e["path"])
        if dst.suffix.lower() in (".bat", ".cmd"):  # cmd.exe zle czyta .bat z LF
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".nowy")
        tmp.write_bytes(data)
        os.replace(tmp, dst)  # plik po pliku; przerwane pobieranie dokonczy sie przy nastepnym starcie


def start(root, repo, branch, main_file):
    """Sprawdza aktualizacje w tle po otwarciu okna; pyta przed pobraniem.
    Brak sieci, UTM, limit API - po cichu nic (program ma dzialac offline)."""
    if ("--selftest" in sys.argv or (DIR / "NIE_AKTUALIZUJ").exists()
            or (DIR / ".git").exists()):
        return
    from tkinter import messagebox

    def show(fn, *a):
        root.after(0, lambda: fn("Aktualizacja", *a, parent=root))

    def work():
        try:
            if FROZEN:
                c = latest(repo, branch, main_file)
                when = c["commit"]["committer"]["date"][:10]
                built = os.path.getmtime(sys.executable)
                import datetime
                if when > datetime.date.fromtimestamp(built).isoformat():
                    show(messagebox.showinfo,
                         "Jest nowsza wersja programu (z %s).\n"
                         "Poproś administratora o nową paczkę albo pobierz ją z:\n"
                         "https://github.com/%s" % (when, repo))
                return
            sha, entries = check(repo, branch)
        except Exception:
            return
        if entries:
            root.after(0, ask, sha, entries)

    def ask(sha, entries):
        if messagebox.askyesno(
                "Aktualizacja",
                "Jest nowa wersja programu (zmienione pliki: %d).\n\n"
                "Pobrać i zainstalować teraz?\n"
                "Twoje pliki w INPUT i OUTPUT nie zostaną ruszone." % len(entries),
                parent=root):
            threading.Thread(target=apply, args=(sha, entries), daemon=True).start()

    def apply(sha, entries):
        try:
            download(repo, sha, entries)
        except Exception as e:
            return show(messagebox.showerror, "Nie udało się pobrać aktualizacji:\n%s" % e)
        msg = "Zaktualizowano. Zamknij i uruchom program ponownie."
        if any(e["path"] == "requirements.txt" for e in entries):
            msg += "\n\nZmieniły się biblioteki - uruchom raz install.bat."
        show(messagebox.showinfo, msg)

    threading.Thread(target=work, daemon=True).start()


def selftest():
    """Bez sieci: hash gita, wykrywanie zmian, pobieranie z podstawionym get."""
    import tempfile

    hello = "ce013625030ba8dba906f756967f9e9ca394464a"  # git hash-object (hello\n)
    assert blob_sha(b"hello\n") == hello and blob_sha(b"hello\r\n") == hello
    d = Path(tempfile.mkdtemp())
    (d / "a.py").write_bytes(b"hello\r\n")
    (d / "b.py").write_bytes(b"stare\n")
    (d / "przyklad").mkdir()
    (d / "przyklad" / "szablon.docx").write_bytes(b"zmieniony przez uzytkownika")
    tree = [{"path": p, "type": "blob", "sha": blob_sha(c)} for p, c in
            (("a.py", b"hello\n"), ("b.py", b"nowe\n"), ("x/c.bat", b"@echo 1\n"),
             (".github/workflows/t.yml", b"x"), ("przyklad/szablon.docx", b"wzor"))]
    tree.append({"path": "x", "type": "tree", "sha": "0"})
    todo = changed(tree, d)
    assert [e["path"] for e in todo] == ["b.py", "x/c.bat"], todo
    files = {"b.py": b"nowe\n", "x/c.bat": b"@echo 1\n"}
    download("r", "s", todo, d, lambda url, t: files[url.rsplit("/s/", 1)[1]])
    assert (d / "b.py").read_bytes() == b"nowe\n"
    assert (d / "x" / "c.bat").read_bytes() == b"@echo 1\r\n"
    assert changed(tree, d) == []
    for bad in ({"path": "../zly.py", "sha": blob_sha(b"z")},
                {"path": "b.py", "sha": "0" * 40}):
        try:
            download("r", "s", [bad], d, lambda url, t: b"z")
            raise AssertionError("przepuszczono: %s" % bad)
        except ValueError:
            pass
