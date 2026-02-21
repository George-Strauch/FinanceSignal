#!/usr/bin/env python3
"""Extract ORG entities from posts/comments and cluster by vector similarity."""

import sys
import os
from collections import Counter
from pathlib import Path

# ── Import guards ───────────────────────────────────────────────────────

try:
    import spacy
except ImportError:
    print("spacy is required: pip install spacy")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("numpy is required: pip install numpy")
    sys.exit(1)

try:
    from scipy.cluster.hierarchy import linkage, fcluster
    from scipy.spatial.distance import pdist
except ImportError:
    print("scipy is required: pip install scipy")
    sys.exit(1)

# ── Project imports ─────────────────────────────────────────────────────

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.config import DB_PATH
from sentinel.db import RedditDatabase

BATCH_SIZE = 500


def load_model():
    try:
        return spacy.load("en_core_web_md", disable=["parser", "lemmatizer"])
    except OSError:
        print("spaCy model not found. Install it with:")
        print("  python -m spacy download en_core_web_md")
        sys.exit(1)


def extract_entities(conn, nlp):
    """Scan all posts and comments, return Counter of ORG entity text -> count."""
    entity_counts = Counter()

    # --- Posts ---
    total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    offset = 0
    scanned = 0
    while offset < total_posts:
        rows = conn.execute(
            "SELECT title, selftext FROM posts LIMIT ? OFFSET ?",
            (BATCH_SIZE, offset),
        ).fetchall()
        if not rows:
            break
        texts = [
            (r["title"] or "") + " " + (r["selftext"] or "") for r in rows
        ]
        for doc in nlp.pipe(texts, batch_size=BATCH_SIZE):
            for ent in doc.ents:
                if ent.label_ == "ORG":
                    entity_counts[ent.text] += 1
        scanned += len(rows)
        offset += BATCH_SIZE
        print(f"  Posts: {scanned}/{total_posts}", end="\r")
    if total_posts:
        print(f"  Posts: {scanned}/{total_posts}")

    # --- Comments ---
    total_comments = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    offset = 0
    scanned = 0
    while offset < total_comments:
        rows = conn.execute(
            "SELECT body FROM comments LIMIT ? OFFSET ?",
            (BATCH_SIZE, offset),
        ).fetchall()
        if not rows:
            break
        texts = [r["body"] or "" for r in rows]
        for doc in nlp.pipe(texts, batch_size=BATCH_SIZE):
            for ent in doc.ents:
                if ent.label_ == "ORG":
                    entity_counts[ent.text] += 1
        scanned += len(rows)
        offset += BATCH_SIZE
        print(f"  Comments: {scanned}/{total_comments}", end="\r")
    if total_comments:
        print(f"  Comments: {scanned}/{total_comments}")

    return entity_counts


def cluster_entities(nlp, entity_counts, threshold=0.35):
    """Cluster entity names by cosine distance on spaCy vectors.

    Returns list of clusters: [(total_mentions, [(name, count), ...]), ...]
    sorted by total_mentions descending.
    """
    # Filter to entities with >= 2 mentions
    frequent = {name: count for name, count in entity_counts.items() if count >= 2}
    if not frequent:
        return []

    names = list(frequent.keys())
    vectors = []
    oov_names = []
    iv_names = []

    for name in names:
        vec = nlp(name).vector
        if np.any(vec):
            vectors.append(vec)
            iv_names.append(name)
        else:
            oov_names.append(name)

    clusters = []

    # Cluster in-vocabulary entities
    if len(iv_names) > 1:
        mat = np.array(vectors)
        dists = pdist(mat, metric="cosine")
        Z = linkage(dists, method="average")
        labels = fcluster(Z, t=threshold, criterion="distance")

        groups = {}
        for name, label in zip(iv_names, labels):
            groups.setdefault(label, []).append(name)

        for members in groups.values():
            total = sum(frequent[m] for m in members)
            members_with_counts = sorted(
                [(m, frequent[m]) for m in members], key=lambda x: -x[1]
            )
            clusters.append((total, members_with_counts))
    elif len(iv_names) == 1:
        name = iv_names[0]
        clusters.append((frequent[name], [(name, frequent[name])]))

    # OOV entities become singletons
    for name in oov_names:
        clusters.append((frequent[name], [(name, frequent[name])]))

    clusters.sort(key=lambda x: -x[0])
    return clusters


def print_results(entity_counts, clusters):
    total_unique = len(entity_counts)
    total_mentions = sum(entity_counts.values())
    print(f"\n{'='*60}")
    print(f"ENTITY SUMMARY")
    print(f"{'='*60}")
    print(f"  Unique ORG entities: {total_unique}")
    print(f"  Total mentions:      {total_mentions}")
    print(f"  Clusters (≥2 mentions): {len(clusters)}")

    # Top 20 raw entities
    print(f"\n{'─'*60}")
    print(f"TOP 20 RAW ENTITIES (before clustering)")
    print(f"{'─'*60}")
    for name, count in entity_counts.most_common(20):
        print(f"  {count:>5}  {name}")

    # Clusters
    print(f"\n{'─'*60}")
    print(f"CLUSTERS (grouped by vector similarity)")
    print(f"{'─'*60}")
    for i, (total, members) in enumerate(clusters, 1):
        if len(members) > 1:
            print(f"\n  Cluster {i} ({total} total mentions):")
            for name, count in members:
                print(f"    {count:>5}  {name}")
        else:
            name, count = members[0]
            print(f"  [{count:>5}]  {name}")


def main():
    if not Path(DB_PATH).exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    print("Loading spaCy model...")
    nlp = load_model()

    with RedditDatabase() as db:
        conn = db.conn

        total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        total_comments = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        if total_posts == 0 and total_comments == 0:
            print("Database is empty — nothing to scan.")
            sys.exit(0)

        print(f"Scanning {total_posts} posts and {total_comments} comments...")
        entity_counts = extract_entities(conn, nlp)

    if not entity_counts:
        print("No ORG entities found.")
        sys.exit(0)

    print("Clustering entities...")
    clusters = cluster_entities(nlp, entity_counts)

    print_results(entity_counts, clusters)


if __name__ == "__main__":
    main()
