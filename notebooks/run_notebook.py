"""Exécute un notebook avec le cwd du kernel forcé à la racine du dépôt.

`jupyter nbconvert --execute` n'hérite pas de manière fiable du cwd du shell appelant pour le
kernel exécuté — sans ce script, l'import de `ml.*` (non installé comme package, cf.
pyproject.toml) échoue selon d'où la commande est lancée.

Usage : .venv/Scripts/python notebooks/run_notebook.py notebooks/<fichier>.ipynb
"""

import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    path = sys.argv[1]
    nb = nbformat.read(path, as_version=4)
    client = NotebookClient(
        nb, kernel_name="gravia", resources={"metadata": {"path": str(REPO_ROOT)}}, timeout=600
    )
    client.execute()
    nbformat.write(nb, path)
    print("OK:", path)


if __name__ == "__main__":
    main()
