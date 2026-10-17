"""Tests d'os-appalias (unittest stdlib).

Lancement depuis la racine du projet :
    python -I -m unittest discover -s tests

Le dossier scripts du plugin est ajouté au sys.path par appalias_testutil.
"""

import os
import sys

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import appalias_testutil  # noqa: E402,F401
