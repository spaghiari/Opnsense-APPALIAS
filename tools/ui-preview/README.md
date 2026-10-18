# Banc de prévisualisation de l'interface

Affiche la page `index.volt` du plugin dans un navigateur, sans OPNsense,
avec des données fictives (domaines de services publics, adresses en
192.0.2.0/24), pour contrôler l'alignement et les états.

## Commande

Depuis la racine du dépôt :

```
python tools/ui-preview/build.py
python -m http.server 8765 -d tools/ui-preview/out
```

Puis ouvrir <http://localhost:8765/index.html>.

`build.py` relit `index.volt` à chaque lancement : après une modification de
la page, relancer seulement `build.py` et recharger le navigateur (le serveur
reste en route). Le navigateur doit pouvoir joindre `cdnjs.cloudflare.com`
(jQuery 3.7.1, Bootstrap 3.4.1, Font Awesome 4.7.0).

`build.py` affiche la liste des endpoints `/api/appalias/...` trouvés dans
`index.volt` et signale ceux qui n'ont pas de fixture.

## États

Paramètre d'URL `?state=` (liens aussi dans la barre du haut) :

| URL | Effet |
|---|---|
| `index.html` | relais en marche, 3 apps, Discovery active |
| `index.html?state=stopped` | relais arrêté, avertissement DoT en alerte rouge (`alert-danger`) |
| `index.html?state=discovery-off` | carte « Discovery is off » |
| `index.html?state=empty` | aucune application, journal et propositions vides |
| `index.html?state=error` | catalogue en erreur (info, populaires, recherche, aperçu, journal, écarts, mise à jour) |

Un état est un dossier `fixtures/<état>/` : ses fichiers remplacent ceux de
`fixtures/`, les autres restent ceux de l'état par défaut. Ajouter un dossier
suffit à créer un état (relancer `build.py`).

## Largeurs étroites

`index.html` ne se laisse pas réduire sous la fenêtre du navigateur. Pour juger
la mise en page mobile, ouvrir `mobile.html?w=420&tab=discovery&state=empty` :
la page est chargée dans une iframe de 420 px (media queries comprises) ;
`log=1` liste les éléments qui dépassent. Edge en `--headless` ne descend pas
sous environ 490 px de fenêtre : une capture `--window-size=420` est mise en page
à 492 px puis rognée, d'où de faux débordements. Capturer `mobile.html` avec une
fenêtre plus large (600 px) à la place.

## Contenu

- `build.py` : traduit `index.volt` (`lang._()`, `partial()`, commentaires),
  encadre la page d'une coquille OPNsense (barre du haut, `page-content-main`,
  le `content-box` et les onglets `nav-tabs` viennent de la page) et écrit
  `out/` (`index.html`, `fixtures.js`, copies de `mock.js` et de
  `opnsense-lite.css`).
- `mock.js` : `ajaxGet`, `ajaxCall`, `mapDataToFormUI`, `saveFormToEndpoint`,
  `updateServiceControlUI`, `formatTokenizersUI`, `BootstrapDialog`,
  `stdDialogRemove`, `stdDialogConfirm`, `SimpleActionButton`, `selectpicker`,
  et la méthode jQuery `bootgrid` (table HTML minimale, événements
  `initialized` et `loaded.rs.jquery.bootgrid`, commandes `reload`, `append`,
  `clear`) avec `UIBootgrid` par-dessus (édition, copie, suppression, bascule).
- `opnsense-lite.css` : imite le thème (police, couleurs, panneaux, onglets,
  `.btn-xs`, `.label`, `.table-responsive` limité à moins de 768 px, grille
  bootgrid tronquée), valeurs relevées dans `main.css` de stable/26.7.
- `fixtures/` : réponses JSON.

## Fixtures

Nom de fichier = les deux premiers segments de l'URL après `/api/appalias/`,
joints par `_` : `/api/appalias/service/status` -> `service_status.json`.
Sans fixture, le banc affiche un avertissement en console et répond `{}`.
Deux fixtures sont indexées par un paramètre, avec `*` comme valeur par défaut :
`settings_getApp.json` (uuid, `new` pour une création) et
`catalog_preview.json` (paramètre `ref`). `catalog_diff` filtre sur `uuid`
si la requête en fournit un.

Endpoints simulés (33) :

- service : `status`, `unboundStatus`, `unboundRestart`, `reconfigure`
- settings : `get`, `set`, `searchApp`, `getApp`, `setApp`, `addApp`, `delApp`, `toggleApp`
- catalog : `info`, `popular`, `search`, `preview`, `import`, `diff`, `applyDiff`,
  `update`, `reset`, `followLog`, `undoFollow`
- discovery : `status`, `proposals`, `unassigned`, `accept`, `ignore`, `unignore`,
  `learn`, `stopLearn`, `purge`, `refresh`

## Limites et hypothèses

- Le banc est sans état : les écritures (`setApp`, `import`, `accept`,
  `undoFollow`...) répondent par une fixture fixe et ne modifient pas les
  données affichées. Les listes se rechargent à l'identique.
- Les champs de `searchApp` portent les clés des options (`both`, `inet`), pas
  leur libellé : hypothèse, non vérifiée sur un pare-feu.
- `getApp` renvoie les listes (`domains`, `networks`, `catalog_refs`) sous la
  forme `{valeur: {value, selected}}`, les listes déroulantes sous la même
  forme (clé de l'option).
- Les formes de `catalog_follow` (`followLog`, `update.follow`, `undoFollow`)
  suivent le code des contrôleurs et de `lib/follow.py` ; `total` et `max`
  de `followLog` viennent du cahier des charges de la 0.6.
- La police SourceSansPro du thème n'est pas chargée (pas sur cdnjs) : repli
  Helvetica/Arial, largeurs de texte légèrement différentes.
- Bootstrap-select et le widget tokenize d'OPNsense ne sont pas chargés : les
  listes déroulantes sont des `<select>` natifs, les listes de jetons un rendu
  statique simplifié ; leur largeur (346 px) reprend celle du pare-feu.
- Le compte à rebours de Learn (74 s au chargement) s'écoule dans le navigateur ;
  à zéro, la page recharge les données (fixture inchangée).
- Les dates de la fixture (journal, Discovery) sont fixes, en octobre 2026.
