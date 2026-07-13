"""Entity canonicalization — tool definitions, exec handlers, system prompt.

Flow:
  1. Exact match lookup (canonical text or alias, case-insensitive) → auto-assign, done.
  2. BM25 search → if any result is an exact match → auto-assign, done.
  3. BM25 results exist (≤10) → include in LLM prompt as candidates.
  4. No BM25 results → LLM can provide alternative search terms or decide junk.
  5. All assigned entities must create an exact match alias for the pending text.

The LLM can create aliases but may NOT delete them.
"""

import json
import logging
import time

from sentinel.db import RedditDatabase

logger = logging.getLogger(__name__)

MAX_CANON_ROUNDS = 6
MODEL = "deepseek/deepseek-v4-flash"

SYSTEM_PROMPT = """\
You are an entity canonicalization assistant for a financial sentiment platform. \
Your job is to resolve named entities extracted from Reddit posts by spaCy NER \
into a canonical registry.

You are given an entity that was extracted by NER and has NOT yet been linked \
to a canonical entity. You must decide:
1. Link it to an existing canonical entity (if it's a variant of one), OR
2. Create a new canonical entity (if it's genuinely new), OR
3. Mark it as MISC (if it's junk that spaCy mis-identifies — common wrongfully \
parsed entities that aren't real people, companies, or places).

You may be given pre-searched candidate matches. Review them first — if one \
is clearly the same entity, link to it rather than creating a duplicate.

You have tools to search the canonical registry and to make your decision.

Rules:
- Search is label-agnostic — spaCy mislabels entities frequently. "Trump" might \
be tagged ORG but is actually PERSON. Don't filter by label when searching.
- When creating a new canonical, provide a DENSE description (2-4 sentences) \
covering: what the entity is, its domain/industry, key identifiers (ticker, \
founding, location for companies; role, affiliations for people), and \
disambiguating context. This description will be used for vector similarity \
matching later, so it must be information-rich. No filler.
- When creating a company entity, set ticker_link to its primary stock ticker \
symbol if known (e.g. "TSLA" for Tesla). The ticker symbol will automatically \
be added as an alias so future spaCy extractions of the ticker symbol auto-link.
- You can assign ticker tags (ambiguous, crypto, etf) if the ticker is \
problematic. "AI" is ambiguous (common word), "BTC" is crypto, "SPY" is an ETF.
- Non-canonical entities (aliases) do NOT have their own description — they \
only point to the canonical.
- For junk that spaCy repeat-extracts (numbers, URLs, common words misidentified \
as entities), use mark_as_misc. This creates a catch so future extractions of \
the same string auto-link without needing LLM calls.
- If you cannot find a match with the initial search, use refine_search with \
alternative spellings or expanded names (e.g. "US" -> try "United States", \
"U.S.", "United States of America"). The search uses BM25 ranking so \
partial matches will surface.
- You may NOT delete aliases. You can only create new ones.

Available ticker tags: {available_tags}
"""


def _get_available_tags(db: RedditDatabase) -> list[dict]:
    return [{"id": ts["id"], "name": ts["name"], "description": ts.get("description", "")}
            for ts in db.list_ticker_tag_sets()]


def build_system_prompt(db: RedditDatabase) -> str:
    tags = _get_available_tags(db)
    tags_str = json.dumps(tags, indent=2) if tags else "none"
    return SYSTEM_PROMPT.format(available_tags=tags_str)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_canonical_entities",
            "description": "BM25-ranked search of the canonical entity registry by text fragment. Case-insensitive, label-agnostic. Returns ranked matches with canonical text, label, description, ticker link, alias count, and BM25 score.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text fragment to search for (e.g. 'trump', 'nvidia', 'tesla')"},
                    "limit": {"type": "integer", "description": "Max results to return (default 10)", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refine_search",
            "description": "Refine the search term and re-search using BM25. Use this when the initial search didn't find what you expected and you want to try a different spelling or expanded name (e.g. 'US' -> 'United States', 'U.S.', 'United States of America').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The refined search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_to_canonical",
            "description": "TERMINAL: Link the pending entity as an alias of an existing canonical entity. Use this when the pending entity is a variant of an existing canonical. An exact-match alias for the pending text will be automatically created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "canonical_id": {"type": "integer", "description": "The ID of the existing canonical entity to link to"},
                    "rename_alias": {"type": "string", "description": "Optional: better capitalization for the alias text (defaults to the pending entity text)"},
                    "update_label": {"type": "string", "description": "Optional: reclassify the canonical's label (PERSON, ORG, etc.)"},
                    "append_description": {"type": "string", "description": "Optional: additional text to append to the canonical's description"},
                    "ticker_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: ticker tag set IDs to assign to the canonical's ticker (e.g. ['ambiguous', 'crypto'])",
                    },
                },
                "required": ["canonical_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_new_canonical",
            "description": "TERMINAL: Create a new canonical entity. The pending entity becomes its first alias (exact match). Use this when the entity is genuinely new and not a variant of any existing canonical.",
            "parameters": {
                "type": "object",
                "properties": {
                    "canonical_text": {"type": "string", "description": "The canonical display form (e.g. 'Donald Trump', 'NVIDIA Corporation')"},
                    "canonical_label": {"type": "string", "description": "The authoritative label: PERSON, ORG, GPE, PRODUCT, EVENT, NORP, FAC, WORK_OF_ART, LAW, or MISC"},
                    "description": {"type": "string", "description": "Dense 2-4 sentence summary of the entity. Must be information-rich for vector similarity matching."},
                    "ticker_link": {"type": "string", "description": "Optional: primary stock ticker symbol (e.g. 'TSLA'). The ticker will be added as an alias."},
                    "ticker_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: ticker tag set IDs to assign (e.g. ['ambiguous', 'crypto', 'etf'])",
                    },
                },
                "required": ["canonical_text", "canonical_label", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_as_misc",
            "description": "TERMINAL: Mark the pending entity as MISC (junk that spaCy mis-identifies). Creates a MISC canonical or links to an existing one. An exact-match alias for the pending text will be created so future extractions auto-link. Use this for: numbers, URLs, markdown artifacts, common words, financial acronyms that aren't companies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "misc_bucket_id": {"type": "integer", "description": "Optional: existing MISC canonical entity ID to link to"},
                    "new_bucket_name": {"type": "string", "description": "Optional: name for a new MISC bucket (e.g. 'URL Artifacts', 'Markdown Remnants')"},
                    "description": {"type": "string", "description": "Optional: description for a new MISC bucket"},
                },
            },
        },
    },
]


def make_tool_executor(db: RedditDatabase, pending_text: str, pending_label: str,
                       dry_run: bool = False):
    """Create a tool executor closure that handles all canonicalization tools."""
    available_tag_ids = {ts["id"] for ts in db.list_ticker_tag_sets()}

    def executor(tool_name: str, args: dict) -> dict:
        if tool_name == "search_canonical_entities":
            query = args.get("query", "")
            limit = args.get("limit", 10)
            results = db.search_entities_bm25(query, limit=limit)
            return {"matches": results, "count": len(results)}

        elif tool_name == "refine_search":
            query = args.get("query", "")
            results = db.search_entities_bm25(query, limit=10)
            return {"matches": results, "count": len(results)}

        elif tool_name == "link_to_canonical":
            canonical_id = args.get("canonical_id")
            if not canonical_id or not isinstance(canonical_id, int):
                return {"error": "canonical_id must be a valid integer"}

            entity = db.get_entity(canonical_id)
            if not entity:
                return {"error": f"canonical_id {canonical_id} does not exist"}

            db_effect = {"action": "link_alias", "canonical_id": canonical_id}

            if not dry_run:
                alias_text = args.get("rename_alias") or pending_text
                db.add_alias(canonical_id, alias_text, pending_label)
                db.set_named_entity_link(pending_text, pending_label, canonical_id)

                if args.get("update_label"):
                    db.update_entity(canonical_id, canonical_label=args["update_label"])
                    db_effect["label_updated"] = args["update_label"]

                if args.get("append_description"):
                    existing = entity.get("description") or ""
                    db.update_entity(canonical_id, description=existing + " " + args["append_description"])
                    db_effect["description_appended"] = True

                if args.get("ticker_tags"):
                    invalid_tags = [t for t in args["ticker_tags"] if t not in available_tag_ids]
                    if invalid_tags:
                        return {"error": f"Invalid tag IDs: {invalid_tags}. Valid: {list(available_tag_ids)}"}
                    ticker = entity.get("ticker_link")
                    if ticker:
                        for tag_id in args["ticker_tags"]:
                            db.add_tickers_to_tag(tag_id, [ticker])
                        db_effect["ticker_tags_set"] = args["ticker_tags"]
                    else:
                        return {"error": "Cannot set ticker_tags: entity has no ticker_link"}
            else:
                db_effect["dry_run"] = True

            return {"success": True, "canonical_id": canonical_id,
                    "canonical_text": entity.get("canonical_text"),
                    "db_effect": db_effect}

        elif tool_name == "create_new_canonical":
            canonical_text = args.get("canonical_text", "").strip()
            canonical_label = args.get("canonical_label", "").strip()
            description = args.get("description", "").strip()
            ticker_link = args.get("ticker_link")
            ticker_tags = args.get("ticker_tags", [])

            if not canonical_text or not canonical_label or not description:
                return {"error": "canonical_text, canonical_label, and description are all required"}

            existing = db.lookup_entity_by_text(canonical_text)
            if existing:
                return {"error": f"Canonical entity '{canonical_text}' already exists (id={existing['id']}). Use link_to_canonical instead."}

            if ticker_tags:
                invalid_tags = [t for t in ticker_tags if t not in available_tag_ids]
                if invalid_tags:
                    return {"error": f"Invalid tag IDs: {invalid_tags}. Valid: {list(available_tag_ids)}"}

            db_effect = {"action": "create_canonical"}

            if not dry_run:
                entity = db.create_entity(canonical_text, canonical_label, description,
                                          ticker_link=ticker_link, source="llm")
                entity_id = entity["id"]

                db.add_alias(entity_id, pending_text, pending_label)
                db.set_named_entity_link(pending_text, pending_label, entity_id)

                if ticker_link:
                    db.add_alias(entity_id, ticker_link.upper(), "ORG")

                if ticker_tags and ticker_link:
                    for tag_id in ticker_tags:
                        db.add_tickers_to_tag(tag_id, [ticker_link.upper()])

                db_effect["entity_id"] = entity_id
                db_effect["aliases_added"] = [pending_text]
                if ticker_link:
                    db_effect["aliases_added"].append(ticker_link.upper())
                    db_effect["ticker_tags_set"] = ticker_tags
            else:
                db_effect["dry_run"] = True

            return {"success": True, "db_effect": db_effect}

        elif tool_name == "mark_as_misc":
            db_effect = {"action": "mark_misc"}

            if not dry_run:
                misc_bucket_id = args.get("misc_bucket_id")
                if misc_bucket_id:
                    entity = db.get_entity(misc_bucket_id)
                    if not entity:
                        return {"error": f"misc_bucket_id {misc_bucket_id} does not exist"}
                    entity_id = misc_bucket_id
                else:
                    bucket_name = args.get("new_bucket_name", "MISC")
                    description = args.get("description", "Miscellaneous entities extracted by NER that are not real people, companies, or places.")
                    entity = db.create_entity(bucket_name, "MISC", description, source="llm")
                    entity_id = entity["id"]

                db.add_alias(entity_id, pending_text, pending_label)
                db.set_named_entity_link(pending_text, pending_label, entity_id)
                db_effect["entity_id"] = entity_id
            else:
                db_effect["dry_run"] = True

            return {"success": True, "db_effect": db_effect}

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    return executor


def _auto_assign(db: RedditDatabase, entity_text: str, entity_label: str,
                 canonical_id: int, dry_run: bool) -> dict:
    """Auto-assign: create alias + link named_entities. No LLM needed."""
    if not dry_run:
        db.add_alias(canonical_id, entity_text, entity_label)
        db.set_named_entity_link(entity_text, entity_label, canonical_id)
    entity = db.get_entity(canonical_id)
    return {
        "terminal_tool": "auto_link",
        "args": {"canonical_id": canonical_id,
                 "canonical_text": entity["canonical_text"] if entity else None},
        "rounds": 0,
        "content": "",
        "error": None,
        "correction_id": None,
        "trace_session_id": None,
        "auto_assigned": True,
    }


def canonicalize_entity(db: RedditDatabase, entity_text: str, entity_label: str,
                        dry_run: bool = False, initiated_by: str = "pipeline",
                        trace_db=None) -> dict:
    """Run the full canonicalization pipeline for a single entity.

    Flow:
      1. Exact match (canonical text or alias) → auto-assign, done.
      2. BM25 search → check for exact match in results → auto-assign, done.
      3. BM25 results exist → include in LLM prompt as candidates.
      4. No BM25 results → LLM can search with alternative terms.
      5. All assignments create an exact-match alias for the pending text.
    """
    from sentinel.llm_client import run_tool_session

    # Step 1: Exact match lookup
    canonical = db.lookup_entity_by_text(entity_text)
    if canonical:
        logger.debug("Auto-assign '%s' -> entity %d (exact match)", entity_text, canonical["id"])
        result = _auto_assign(db, entity_text, entity_label, canonical["id"], dry_run)
        db.add_correction(
            action="auto_link", initiated_by=initiated_by,
            pending_text=entity_text, pending_label=entity_label,
            target_canonical_id=canonical["id"], llm_tool_used="auto_link",
            after_state=None if dry_run else json.dumps({"auto_assigned": True}),
        )
        if not dry_run:
            enqueue_relevance_for_canonical(db, canonical["id"])
        return result
    bm25_results = db.search_entities_bm25(entity_text, limit=10)

    # Step 3: Check for exact match in BM25 results
    text_lower = entity_text.lower()
    for r in bm25_results:
        if r["canonical_text"].lower() == text_lower:
            logger.debug("Auto-assign '%s' -> entity %d (BM25 exact match)", entity_text, r["id"])
            result = _auto_assign(db, entity_text, entity_label, r["id"], dry_run)
            db.add_correction(
                action="auto_link", initiated_by=initiated_by,
                pending_text=entity_text, pending_label=entity_label,
                target_canonical_id=r["id"], llm_tool_used="auto_link_bm25",
                after_state=None if dry_run else json.dumps({"auto_assigned": True}),
            )
            if not dry_run:
                enqueue_relevance_for_canonical(db, r["id"])
            return result
        for mt in r.get("matched_texts", []):
            if mt.lower() == text_lower:
                logger.debug("Auto-assign '%s' -> entity %d (BM25 alias exact match)", entity_text, r["id"])
                result = _auto_assign(db, entity_text, entity_label, r["id"], dry_run)
                db.add_correction(
                    action="auto_link", initiated_by=initiated_by,
                    pending_text=entity_text, pending_label=entity_label,
                    target_canonical_id=r["id"], llm_tool_used="auto_link_bm25",
                    after_state=None if dry_run else json.dumps({"auto_assigned": True}),
                )
                if not dry_run:
                    enqueue_relevance_for_canonical(db, r["id"])
                return result

    # Step 4: Build LLM prompt with BM25 candidates (if any)
    system_prompt = build_system_prompt(db)

    candidates_str = ""
    if bm25_results:
        candidates_str = "\n\nPre-searched candidate matches (review these first):\n"
        for i, r in enumerate(bm25_results):
            candidates_str += f"  [{i+1}] id={r['id']} \"{r['canonical_text']}\" ({r['canonical_label']})"
            if r.get("ticker_link"):
                candidates_str += f" ticker={r['ticker_link']}"
            if r.get("description"):
                candidates_str += f"\n      desc: {r['description'][:200]}"
            candidates_str += f"\n      matched_texts: {r.get('matched_texts', [])}\n"
        candidates_str += "\nIf one of these is the same entity as the pending entity, use link_to_canonical with its ID. Otherwise, search with refine_search or create a new canonical.\n"

    # Fetch the most recent post containing this entity for context
    recent_post_text = db.get_recent_post_text_for_entity(entity_text, entity_label, max_chars=500)
    context_str = ""
    if recent_post_text:
        context_str = f"\n\nMost recent post mentioning this entity (first 500 chars):\n\"\"\"\n{recent_post_text}\n\"\"\"\n"

    user_message = (
        f"Entity to canonicalize:\n"
        f"  text: \"{entity_text}\"\n"
        f"  label: {entity_label}\n"
        f"{candidates_str}"
        f"{context_str}\n"
        f"Decide: link to an existing canonical (from the candidates above or by searching), "
        f"create a new canonical, or mark as MISC if it's junk."
    )

    goal = f"Canonicalize entity '{entity_text}' ({entity_label}) — {initiated_by}"
    input_context = {
        "entity_text": entity_text,
        "entity_label": entity_label,
        "initiated_by": initiated_by,
        "dry_run": dry_run,
        "bm25_candidates": bm25_results,
        "recent_post_context": recent_post_text,
    }

    session_id = None
    if trace_db:
        session_id = trace_db.start_session(
            purpose="canonicalization",
            model=MODEL,
            goal=goal,
            system_prompt=system_prompt,
            tool_definitions=TOOL_DEFINITIONS,
            input_context=input_context,
        )

    executor = make_tool_executor(db, entity_text, entity_label, dry_run=dry_run)

    result = run_tool_session(
        model=MODEL,
        system_prompt=system_prompt,
        user_message=user_message,
        tools=TOOL_DEFINITIONS,
        max_rounds=MAX_CANON_ROUNDS,
        execute_tool=executor,
        trace_db=trace_db,
        trace_session_id=session_id,
    )

    correction_id = None
    if result.terminal_tool:
        correction_id = db.add_correction(
            action=result.terminal_tool,
            initiated_by=initiated_by,
            pending_text=entity_text,
            pending_label=entity_label,
            llm_tool_used=result.terminal_tool,
            llm_session_id=session_id,
            reasoning=result.content or None,
            after_state=None if dry_run else json.dumps({"applied": True}),
        )

    if trace_db and session_id:
        status = "completed" if result.terminal_tool else ("error" if result.error else "no_tool_call")
        trace_db.complete_session(
            session_id, status=status,
            round_count=result.rounds,
        )

    # Deferred relevance: if a canonical was resolved (create/link, non-MISC),
    # enqueue relevance scoring for all sources that mention this entity.
    if not dry_run and result.terminal_tool:
        resolved = db.lookup_entity_by_text(entity_text)
        if resolved and (resolved.get("canonical_label") or "").upper() != "MISC":
            try:
                enqueue_relevance_for_canonical(db, resolved["id"])
            except Exception:
                logger.debug("Deferred relevance enqueue failed (non-critical)", exc_info=True)

    return {
        "terminal_tool": result.terminal_tool,
        "args": result.terminal_args,
        "rounds": result.rounds,
        "content": result.content,
        "error": result.error,
        "correction_id": correction_id,
        "trace_session_id": session_id,
        "auto_assigned": False,
    }


def enqueue_relevance_for_canonical(db: RedditDatabase, canonical_id: int,
                                     limit: int = 5000) -> int:
    """After a canonical entity is created or linked, enqueue relevance
    scoring for every source that mentions it (via named_entities.entity_id).

    This is the deferred-relevance mechanism: entities extracted by NER that
    had no canonical at extraction time are not scored until canonicalization
    resolves them. This function picks up those deferred sources.

    Skips MISC buckets. Idempotent — the relevance_queue UNIQUE constraint
    prevents duplicate rows. Returns the number of newly enqueued pairs.
    """
    from sentinel.relevance_utils import (
        build_post_document, build_comment_document,
        build_canonical_query, should_score,
    )

    entity = db.get_entity(canonical_id)
    if not entity:
        return 0
    if (entity.get("canonical_label") or "").upper() == "MISC":
        return 0

    sources = db.get_named_entities_by_canonical(canonical_id)
    if not sources:
        return 0

    query = build_canonical_query(entity)

    # Batch-fetch source texts
    post_ids = [s["source_id"] for s in sources if s["source_type"] == "post"]
    comment_ids = [s["source_id"] for s in sources if s["source_type"] == "comment"]

    post_texts = {}
    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        rows = db.conn.execute(f"""
            SELECT id, title, selftext FROM posts WHERE id IN ({placeholders})
        """, post_ids).fetchall()
        post_texts = {r["id"]: (r["title"], r["selftext"]) for r in rows}

    comment_texts = {}
    if comment_ids:
        placeholders = ",".join("?" * len(comment_ids))
        rows = db.conn.execute(f"""
            SELECT id, body FROM comments WHERE id IN ({placeholders})
        """, comment_ids).fetchall()
        comment_texts = {r["id"]: r["body"] for r in rows}

    count = 0
    for s in sources[:limit]:
        st, sid = s["source_type"], s["source_id"]
        if st == "post":
            title, selftext = post_texts.get(sid, (None, None))
            document = build_post_document(title, selftext)
        else:
            document = build_comment_document(comment_texts.get(sid))

        if not should_score(document):
            continue

        result = db.enqueue_relevance(
            source_type=st, source_id=sid,
            entity_type="entity", entity_ref=str(canonical_id),
            entity_text=query, document_text=document,
        )
        if result is not None:
            count += 1

    if count > 0:
        logger.info("Canonicalization: enqueued %d deferred relevance pairs for entity %d",
                    count, canonical_id)
    return count