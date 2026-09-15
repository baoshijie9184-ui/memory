#!/usr/bin/env python
"""Dump Qdrant memory stores to a readable JSON mirror.

Reads the qdrant local-mode sqlite files directly (read-only), producing:
  <mirror-dir>/<collection>.json   — all entries sorted by time_stamp
  <mirror-dir>/mirror_index.json   — global index: counts, paths, dump time
Usage:
  python dump_memory_mirror.py --qdrant-dir <qdrant base> --mirror-dir <out>
"""
import argparse, glob, json, os, sqlite3, sys
from datetime import datetime


def find_collections(base):
    found = {}
    for db in glob.glob(os.path.join(base, '**', 'storage.sqlite'), recursive=True):
        parts = db.split(os.sep)
        # canonical layout: <base>/<col>/collection/<col>/storage.sqlite
        if len(parts) >= 3 and parts[-3] == 'collection' and parts[-4] == parts[-2]:
            found[parts[-2]] = db
    return found


def dump_collection(db):
    """points table: (id TEXT, point BLOB) where point is a pickled PointStruct."""
    import pickle
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        rows = con.execute('SELECT id, point FROM points').fetchall()
    finally:
        con.close()
    entries = []
    for pid, blob in rows:
        try:
            struct = pickle.loads(blob)
            payload = getattr(struct, 'payload', None) or {}
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        payload['point_id'] = str(pid)
        entries.append(payload)
    entries.sort(key=lambda e: str(e.get('time_stamp') or e.get('float_time_stamp') or ''))
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--qdrant-dir', required=True)
    ap.add_argument('--mirror-dir', required=True)
    args = ap.parse_args()

    os.makedirs(args.mirror_dir, exist_ok=True)
    cols = find_collections(args.qdrant_dir)
    if not cols:
        print(f'No collections found under {args.qdrant_dir}', file=sys.stderr)
        sys.exit(1)

    index = {'dumped_at': datetime.now().isoformat(),
             'qdrant_dir': os.path.abspath(args.qdrant_dir),
             'collections': {}}
    for col, db in sorted(cols.items()):
        entries = dump_collection(db)
        out = os.path.join(args.mirror_dir, f'{col}.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False, indent=1)
        index['collections'][col] = {'count': len(entries), 'db': db, 'mirror_file': out}
        print(f'{col:30s} {len(entries):6d} entries -> {out}')

    with open(os.path.join(args.mirror_dir, 'mirror_index.json'), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    total = sum(v['count'] for v in index['collections'].values())
    print(f'Total: {total} entries in {len(cols)} collections')


if __name__ == '__main__':
    main()
