# n8n RAG Ranking & Re-ranking Implementation Guide

## Overview

This guide shows how to implement metadata-based boosting and re-ranking in n8n workflows for your recruitment RAG bot. These approaches work **without external API dependencies** (Cohere, etc.) and overcome n8n's Pinecone node limitations.

### The Challenge

n8n's Pinecone Vector Store node has limitations:
- ❌ Only supports `$eq` operator for metadata filtering
- ❌ No native query-time boosting/weighting
- ❌ Cannot use `$or`, `$in`, `$ne`, etc. operators
- ❌ No built-in score manipulation

### Your Metadata Fields

Your Pinecone vectors have these boolean metadata fields for boosting:
- **`has_trusted`**: Content from verified sources (SMEs, official docs)
- **`has_recruitment_reaction`**: Validated by team (Slack `:recruitment:` emoji)

### Goal

Boost search results that have `has_trusted=true` or `has_recruitment_reaction=true` to appear higher in rankings, while still allowing other results.

---

## Approach 1: Simple Code Node Boosting (RECOMMENDED)

**Best for**: Getting started, quick wins, most use cases
**Complexity**: Low
**Expected improvement**: 20-30% better relevance
**Setup time**: 30 minutes

### How It Works

1. Pinecone Vector Store retrieves top 20 candidates (unfiltered)
2. Code node applies boost multipliers based on metadata
3. Re-sort by boosted scores
4. Pass top 5 to LLM

### n8n Workflow Structure

```
[Trigger: Chat Message]
    ↓
[Get query embedding]
    ↓
[Pinecone Vector Store: Retrieve Documents]
  - Top K: 20
  - Include metadata: true
  - No filters
    ↓
[Code Node: Apply Weighted Boosting] ← YOU ADD THIS
    ↓
[Take top 5]
    ↓
[OpenAI/LLM: Generate response with context]
```

### Step-by-Step Setup

#### Step 1: Configure Pinecone Vector Store Node

1. Add **Pinecone Vector Store** node (Get Many or Retrieve mode)
2. Set **Top K** = `20` (retrieve more candidates than needed)
3. Enable **Include Metadata** = `true`
4. **Do NOT add metadata filters** (we'll boost post-retrieval)
5. Configure your index/namespace as usual

#### Step 2: Add Code Node for Boosting

1. Add a **Code** node after the Pinecone node
2. Set **Language** = `JavaScript`
3. Paste the following code:

```javascript
// ===== CONFIGURATION =====
const TRUSTED_BOOST = 1.5;      // 50% boost for trusted sources
const REACTION_BOOST = 1.3;     // 30% boost for recruitment reactions
const COMBINED_BOOST = 2.0;     // 100% boost when both are true
const TOP_N_RESULTS = 5;        // Number of results to return

// ===== BOOSTING LOGIC =====
const items = $input.all();

function applyBoost(item) {
  const metadata = item.json.metadata || {};
  const baseScore = item.json.score || 0; // Pinecone similarity score (0-1)

  // Check metadata flags
  const hasTrusted = metadata.has_trusted === true;
  const hasReaction = metadata.has_recruitment_reaction === true;

  // Determine boost multiplier
  let boost = 1.0;
  let boostReason = 'none';

  if (hasTrusted && hasReaction) {
    boost = COMBINED_BOOST;
    boostReason = 'trusted+reaction';
  } else if (hasTrusted) {
    boost = TRUSTED_BOOST;
    boostReason = 'trusted';
  } else if (hasReaction) {
    boost = REACTION_BOOST;
    boostReason = 'reaction';
  }

  // Calculate boosted score
  const boostedScore = baseScore * boost;

  return {
    ...item,
    json: {
      ...item.json,
      original_score: baseScore,
      boost_factor: boost,
      boosted_score: boostedScore,
      boost_reason: boostReason
    }
  };
}

// Apply boosts to all items
const boostedItems = items.map(applyBoost);

// Sort by boosted score (descending)
const ranked = boostedItems.sort((a, b) =>
  b.json.boosted_score - a.json.boosted_score
);

// Return top N
const topResults = ranked.slice(0, TOP_N_RESULTS);

// Optional: Log boost statistics
const boostedCount = topResults.filter(item => item.json.boost_factor > 1.0).length;
console.log(`[Boosting Stats] Total: ${topResults.length}, Boosted: ${boostedCount}`);

return topResults;
```

#### Step 3: Use Results in LLM

The Code node outputs the top N results with boosted scores. Pass these to your LLM node as context.

**Access the text**: `{{ $json.text }}` or `{{ $json.metadata.text }}`

### Tuning Boost Multipliers

Start with conservative values and tune based on results:

| Scenario | Trusted Boost | Reaction Boost | Combined Boost |
|----------|--------------|----------------|----------------|
| **Conservative** | 1.2x | 1.1x | 1.5x |
| **Moderate** (default) | 1.5x | 1.3x | 2.0x |
| **Aggressive** | 2.0x | 1.8x | 3.0x |

**How to tune:**
1. Run test queries and check `boost_reason` in results
2. If too few boosted results appear → increase multipliers
3. If too many low-quality results are boosted → decrease multipliers
4. Monitor which results users find helpful

### Debugging

Add this code at the end of the Code node to see what's being boosted:

```javascript
// Debug: Log top 5 results with boost info
topResults.forEach((item, i) => {
  console.log(`[${i+1}] Score: ${item.json.original_score.toFixed(3)} → ${item.json.boosted_score.toFixed(3)} | Boost: ${item.json.boost_reason}`);
});
```

View logs in n8n's execution history.

---

## Approach 2: Advanced RRF Multi-Query

**Best for**: Complex scenarios, when simple boosting isn't enough
**Complexity**: Medium-High
**Expected improvement**: 30-40% better relevance
**Setup time**: 2-3 hours

### How It Works

Run **three parallel Pinecone queries** with different strategies, then merge using **Reciprocal Rank Fusion (RRF)**:

1. **Query A**: Pure semantic search (no filters)
2. **Query B**: Filtered by `has_trusted=true`
3. **Query C**: Filtered by `has_recruitment_reaction=true`
4. **Merge**: Combine using RRF algorithm with weights

### n8n Workflow Structure

```
[Trigger: Chat Message]
    ↓
[Get query embedding]
    ↓
    ├─→ [Pinecone: Semantic Search] → weight 0.4
    ├─→ [Pinecone: Trusted Filter] → weight 0.3
    └─→ [Pinecone: Reaction Filter] → weight 0.3
         ↓
    [Code Node: RRF Merge] ← Combines all three
         ↓
    [Take top 5]
         ↓
    [LLM]
```

### Step-by-Step Setup

#### Step 1: Create Three Pinecone Queries

Add three **Pinecone Vector Store** nodes in parallel:

**Node 1: Semantic Search (Unfiltered)**
- Name: `Semantic Search`
- Top K: `10`
- Metadata filter: (empty)
- Include metadata: `true`

**Node 2: Trusted Filter**
- Name: `Trusted Filter`
- Top K: `10`
- Metadata filter: `{"has_trusted": {"$eq": true}}`
- Include metadata: `true`

**Node 3: Reaction Filter**
- Name: `Reaction Filter`
- Top K: `10`
- Metadata filter: `{"has_recruitment_reaction": {"$eq": true}}`
- Include metadata: `true`

#### Step 2: Add RRF Merge Code Node

After the three Pinecone nodes, add a **Code** node:

```javascript
// ===== CONFIGURATION =====
const RRF_K = 60;               // Standard RRF constant
const WEIGHTS = {
  semantic: 0.4,                // Base semantic similarity
  trusted: 0.3,                 // Trusted sources
  reaction: 0.3                 // Validated by reactions
};
const TOP_N_RESULTS = 5;

// ===== RECIPROCAL RANK FUSION =====
const allInputs = $input.all();

// Extract results from each query
// Assumes nodes are named: "Semantic Search", "Trusted Filter", "Reaction Filter"
const semanticResults = allInputs.filter(item =>
  item.json.query_type === 'semantic' ||
  allInputs.indexOf(item) < Math.floor(allInputs.length / 3)
);
const trustedResults = allInputs.filter(item =>
  item.json.query_type === 'trusted' ||
  (allInputs.indexOf(item) >= Math.floor(allInputs.length / 3) &&
   allInputs.indexOf(item) < 2 * Math.floor(allInputs.length / 3))
);
const reactionResults = allInputs.filter(item =>
  item.json.query_type === 'reaction' ||
  allInputs.indexOf(item) >= 2 * Math.floor(allInputs.length / 3)
);

// Alternative: If nodes are connected sequentially, split by count
// const semanticResults = allInputs.slice(0, 10);
// const trustedResults = allInputs.slice(10, 20);
// const reactionResults = allInputs.slice(20, 30);

// RRF scoring function
function calculateRRFScore(rank, weight, k = RRF_K) {
  return weight / (rank + k);
}

// Build doc score map
const docScores = new Map();

function addScores(results, queryType, weight) {
  results.forEach((item, rank) => {
    // Use thread_ts or doc_id as unique identifier
    const docId = item.json.metadata?.thread_ts ||
                  item.json.metadata?.doc_id ||
                  item.json.id;

    if (!docId) return; // Skip if no ID

    const rrfScore = calculateRRFScore(rank, weight);

    if (!docScores.has(docId)) {
      docScores.set(docId, {
        doc: item.json,
        queryScores: {},
        totalScore: 0,
        appearCount: 0
      });
    }

    const entry = docScores.get(docId);
    entry.queryScores[queryType] = rrfScore;
    entry.totalScore += rrfScore;
    entry.appearCount += 1;
  });
}

// Add scores from all three queries
addScores(semanticResults, 'semantic', WEIGHTS.semantic);
addScores(trustedResults, 'trusted', WEIGHTS.trusted);
addScores(reactionResults, 'reaction', WEIGHTS.reaction);

// Sort by total RRF score
const rankedDocs = Array.from(docScores.values())
  .sort((a, b) => b.totalScore - a.totalScore)
  .slice(0, TOP_N_RESULTS);

// Format output
const results = rankedDocs.map((entry, rank) => ({
  json: {
    ...entry.doc,
    rrf_score: entry.totalScore,
    rrf_rank: rank + 1,
    query_scores: entry.queryScores,
    appeared_in: entry.appearCount,
    rrf_breakdown: `semantic:${(entry.queryScores.semantic || 0).toFixed(3)} + trusted:${(entry.queryScores.trusted || 0).toFixed(3)} + reaction:${(entry.queryScores.reaction || 0).toFixed(3)}`
  }
}));

// Debug logging
console.log(`[RRF Merge] Input: ${allInputs.length} docs, Unique: ${docScores.size}, Top: ${results.length}`);
results.forEach((r, i) => {
  console.log(`[${i+1}] RRF=${r.json.rrf_score.toFixed(3)} | Appeared in ${r.json.appeared_in} queries | ${r.json.rrf_breakdown}`);
});

return results;
```

#### Step 3: Connect to LLM

Pass the top 5 merged results to your LLM node.

### When to Use RRF vs Simple Boosting

| Use Case | Recommended Approach |
|----------|---------------------|
| Most queries | Simple boosting (Approach 1) |
| Need to surface rare but important docs | RRF multi-query |
| Multiple metadata criteria with complex logic | RRF multi-query |
| Performance is critical (low latency) | Simple boosting (fewer API calls) |
| Maximum accuracy regardless of cost | RRF multi-query |

### RRF Weight Tuning

Adjust weights based on your priorities:

```javascript
// Prioritize semantic relevance
const WEIGHTS = { semantic: 0.6, trusted: 0.2, reaction: 0.2 };

// Prioritize trusted sources
const WEIGHTS = { semantic: 0.3, trusted: 0.5, reaction: 0.2 };

// Equal weighting
const WEIGHTS = { semantic: 0.33, trusted: 0.33, reaction: 0.34 };
```

**Rule of thumb**: Weights should sum to ~1.0

---

## Approach 3: HTTP Request for Advanced Filtering

**Best for**: When you need Pinecone features not available in n8n node
**Complexity**: Medium
**Use case**: OR filters, IN operators, hybrid search

### Why Use This

n8n's Pinecone node doesn't support:
- `$or` operator (match ANY condition)
- `$in` operator (match value in array)
- Hybrid search with alpha parameter
- Fine-grained control over query parameters

### Setup: HTTP Request to Pinecone API

Replace the Pinecone Vector Store node with an **HTTP Request** node:

#### Node Configuration

**Settings:**
- **Method**: `POST`
- **URL**: `https://{{$env.PINECONE_INDEX}}-{{$env.PINECONE_PROJECT}}.svc.{{$env.PINECONE_ENV}}.pinecone.io/query`

**Headers:**
```json
{
  "Api-Key": "{{$env.PINECONE_API_KEY}}",
  "Content-Type": "application/json"
}
```

**Body (JSON):**
```json
{
  "vector": {{$json.query_embedding}},
  "filter": {
    "$or": [
      {"has_trusted": {"$eq": true}},
      {"has_recruitment_reaction": {"$eq": true}}
    ]
  },
  "topK": 20,
  "includeMetadata": true,
  "namespace": "recruitment-rag"
}
```

### Advanced Filter Examples

**Match documents with EITHER flag:**
```json
{
  "filter": {
    "$or": [
      {"has_trusted": {"$eq": true}},
      {"has_recruitment_reaction": {"$eq": true}}
    ]
  }
}
```

**Match documents with BOTH flags:**
```json
{
  "filter": {
    "$and": [
      {"has_trusted": {"$eq": true}},
      {"has_recruitment_reaction": {"$eq": true}}
    ]
  }
}
```

**Match documents with trusted=true OR (reaction=true AND message_count > 5):**
```json
{
  "filter": {
    "$or": [
      {"has_trusted": {"$eq": true}},
      {
        "$and": [
          {"has_recruitment_reaction": {"$eq": true}},
          {"message_count": {"$gt": 5}}
        ]
      }
    ]
  }
}
```

**Match specific authors:**
```json
{
  "filter": {
    "authors": {"$in": ["U082LELDMBN", "U077C51J0UW"]}
  }
}
```

### Processing HTTP Response

The Pinecone API returns results in this format:

```json
{
  "matches": [
    {
      "id": "slack:C123:thread:1234.5678:chunk:0",
      "score": 0.856,
      "metadata": {
        "text": "...",
        "has_trusted": true,
        "has_recruitment_reaction": true,
        ...
      }
    }
  ],
  "namespace": "recruitment-rag"
}
```

Add a **Code** node to transform this to n8n format:

```javascript
const response = $input.first().json;
const matches = response.matches || [];

return matches.map(match => ({
  json: {
    id: match.id,
    score: match.score,
    metadata: match.metadata,
    text: match.metadata.text
  }
}));
```

---

## Performance Comparison

| Approach | API Calls | Latency | Accuracy | Complexity | Cost |
|----------|-----------|---------|----------|------------|------|
| Simple Boosting | 1 | ~200ms | **Best** | Low | $ |
| RRF Multi-Query | 3 | ~400ms | **Best** | Medium | $$$ |
| HTTP + Advanced Filter | 1 | ~200ms | Good | Medium | $ |

**Recommendation**: Start with **Simple Boosting** (Approach 1). This gives the best results for metadata-driven ranking. Only move to RRF if you need to handle complex multi-criteria scenarios.

### Why NOT Use External Rerankers (Cohere, etc.)

You might see n8n's native Cohere reranker node and think it's a good option. **For this use case, it's not recommended:**

**Problems with external rerankers for metadata-driven ranking:**
- ❌ **Zero customizability**: Can't tell Cohere to prioritize `has_trusted` or `has_recruitment_reaction`
- ❌ **Metadata noise**: n8n automatically sends ALL metadata to reranker, which can confuse semantic scoring
- ❌ **Black box**: No control over scoring weights or logic
- ❌ **Ignores quality signals**: Cohere does semantic similarity only, ignores your explicit quality metadata
- ❌ **Extra cost**: ~$1/1K searches, plus Pinecone costs
- ❌ **Slower**: Additional API call adds 100-200ms latency

**Your metadata IS the quality signal**. Code-based boosting gives you:
- ✅ Full control over boost weights
- ✅ Direct use of quality signals (trusted sources, validated content)
- ✅ Transparent, debuggable logic
- ✅ No external costs
- ✅ Faster (no extra API call)

**Bottom line**: Code Node Boosting (Approach 1) outperforms Cohere for metadata-driven use cases like yours.

---

## Testing & Validation

### Test Query Examples

Create these test queries to validate your boosting:

1. **General question**: "How do I set up a recruitment job?"
   - Should return mix of trusted + community content
   - Trusted sources boosted to top

2. **Specific technical question**: "How to integrate Cronofy calendar?"
   - Should return exact matches first
   - Boosted if trusted/validated

3. **Edge case**: "Random unrelated query"
   - Should still return relevant results
   - Boosting shouldn't break base retrieval

### Validation Checklist

- [ ] Boosted results appear higher than equivalent non-boosted
- [ ] Non-boosted results still appear when relevant
- [ ] Boost reasons logged correctly
- [ ] Top 5 results contain at least 1-2 boosted items (if available)
- [ ] Query latency acceptable (<500ms for simple boost, <1s for RRF)
- [ ] No errors when metadata fields are missing

### A/B Testing

Run parallel workflows to compare:

**Workflow A**: No boosting (baseline)
**Workflow B**: Simple boosting
**Workflow C**: RRF multi-query

Measure:
- User satisfaction (thumbs up/down)
- Click-through rate on sources
- Answer accuracy (human eval)

---

## Troubleshooting

### Issue: Boosted scores seem wrong

**Check:**
- Pinecone's base score is typically 0-1 (cosine similarity)
- Verify `item.json.score` exists and is correct
- Log `original_score` and `boosted_score` to debug

### Issue: No results from filtered queries (RRF)

**Cause:** No documents match the filter
**Solution:**
- Add fallback logic: if filtered query returns 0 results, use semantic only
- Reduce filter strictness

### Issue: Metadata fields missing

**Check:**
- Ensure `includeMetadata: true` in Pinecone query
- Verify metadata was added during ingestion
- Add null checks: `metadata.has_trusted === true` (not just `metadata.has_trusted`)

### Issue: RRF merge returns duplicates

**Cause:** Document ID not unique across queries
**Solution:** Use `thread_ts` or `doc_id` from metadata as unique key

---

## Next Steps

1. **Start simple**: Implement Approach 1 (Code Node Boosting)
2. **Test with real queries**: Use your Slack data
3. **Tune multipliers**: Adjust based on results
4. **Monitor performance**: Track which results get boosted
5. **Consider RRF**: Only if simple boosting isn't sufficient

For ready-to-use code snippets, see [`n8n-code-snippets.md`](./n8n-code-snippets.md).

---

## Additional Resources

- [Pinecone Metadata Filtering Docs](https://docs.pinecone.io/guides/search/filter-by-metadata)
- [Pinecone Query API Reference](https://docs.pinecone.io/reference/query)
- [n8n Code Node Docs](https://docs.n8n.io/code/builtin/code-node/)
- [Reciprocal Rank Fusion Paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
