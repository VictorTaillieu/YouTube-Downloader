# Refonte YouTube-Downloader — plan par itérations

## Contexte
`ytdownload.py` (103 lignes) est un wrapper Tkinter autour de la CLI `yt-dlp`. Problèmes actuels :
- `subprocess.call` sur le thread UI → la fenêtre gèle pendant tout le téléchargement, aucun retour (progression, erreurs).
- Bug : `cmd.append("-f m4a")` passe un seul argument `"-f m4a"` (le format reçu est `" m4a"`).
- `--write-sub` ne récupère que les sous-titres manuels, sans choix de langue.
- `urls.txt` + placeholder "Enter video URL..." comme déclencheur du mode multi-URL : fragile.
- `yt-dlp.exe` versionné dans git, aucune gestion de ffmpeg (nécessaire pour fusionner vidéo+audio > 720p, convertir en mp3, intégrer les sous-titres).
- Depuis fin 2025, yt-dlp a besoin d'un runtime JavaScript externe (Deno recommandé) pour supporter YouTube pleinement — à gérer sinon des formats manquent ou ça casse. (À revérifier dans la doc yt-dlp au moment de l'itération 1.)
- Fenêtre `topmost`, layout `place()` en positions relatives, chemins d'images relatifs au cwd.

Choix validés : **PySide6**, fonctionnalités **choix format/qualité + sous-titres + UI non bloquante avec % / vitesse / ETA + annulation**, distribution **exécutables PyInstaller** (Windows + Ubuntu).

## Choix d'architecture recommandé
**Garder yt-dlp comme binaire externe, mais géré par l'app** (et non l'importer comme lib Python) :
- Dans un exe PyInstaller, une lib yt-dlp embarquée ne peut pas se mettre à jour ; or YouTube casse yt-dlp régulièrement. Un binaire officiel (`yt-dlp.exe` / `yt-dlp_linux`) stocké dans le dossier de données utilisateur se met à jour via `yt-dlp -U` → le bouton "Mettre à jour" reste utile.
- `QProcess` (Qt) lance le process de façon asynchrone → pas de threads à gérer, annulation = `kill()`.
- Infos vidéo via `yt-dlp -J <url>` (JSON : titre, durée, miniature, formats, sous-titres).
- Progression via `--newline --progress-template "download:%(progress)j"` → une ligne JSON par tick, parsée pour %/vitesse/ETA.

Structure cible :
```
pyproject.toml            # uv, deps: PySide6 ; dev: pytest, ruff, pyinstaller
src/ytdownloader/
  __main__.py             # point d'entrée
  tools.py                # localiser/télécharger/mettre à jour yt-dlp, ffmpeg, deno (QStandardPaths.AppDataLocation)
  ytdlp.py                # construction des arguments (pure, testable) + parsing JSON/progression
  jobs.py                 # wrapper QProcess : signaux progress/finished/error, cancel
  ui/main_window.py       # fenêtre principale
  resources/logo.png
tests/test_ytdlp.py       # tests unitaires sur build_args / parse_progress / parse_formats
```

## Suivi
Ce plan est copié dans le dépôt sous `docs/PLAN.md`. À la fin de chaque itération, je mets à jour ce fichier : statut (✅ fait / 🚧 en cours / ⏳ à faire), résumé de ce qui a été réellement fait, écarts par rapport au plan, points à tester. Je m'arrête ensuite pour ta relecture et ton commit.

## État d'avancement

| It. | Sujet | Statut |
|-----|-------|--------|
| 0 | Nettoyage & squelette projet | ✅ fait |
| 1 | Backend (outils, args, parsing) | ✅ fait |
| 2 | UI PySide6 minimale non bloquante | ✅ fait |
| 3 | Analyse URL & choix format/qualité | ✅ fait |
| 4 | Sous-titres | ✅ fait (à relire/commiter) |
| 5 | Packaging & CI | ⏳ à faire |

### Journal

#### It. 0 — 2026-11-20
Fait :
- `pyproject.toml` (uv + hatchling), layout `src/ytdownloader/`, point d'entrée `python -m ytdownloader` / script `ytdownloader` (placeholder pour l'instant).
- Dépendance `PySide6-Essentials` (et non `PySide6` complet) : évite QtWebEngine & co., ~277 Mo de venv au lieu de bien plus, et un exécutable PyInstaller nettement plus léger.
- Groupe dev : `pytest`, `ruff` (règles E, F, W, I, UP, B, SIM ; longueur 100). `uv.lock` généré — à commiter.
- `tests/test_package.py` (test trivial).
- `yt-dlp.exe` supprimé du dépôt (`git rm`).
- `.gitignore` : ajout de `.ruff_cache/`.

Écarts :
- `urls.txt` et `images/background.png` **conservés** jusqu'à l'It. 2 : l'ancien `ytdownload.py` en a besoin pour démarrer (`background.png`) et pour le mode multi-URL. Ils seront supprimés avec lui.
- `ytdownload.py` exclu de ruff (`extend-exclude`) jusqu'à sa suppression.

À tester :
- `uv sync && uv run ruff check && uv run ruff format --check src tests && uv run pytest`
- `uv run python -m ytdownloader` affiche le message placeholder.
- L'ancien `python ytdownload.py` démarre toujours (avec un `yt-dlp` présent dans le PATH).

#### It. 1 — 2026-11-20
Fait :
- `src/ytdownloader/tools.py` (sans dépendance Qt) : dossier de données par OS (`%LOCALAPPDATA%\ytdownloader`, `$XDG_DATA_HOME/ytdownloader`, surcharge via `$YTDOWNLOADER_DATA_DIR`), recherche des outils (copie gérée dans `<data>/bin` **puis** PATH), détection de version, installation depuis les releases officielles GitHub (yt-dlp, Deno, ffmpeg+ffprobe via `yt-dlp/FFmpeg-Builds`) avec callback de progression et remplacement atomique, mise à jour `yt-dlp -U` (refusée pour une copie non gérée), pas de fenêtre console sous Windows (`CREATE_NO_WINDOW`).
- CLI : `python -m ytdownloader.tools [--check] [--install yt-dlp|deno|ffmpeg|all] [--update]`.
- `src/ytdownloader/ytdlp.py` (pur, testable) :
  - `DownloadOptions` / `SubtitleOptions` / `ToolPaths` → `build_download_args()`, `build_info_args()`.
  - Vidéo : `-f bv*[height<=H]+ba/b[height<=H]/bv*+ba/b` + `-S` selon le conteneur (mp4 → H.264/AAC pour lire partout, webm → VP9/Opus, mkv → meilleure qualité) + `--merge-output-format` / `--remux-video`.
  - Audio : `-f ba/b -x [--audio-format X --audio-quality Q]` (`best` = garde le format d'origine sans réencodage).
  - Sous-titres : `--write-subs [--write-auto-subs] --sub-langs … [--embed-subs] --convert-subs srt` (pas de conversion si intégration en webm, jamais d'intégration en mode audio).
  - Toujours : `--no-playlist`, `--no-mtime`, `--paths`, URL après `--`, `--ffmpeg-location` et `--js-runtimes deno:<chemin>` si connus.
  - Protocole de sortie : lignes `[ytdl-progress]{json}` et `[ytdl-file]<chemin final>` → `parse_output_line()` renvoie `Progress` (fraction, vitesse, ETA, fichier) / `FinalFile` / `None` (log).
  - `parse_info()` → `VideoInfo` (titre, auteur, durée, miniature, formats avec taille exacte ou approx., hauteurs dispo, sous-titres manuels et auto).
- Tests : `tests/test_ytdlp.py`, `tests/test_tools.py` (55 tests).
- Validé en réel (Linux) : installation des 3 outils dans un dossier temporaire, `-U`, puis « Me at the zoo » en 240p mp4 (H.264/AAC) + sous-titres en `.srt`, et en mp3 ; Deno résout bien les challenges JS (`[jsc:deno]`).

Écarts :
- Ordre de recherche **copie gérée puis PATH** (au lieu de PATH puis copie gérée) : un yt-dlp système (apt/pip) est souvent ancien et non mettable à jour sans root ; dès que l'app a installé sa copie, elle l'utilise.
- ffmpeg téléchargeable aussi sous Linux (build statique), en repli seulement : ~350 Mo avec ffprobe, donc l'UI privilégiera `sudo apt install ffmpeg` si aucun ffmpeg système n'est trouvé.
- Dossier de données calculé en Python pur plutôt qu'avec `QStandardPaths` (testable sans Qt, utilisable en CLI).
- Modèle de nom inchangé (`%(title)s.%(ext)s`).

À noter pour l'It. 2 :
- Les sous-titres émettent aussi des événements de progression (fichier `.vtt`), et une vidéo « bv+ba » en émet deux séries (vidéo puis audio) : l'UI doit afficher l'étape en cours plutôt qu'une seule barre qui repart à 0.

À tester :
- `uv run ruff check && uv run ruff format --check src tests && uv run pytest`
- `uv run python -m ytdownloader.tools --check`
- Dans un dossier jetable : `YTDOWNLOADER_DATA_DIR=/tmp/ytd uv run python -m ytdownloader.tools --install all` puis `--update` puis `--check` (les 3 outils en « managed »).

#### It. 2 — 2026-11-21
Fait :
- `src/ytdownloader/jobs.py` : `DownloadJob` (`QProcess`, signaux progression / étape / log / succès / échec / annulation) et `ToolTask` (`QThread` pour détecter, installer et mettre à jour les outils sans geler l'UI).
- Annulation : yt-dlp tourne dans sa propre session et tout l'arbre de processus est tué (`killpg`, `taskkill /T` sous Windows), car le binaire autonome et ffmpeg sont des processus enfants. Seuls les fichiers écrits par ce téléchargement sont supprimés (`.part`, `.ytdl`, fragments, flux intermédiaires, `.temp`).
- `src/ytdownloader/ui/main_window.py` : URL (collage auto d'une URL YouTube), dossier persisté (`QSettings`, défaut Téléchargements), barre + « 42 % · 3,1 Mo/s · ETA 0:12 », étape en cours (flux n, sous-titres, fusion, conversion), Annuler, Ouvrir le dossier, Mettre à jour yt-dlp, journal repliable, bandeau des outils manquants.
- Suppression de `ytdownload.py`, `urls.txt`, `images/background.png`. Logo copié dans `src/ytdownloader/resources/`. README : commande de lancement mise à jour.
- Tests : `tests/test_jobs.py`, `tests/test_main_window.py` (Qt `offscreen`), ajouts dans `tests/test_ytdlp.py`.
- Validé en réel (Linux, sans affichage) : téléchargement de « Me at the zoo », annulation d'une grosse vidéo en cours sans fichier résiduel ni processus restant.

Écarts :
- « Mettre à jour yt-dlp » sur une copie système installe une copie gérée, qui devient prioritaire (une copie système n'est pas modifiable par l'app).
- Sous Linux, ffmpeg manquant : confirmation avant de télécharger le build statique (~350 Mo), `apt` étant conseillé.
- Le mode multi-URL (`urls.txt`) disparaît ; la file d'attente reste une idée optionnelle.

À tester :
- `uv run ruff check && uv run ruff format --check src tests && uv run pytest`
- `uv run python -m ytdownloader` : télécharger une vidéo courte, vérifier que la fenêtre reste réactive, annuler une longue vidéo, rouvrir l'app (dossier conservé).
- Windows : non testé (annulation, encodage des noms de fichiers, absence de fenêtre console).

#### It. 3 — 2026-11-21
Fait :
- Analyse via `InfoJob` (`QProcess`, `--dump-single-json --flat-playlist`) : automatique 400 ms après la saisie d'une URL YouTube, ou via « Analyser » / Entrée. Changer l'URL annule l'analyse en cours et ignore son résultat.
- `ui/preview.py` : miniature, titre, chaîne, durée (textes web affichés en texte brut).
- `ui/options_panel.py` : mode Vidéo (résolutions réellement disponibles + « Meilleure », MP4/MKV/WebM) ou Audio seul (Original, MP3, M4A, Opus, FLAC). Choix conservés dans `QSettings`.
- `estimate_size()` : taille estimée selon les mêmes priorités de codecs que yt-dlp ; « inconnue » pour MP3 et FLAC, qui sont réencodés.
- Le téléchargement exige désormais une analyse réussie et ffmpeg. Une URL déjà collée n'est plus recollée après son téléchargement.
- Tests : 78 au total.
- Validé en réel (Linux, sans affichage) : 8 combinaisons vidéo/audio vérifiées avec ffprobe, 42 estimations à moins de 2 % de la sélection de yt-dlp, source 5.1 convertie en MP3 stéréo 192 kbit/s, vidéo indisponible, chaîne/playlist refusée, changement d'URL pendant l'analyse.

Écarts :
- Pas d'option de qualité audio (retirée après essai) : elle ne concernait que le MP3, désormais toujours encodé en VBR meilleure qualité (`--audio-quality 0`). M4A et Opus sont copiés sans réencodage quand la source le permet, FLAC est sans perte.
- Bouton « Analyser » conservé pour les URL hors YouTube, qui ne déclenchent pas l'analyse automatique.
- Les playlists sont refusées avec un message clair (idée optionnelle à part entière).
- En MP4 au-delà de 1080p, YouTube ne propose généralement pas de H.264 : le fichier contient alors du VP9, moins bien lu par certains lecteurs.

À tester :
- `uv run ruff check && uv run ruff format --check src tests && uv run pytest`
- `uv run python -m ytdownloader` : vidéo 4K en 1440p ou plus, vidéo limitée à 720p, audio MP3 ; vérifier les fichiers (`ffprobe`), la miniature et les choix conservés au redémarrage.
- Windows : non testé.

#### It. 4 — 2026-11-21
Fait :
- `ui/subtitles_panel.py` : section repliable avec filtre, langues cochables (manuelles d'abord, puis auto avec la piste originale en tête), sortie en `.srt` séparés ou intégrée à la vidéo. Le mode de sortie est conservé, la sélection de langues repart à zéro pour chaque vidéo.
- Une langue disponible en manuel n'est pas répétée en auto.
- Un sous-titre refusé (souvent HTTP 429) ne fait plus échouer le téléchargement : la vidéo est conservée et les langues manquantes sont affichées à la fin.
- Intégration sans `.srt` résiduel (`--compat-options no-keep-subs`).
- Annulation : le `.srt` converti est supprimé, sauf s'il existait avant le téléchargement.
- Tests : 85 au total.
- Validé en réel (Linux, sans affichage) : intégration MP4, MKV (2 langues) et WebM vérifiée avec ffprobe, `.srt` séparés, mode audio, échec 429 sur une langue, vidéo indisponible toujours signalée en erreur, annulation sans fichier résiduel.

Écarts :
- Filtre ajouté à la liste : une vidéo peut proposer plus de 160 langues auto.
- `--sleep-subtitles 1` entre chaque sous-titre pour limiter les 429, qui restent possibles.

Limite connue :
- Les `.srt` auto contiennent des répétitions (texte défilant de YouTube, blocs de 10 ms). Les sous-titres manuels ne sont pas concernés.

À tester :
- `uv run ruff check && uv run ruff format --check src tests && uv run pytest`
- `uv run python -m ytdownloader` : vidéo avec sous-titres manuels et auto, en `.srt` puis intégrés (MP4, MKV), filtre, mode audio, annulation.
- Windows : non testé.

## Itérations (chacune testable et commitée par toi)

### It. 0 — Nettoyage & squelette projet
- Création de `docs/PLAN.md` (ce plan + section suivi).
- `git rm yt-dlp.exe` (`urls.txt`, `images/background.png` : reportés à l'It. 2).
- `pyproject.toml` (uv), layout `src/`, ruff configuré, `pytest` vide qui passe.
- Ancien `ytdownload.py` conservé temporairement ou supprimé dès It. 2.
- **Test** : `uv sync && uv run pytest && uv run ruff check`.

### It. 1 — Backend (sans UI)
- `tools.py` : résolution des outils dans l'ordre PATH → dossier app ; téléchargement du binaire yt-dlp depuis les releases GitHub officielles ; `yt-dlp -U` ; détection ffmpeg (Ubuntu : message « `sudo apt install ffmpeg` » ; Windows : téléchargement d'un build ffmpeg) ; détection Deno (téléchargement du binaire release sur Windows/Linux si absent).
- `ytdlp.py` : `build_args(options) -> list[str]` (fonction pure), `parse_progress(line)`, `parse_info(json)` → modèle `VideoInfo` (titre, durée, miniature, liste de hauteurs dispo, langues de sous-titres manuels/auto).
- **Test** : pytest sur les fonctions pures + petit script `uv run python -m ytdownloader.tools --check` affichant versions trouvées.

### It. 2 — Nouvelle UI PySide6 minimale, non bloquante
- Fenêtre : dossier de sortie (persisté avec `QSettings`, défaut = dossier Téléchargements), champ URL (collage auto depuis le presse-papiers si URL YouTube), bouton Télécharger, `QProgressBar` + label « 42 % · 3,1 Mo/s · ETA 0:12 », bouton Annuler, zone de log repliable, bouton « Mettre à jour yt-dlp », bouton « Ouvrir le dossier » à la fin.
- `jobs.py` via `QProcess` ; nettoyage des fichiers `.part` à l'annulation.
- Supprime `ytdownload.py`.
- **Test** : `uv run python -m ytdownloader`, télécharger une vidéo courte, vérifier que l'UI reste réactive, annuler en cours.

### It. 3 — Analyse de l'URL & choix format/qualité
- Bouton « Analyser » (ou auto après collage) → `yt-dlp -J` en `QProcess` → aperçu : miniature, titre, chaîne, durée.
- Mode **Vidéo** : résolution (liste construite depuis les formats réellement dispo : 2160p…144p + « Meilleure »), conteneur mp4/mkv/webm. Sélecteur généré : `-f "bv*[height<=H]+ba/b[height<=H]" --merge-output-format <ext>` (mp4 : préférer avc1/m4a pour compatibilité lecteurs Windows).
- Mode **Audio seul** : mp3 / m4a / opus / flac + qualité (`-x --audio-format X --audio-quality Q`).
- Taille estimée affichée quand `filesize`/`filesize_approx` est dispo.
- **Test** : vidéo 4K, vidéo 720p max, audio mp3 → vérifier fichiers produits (ffprobe).

### It. 4 — Sous-titres
- Liste cochable des langues issue de `-J` (manuels en premier, auto-générés marqués « auto »).
- Options : fichier séparé (srt) ou intégré à la vidéo (`--embed-subs`, mkv/mp4).
- Args : `--write-subs [--write-auto-subs] --sub-langs fr,en --convert-subs srt [--embed-subs]`.
- **Test** : vidéo avec sous-titres FR manuels + auto EN.

### It. 5 — Packaging & CI
- `pyinstaller` spec (one-folder sur Linux, onefile ou one-folder sur Windows), icône `.ico`/`.png`, chemins ressources via `importlib.resources`.
- GitHub Actions : matrice `ubuntu-latest` / `windows-latest`, build sur tag `v*`, upload dans la Release. Lint + tests sur chaque PR.
- README réécrit (captures, install, dépendances ffmpeg/Deno).
- **Test** : télécharger l'artefact CI, lancer sur Ubuntu et Windows (VM) sans Python installé.

## Idées supplémentaires (itérations optionnelles, à piocher plus tard)
- **File d'attente multi-URL** (remplace proprement `urls.txt`) avec tableau de jobs et téléchargements parallèles limités.
- **Playlists** : détection, sélection des vidéos, `--download-archive` pour ne pas re-télécharger.
- **Métadonnées** : `--embed-metadata --embed-thumbnail --embed-chapters` (pochette sur les mp3 notamment).
- **SponsorBlock** : supprimer/marquer sponsors, intros (`--sponsorblock-remove`).
- **Extrait** : télécharger seulement un passage (`--download-sections "*1:30-2:45"`).
- **Cookies navigateur** (`--cookies-from-browser firefox`) pour vidéos à restriction d'âge / membres.
- Modèle de nom de fichier personnalisable, thème clair/sombre, UI FR/EN, notification système en fin de téléchargement, drag-and-drop d'URL.
- Vérification auto des MAJ yt-dlp au démarrage (une fois par jour).

## Vérification globale
À chaque itération : `uv run ruff check && uv run pytest`, puis test manuel décrit dans l'itération ; tu relis le diff et commits toi-même (je ne commite pas).
