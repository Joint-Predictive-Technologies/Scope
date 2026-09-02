"""Vendored OSINT-Graph serving code — see VENDORED.json.

Byte-identical copies from the `osint-graph` repo. The nested `serving/` and
`loader/` directories mirror that repo's layout on purpose: `export_map.py`
derives its sibling `loader/` path from `__file__`, so keeping the shape lets
the files be vendored with zero edits and diffed against upstream directly.
"""
