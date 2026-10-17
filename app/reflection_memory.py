"""
Episodic reflexion memory — lets the structuring agent draw on
validated corrections from EARLIER rows and batches, not just the
current row's own retry. This is the piece of the original Reflexion
design (Shinn et al., 2023) beyond the inner retry loop: a reflection
is only worth writing down if a LATER actor call can retrieve it —
otherwise every row relearns the same lesson from zero.

Backend selection mirrors this project's existing convention
(STOREMIND_LLM_BACKEND / STOREMIND_STORE_BACKEND / STOREMIND_IMAGE_BACKEND):

    STOREMIND_REFLECTION_MEMORY = keyword (default) | firestore_vector | off

keyword          Jaccard similarity over classification tags + category,
                 persisted through the SAME store module (local_store or
                 firestore_client) everything else in this project uses
                 — no new database, no embeddings, no network call. Zero
                 marginal cost, fully offline, fully unit-testable. This
                 is what runs under the mock LLM backend, and is a
                 legitimate retrieval strategy in its own right for a
                 store this size — it just doesn't capture semantic
                 similarity the way embeddings do (a "missing pack
                 count" reflection won't match a "no unit quantity"
                 tag unless they share a literal tag string).

    firestore_vector   Real semantic retrieval: Firestore native vector
                 search (find_nearest, COSINE) over gemini-embedding-001
                 embeddings at 768 dimensions. 768 rather than the
                 model's native 3072 for two reasons — it comfortably
                 clears Firestore's documented 2048-dim vector ceiling
                 with margin, and gemini-embedding-001 needs MANUAL
                 normalization for any dimension other than 3072 (unlike
                 gemini-embedding-2, which normalizes automatically) —
                 cosine distance is invariant to vector magnitude, so
                 skipping normalization is correct here, not an
                 oversight.

                 Requires a one-time composite vector index on the
                 `reflections` collection's `embedding` field — see
                 FIRESTORE_INDEX_COMMAND below. NOT created by this
                 module, and retrieval fails OPEN (returns no memories,
                 never raises) if the index doesn't exist yet, so a
                 fresh deploy degrades to "no cross-batch memory" rather
                 than breaking ingest.

    off              No-op both ways — the inner per-row retry loop
                 (reflexion.py) works correctly with zero memories, so
                 this whole module is a pure enhancement, never a
                 dependency of correctness.

UNVERIFIED IN THIS SANDBOX: firestore_vector's find_nearest() call and
the embedding call it depends on need live GCP credentials and a real
Firestore database — no network access here, the same documented
limitation already flagged for catalog_agent.py's own
_fetch_image_bytes and _compress_field. Smoke-test both before the demo.

Poisoning guard, in both backends: save() is the CALLER's decision, not
this module's — reflexion.py's caller (main.py) only calls save() for
an attempt whose grounding check found nothing to reject AND whose
score measurably improved over the attempt before it. This module
itself never re-derives that judgment; it just persists whatever it's
handed.
"""

from __future__ import annotations

import dataclasses
import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768  # see module docstring for why 768, not 3072
DEFAULT_TOP_K = 3
DEFAULT_DISTANCE_THRESHOLD = 0.35  # COSINE — lower is more similar; untuned, see docstring

# Run once, manually, against the real Firestore database before
# firestore_vector is usable — this module does not run it for you.
FIRESTORE_INDEX_COMMAND = (
    "gcloud firestore indexes composite create "
    "--collection-group=reflections --query-scope=COLLECTION "
    '--field-config=vector-config=\'{"dimension":"768","flat":"{}"}\',field-path=embedding'
)


@dataclass
class ReflectionEntry:
    category: str
    tags: list[str]
    note: str
    score_before: float
    score_after: float
    sku: str
    created_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class ReflectionMemory(Protocol):
    def retrieve(self, category: str, tags: list[str], top_k: int = DEFAULT_TOP_K) -> list[str]: ...
    def save(self, entry: ReflectionEntry) -> None: ...


class NullReflectionMemory:
    """STOREMIND_REFLECTION_MEMORY=off. Both operations are no-ops —
    the safest possible default if cross-batch memory isn't wanted at
    all, distinct from a memory backend that's merely empty."""

    def retrieve(self, category: str, tags: list[str], top_k: int = DEFAULT_TOP_K) -> list[str]:
        return []

    def save(self, entry: ReflectionEntry) -> None:
        return None


class KeywordReflectionMemory:
    """STOREMIND_REFLECTION_MEMORY=keyword (default). Persists through
    the SAME store module (local_store.py or firestore_client.py)
    everything else in this project uses — see write_reflection /
    list_reflections added to both. Retrieval ranks by tag overlap
    (Jaccard-style intersection count), category-scoped so a Fashion
    reflection never surfaces for a Kitchenware row — same category-
    scoping instinct as brand_resolution.infer_brand_by_category.
    """

    def __init__(self, store: Any, max_entries_scanned: int = 500):
        self._store = store
        self._max_entries_scanned = max_entries_scanned

    def retrieve(self, category: str, tags: list[str], top_k: int = DEFAULT_TOP_K) -> list[str]:
        try:
            entries = self._store.list_reflections(category=category, limit=self._max_entries_scanned)
        except Exception:  # noqa: BLE001 — memory is best-effort, see module docstring
            return []
        if not entries:
            return []
        if tags:
            tagset = set(tags)
            scored = [(len(tagset & set(e.get("tags") or [])), e) for e in entries]
            scored = [pair for pair in scored if pair[0] > 0]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return [e.get("note", "") for _, e in scored[:top_k] if e.get("note")]
        # No tags — this is the PRE-EMPTIVE call, before a first attempt
        # has even run, so there is no gap classification yet to rank
        # against. Recency is the best available signal until this
        # row's OWN issues (if any) are known: most-recently-validated
        # corrections for the category, newest first.
        entries_sorted = sorted(entries, key=lambda e: e.get("created_at") or "", reverse=True)
        return [e.get("note", "") for e in entries_sorted[:top_k] if e.get("note")]

    def save(self, entry: ReflectionEntry) -> None:
        try:
            self._store.write_reflection(dataclasses.asdict(entry))
        except Exception:  # noqa: BLE001
            pass


class FirestoreVectorReflectionMemory:
    """STOREMIND_REFLECTION_MEMORY=firestore_vector. Real semantic
    retrieval — see module docstring for the index requirement and the
    768-dimension reasoning. `embed_fn` is injected rather than
    imported so this module has no direct dependency on which LLM
    backend produced it — catalog_agent.py / catalog_agent_aistudio.py
    each expose their own embed_text(text) -> list[float] | None using
    their own already-authenticated client; the mock backend has none,
    which is exactly why the default backend is `keyword`, not this
    one — a mock LLM has nothing to embed with.
    """

    def __init__(
        self,
        embed_fn: Callable[[str], "list[float] | None"],
        distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    ):
        self._embed_fn = embed_fn
        self._distance_threshold = distance_threshold

    def retrieve(self, category: str, tags: list[str], top_k: int = DEFAULT_TOP_K) -> list[str]:
        # No early tags guard: an empty tags list is the PRE-EMPTIVE
        # call (see KeywordReflectionMemory's own note) — _safe_embed
        # embeds the category string alone in that case, which is
        # still a meaningful semantic query, just less specific than
        # once this row's own gap tags are known.
        vector = self._safe_embed(category, tags)
        if vector is None:
            return []
        try:
            from google.cloud import firestore
            from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
            from google.cloud.firestore_v1.vector import Vector

            db = firestore.Client()
            docs = (
                db.collection("reflections")
                .where("category", "==", category)
                .find_nearest(
                    vector_field="embedding",
                    query_vector=Vector(vector),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=top_k,
                    distance_threshold=self._distance_threshold,
                )
                .get()
            )
            return [d.to_dict().get("note", "") for d in docs if d.to_dict().get("note")]
        except Exception:  # noqa: BLE001 — missing index, auth issue, etc. — fail open, see docstring
            return []

    def save(self, entry: ReflectionEntry) -> None:
        vector = self._safe_embed(entry.category, entry.tags)
        if vector is None:
            return
        try:
            from google.cloud import firestore
            from google.cloud.firestore_v1.vector import Vector

            db = firestore.Client()
            doc = dataclasses.asdict(entry)
            doc["embedding"] = Vector(vector)
            db.collection("reflections").add(doc)
        except Exception:  # noqa: BLE001
            pass

    def _safe_embed(self, category: str, tags: list[str]) -> "list[float] | None":
        try:
            return self._embed_fn(f"{category} " + " ".join(tags))
        except Exception:  # noqa: BLE001 — an embedding failure degrades to no memory, not a crash
            return None


def build_reflection_memory(backend: str, store: Any, embed_fn: "Callable[[str], list[float] | None] | None" = None) -> ReflectionMemory:
    """Mirrors main.py's _build_agent / _build_store factory pattern.
    Falls back to NullReflectionMemory for an unrecognized value rather
    than raising — a typo'd env var should degrade to "no memory",
    never crash startup."""
    if backend == "firestore_vector":
        if embed_fn is None:
            return NullReflectionMemory()
        return FirestoreVectorReflectionMemory(embed_fn)
    if backend == "keyword":
        return KeywordReflectionMemory(store)
    return NullReflectionMemory()

