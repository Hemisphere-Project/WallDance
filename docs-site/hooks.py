"""MkDocs hook for the documents' website (mkdocs.yml, hub walldance#t-069).

The menu names the pages that will exist (« Situations », « Exercices ») before they are written.
A menu entry whose file is not there yet would fail the strict build, so it is dropped here, and
a folder entry (``exercices/``) becomes a section listing the Markdown pages inside it.

The folded answer of the booklet and the exercises is plain HTML, ``<details><summary>Que faire ?
</summary> … </details>``, with Markdown inside (lists, pictures). Python-Markdown leaves the inside
of an HTML block as it is; the hook marks each ``<details>`` for ``md_in_html`` so its inside renders
as it does in the PDFs. It opens and closes without JavaScript: it stays a native ``<details>``.
Nothing else is touched: the documents render as they are written.

The allow-list in ``exclude_docs`` re-admits ``assets/`` for the theme's own files; a document put
under ``docs/assets/`` would ride on that line, so it fails the build here instead.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("mkdocs.hooks.walldance")


def _present(entry, docs_dir: str):
    """Return the nav entry with missing pages removed, or None when nothing is left."""
    if isinstance(entry, str):
        return _leaf(None, entry, docs_dir)
    if isinstance(entry, dict):
        (title, value), = entry.items()
        if isinstance(value, list):
            kept = [e for e in (_present(c, docs_dir) for c in value) if e is not None]
            return {title: kept} if kept else None
        return _leaf(title, value, docs_dir)
    return entry


def _leaf(title, path: str, docs_dir: str):
    if "://" in path:
        return {title: path} if title else path
    full = os.path.join(docs_dir, path)
    if path.endswith("/"):
        pages = sorted(
            os.path.join(path, name)
            for name in os.listdir(full) if name.endswith(".md")
        ) if os.path.isdir(full) else []
        if not pages:
            log.info("menu: %s (%s) is not written yet, left out", title or path, path)
            return None
        index = [p for p in pages if os.path.basename(p) == "index.md"]
        pages = index + [p for p in pages if p not in index]
        return {title: pages} if title else pages
    if not os.path.isfile(full):
        log.info("menu: %s (%s) is not written yet, left out", title or path, path)
        return None
    return {title: path} if title else path


_DETAILS = re.compile(r"^(\s*)<details(?![^>]*\bmarkdown\b)(\s[^>]*)?>", re.MULTILINE)
_FENCE = re.compile(r"^(\s*)(```|~~~)", re.MULTILINE)


def on_page_markdown(markdown, **kwargs):
    """Mark every <details> outside a code fence for md_in_html (see the module's docstring)."""
    if "<details" not in markdown:
        return markdown
    out, fenced = [], False
    for line in markdown.splitlines(keepends=True):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            line = _DETAILS.sub(lambda m: f'{m.group(1)}<details markdown="block"{m.group(2) or ""}>', line)
        out.append(line)
    return "".join(out)


def on_config(config, **kwargs):
    if config.nav:
        docs_dir = config.docs_dir
        config.nav = [e for e in (_present(e, docs_dir) for e in config.nav) if e is not None]
    return config


def on_files(files, config, **kwargs):
    docs_assets = os.path.join(os.path.abspath(config.docs_dir), "assets") + os.sep
    for f in files:
        if os.path.abspath(f.abs_src_path or "").startswith(docs_assets):
            log.warning(
                "docs/%s would be published through the theme's assets/ line of exclude_docs: "
                "move it, or name it in exclude_docs on purpose", f.src_uri,
            )
    return files
