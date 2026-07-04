"""Recommender v0 — pure content-similarity engine (docs/phases/PHASE-3-recommender.md §1-2).

Classical scikit-learn only (TF-IDF, FeatureHasher, cosine similarity). No
generative AI. This package implements just the feature-engineering (§2) and
a direct cosine-similarity "similar films" query — NOT the candidate
generation (§3), per-user Ridge taste models (§4), full recommendation
scoring/MMR (§5), retrain artefact versioning (§6), or evaluation (§8). Those
are later phases.
"""
from __future__ import annotations
