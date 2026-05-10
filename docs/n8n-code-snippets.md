# n8n Code Snippets for RAG Ranking

Ready-to-use code templates for implementing metadata-based boosting in n8n Code nodes. All snippets are optimized for the recruitment RAG bot with `has_trusted` and `has_recruitment_reaction` metadata.

---

## Table of Contents

1. [Simple Weighted Boosting](#1-simple-weighted-boosting)
2. [Reciprocal Rank Fusion (RRF)](#2-reciprocal-rank-fusion-rrf)
3. [HTTP Request to Pinecone API](#3-http-request-to-pinecone-api)
4. [Debugging & Logging Helpers](#4-debugging--logging-helpers)
5. [Utility Functions](#5-utility-functions)

---

## 1. Simple Weighted Boosting

### Basic Boosting (Copy-Paste Ready)

Use in a **Code** node after Pinecone Vector Store retrieval.

```javascript
// ===== CONFIGURATION =====
const TRUSTED_BOOST = 1.5;
const REACTION_BOOST = 1.3;
const COMBINED_BOOST = 2.0;
const TOP_N = 5;

// ===== MAIN LOGIC =====
const items = $input.all();

const results = items
  .map(item => {
    const meta = item.json.metadata || {};
    const score = item.json.score || 0;

    const hasTrusted = meta.has_trusted === true;
    const hasReaction = meta.has_recruitment_reaction === true;

    let boost = 1.0;
    if (hasTrusted && hasReaction) boost = COMBINED_BOOST;
    else if (hasTrusted) boost = TRUSTED_BOOST;
    else if (hasReaction) boost = REACTION_BOOST;

    return {
      ...item,
      json: {
        ...item.json,
        boosted_score: score * boost,
        boost_factor: boost
      }
    };
  })
  .sort((a, b) => b.json.boosted_score - a.json.boosted_score)
  .slice(0, TOP_N);

return results;
```

---

### Advanced Boosting with Logging

Includes detailed logging for debugging.

```javascript
// ===== CONFIGURATION =====
const BOOSTS = {
  trusted_only: 1.5,
  reaction_only: 1.3,
  both: 2.0,
  none: 1.0
};
const TOP_N = 5;

// ===== MAIN LOGIC =====
const items = $input.all();

function determineBoost(metadata) {
  const hasTrusted = metadata.has_trusted === true;
  const hasReaction = metadata.has_recruitment_reaction === true;

  if (hasTrusted && hasReaction) return { boost: BOOSTS.both, reason: 'trusted+reaction' };
  if (hasTrusted) return { boost: BOOSTS.trusted_only, reason: 'trusted' };
  if (hasReaction) return { boost: BOOSTS.reaction_only, reason: 'reaction' };
  return { boost: BOOSTS.none, reason: 'none' };
}

const boostedItems = items.map((item, index) => {
  const metadata = item.json.metadata || {};
  const baseScore = item.json.score || 0;
  const { boost, reason } = determineBoost(metadata);
  const boostedScore = baseScore * boost;

  return {
    ...item,
    json: {
      ...item.json,
      original_score: baseScore,
      boost_factor: boost,
      boosted_score: boostedScore,
      boost_reason: reason,
      original_rank: index + 1
    }
  };
});

// Sort by boosted score
const ranked = boostedItems.sort((a, b) => b.json.boosted_score - a.json.boosted_score);

// Take top N
const topResults = ranked.slice(0, TOP_N);

// Logging
const boostedCount = topResults.filter(r => r.json.boost_factor > 1.0).length;
console.log(`[Boost Stats] Total: ${topResults.length}, Boosted: ${boostedCount}, Avg boost: ${(topResults.reduce((sum, r) => sum + r.json.boost_factor, 0) / topResults.length).toFixed(2)}x`);

topResults.forEach((r, i) => {
  const scoreChange = ((r.json.boosted_score - r.json.original_score) / r.json.original_score * 100).toFixed(1);
  console.log(`  [${i+1}] ${r.json.boost_reason} | Rank ${r.json.original_rank}→${i+1} | Score: ${r.json.original_score.toFixed(3)}→${r.json.boosted_score.toFixed(3)} (+${scoreChange}%)`);
});

return topResults;
```

---

### Boosting with Fallback

Ensures you always return results even if no boosted items exist.

```javascript
// ===== CONFIGURATION =====
const TRUSTED_BOOST = 1.5;
const REACTION_BOOST = 1.3;
const COMBINED_BOOST = 2.0;
const TOP_N = 5;
const MIN_BOOSTED = 2; // Minimum boosted results desired

// ===== MAIN LOGIC =====
const items = $input.all();

const boosted = items.map(item => {
  const meta = item.json.metadata || {};
  const score = item.json.score || 0;

  const hasTrusted = meta.has_trusted === true;
  const hasReaction = meta.has_recruitment_reaction === true;

  let boost = 1.0;
  if (hasTrusted && hasReaction) boost = COMBINED_BOOST;
  else if (hasTrusted) boost = TRUSTED_BOOST;
  else if (hasReaction) boost = REACTION_BOOST;

  return {
    ...item,
    json: { ...item.json, boosted_score: score * boost, is_boosted: boost > 1.0 }
  };
});

const sorted = boosted.sort((a, b) => b.json.boosted_score - a.json.boosted_score);
const topResults = sorted.slice(0, TOP_N);
const boostedInTop = topResults.filter(r => r.json.is_boosted).length;

// Warning if not enough boosted results
if (boostedInTop < MIN_BOOSTED) {
  console.warn(`[Boost Warning] Only ${boostedInTop}/${MIN_BOOSTED} boosted results in top ${TOP_N}. Consider lowering boost thresholds or adding more metadata.`);
}

return topResults;
```

---

## 2. Reciprocal Rank Fusion (RRF)

### Standard RRF Implementation

Merges three query result sets using RRF algorithm.

```javascript
// ===== CONFIGURATION =====
const RRF_K = 60;  // Standard constant
const WEIGHTS = {
  semantic: 0.4,
  trusted: 0.3,
  reaction: 0.3
};
const TOP_N = 5;

// ===== HELPER FUNCTIONS =====
function calculateRRF(rank, weight, k = RRF_K) {
  return weight / (rank + k);
}

function getDocId(doc) {
  return doc.metadata?.thread_ts ||
         doc.metadata?.doc_id ||
         doc.id ||
         doc.metadata?.path;
}

// ===== MAIN LOGIC =====
const allInputs = $input.all();

// Split inputs into three query results
// Assumes: First 10 = semantic, next 10 = trusted, last 10 = reaction
const semanticResults = allInputs.slice(0, 10);
const trustedResults = allInputs.slice(10, 20);
const reactionResults = allInputs.slice(20, 30);

// Build score map
const docScores = new Map();

function addQueryScores(results, queryName, weight) {
  results.forEach((item, rank) => {
    const docId = getDocId(item.json);
    if (!docId) return;

    const rrfScore = calculateRRF(rank, weight);

    if (!docScores.has(docId)) {
      docScores.set(docId, {
        doc: item.json,
        scores: {},
        total: 0,
        appearances: 0
      });
    }

    const entry = docScores.get(docId);
    entry.scores[queryName] = rrfScore;
    entry.total += rrfScore;
    entry.appearances += 1;
  });
}

addQueryScores(semanticResults, 'semantic', WEIGHTS.semantic);
addQueryScores(trustedResults, 'trusted', WEIGHTS.trusted);
addQueryScores(reactionResults, 'reaction', WEIGHTS.reaction);

// Sort and format
const merged = Array.from(docScores.values())
  .sort((a, b) => b.total - a.total)
  .slice(0, TOP_N)
  .map((entry, rank) => ({
    json: {
      ...entry.doc,
      rrf_score: entry.total,
      rrf_rank: rank + 1,
      appeared_in_queries: entry.appearances,
      query_scores: entry.scores
    }
  }));

return merged;
```

---

### RRF with Query Type Detection

Automatically detects which query each result came from.

```javascript
// ===== CONFIGURATION =====
const RRF_K = 60;
const WEIGHTS = { semantic: 0.4, trusted: 0.3, reaction: 0.3 };
const TOP_N = 5;

// ===== HELPER FUNCTIONS =====
function detectQueryType(item) {
  const meta = item.json.metadata || {};

  // Check if item has markers indicating which query it came from
  if (item.json.query_type) return item.json.query_type;

  // Heuristic: if metadata shows filtering, assume filtered query
  if (meta.has_trusted === true && meta.has_recruitment_reaction !== true) {
    return 'trusted';
  }
  if (meta.has_recruitment_reaction === true && meta.has_trusted !== true) {
    return 'reaction';
  }

  return 'semantic'; // Default
}

function calculateRRF(rank, weight, k = RRF_K) {
  return weight / (rank + k);
}

// ===== MAIN LOGIC =====
const allInputs = $input.all();

// Group by query type
const grouped = {
  semantic: [],
  trusted: [],
  reaction: []
};

allInputs.forEach(item => {
  const type = detectQueryType(item);
  if (grouped[type]) grouped[type].push(item);
});

// RRF scoring
const docScores = new Map();

Object.keys(grouped).forEach(queryType => {
  const weight = WEIGHTS[queryType] || 0.33;
  grouped[queryType].forEach((item, rank) => {
    const docId = item.json.metadata?.thread_ts || item.json.id;
    const rrfScore = calculateRRF(rank, weight);

    if (!docScores.has(docId)) {
      docScores.set(docId, {
        doc: item.json,
        scores: {},
        total: 0,
        sources: []
      });
    }

    const entry = docScores.get(docId);
    entry.scores[queryType] = rrfScore;
    entry.total += rrfScore;
    entry.sources.push(queryType);
  });
});

// Output
const results = Array.from(docScores.values())
  .sort((a, b) => b.total - a.total)
  .slice(0, TOP_N)
  .map(entry => ({
    json: {
      ...entry.doc,
      rrf_score: entry.total,
      query_sources: entry.sources,
      score_breakdown: entry.scores
    }
  }));

console.log(`[RRF] Merged ${allInputs.length} inputs → ${docScores.size} unique → top ${results.length}`);
return results;
```

---

## 3. HTTP Request to Pinecone API

### Basic HTTP Query Configuration

For use in **HTTP Request** node.

**URL:**
```
https://{{$env.PINECONE_INDEX}}-{{$env.PINECONE_PROJECT}}.svc.{{$env.PINECONE_ENV}}.pinecone.io/query
```

**Headers:**
```json
{
  "Api-Key": "{{$env.PINECONE_API_KEY}}",
  "Content-Type": "application/json",
  "Accept": "application/json"
}
```

**Body (JSON):**
```json
{
  "vector": {{$json.embedding}},
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

---

### Response Transformer Code

Use in **Code** node after HTTP Request to transform Pinecone API response to n8n format.

```javascript
const response = $input.first().json;
const matches = response.matches || [];

if (matches.length === 0) {
  console.warn('[Pinecone] No matches returned');
  return [];
}

const transformed = matches.map((match, index) => ({
  json: {
    id: match.id,
    score: match.score,
    metadata: match.metadata || {},
    text: match.metadata?.text || '',
    rank: index + 1,
    namespace: response.namespace
  }
}));

console.log(`[Pinecone] Retrieved ${transformed.length} results`);
return transformed;
```

---

### Dynamic Filter Builder

Builds Pinecone filters dynamically based on query context.

```javascript
// ===== CONFIGURATION =====
const ENABLE_TRUSTED_FILTER = true;
const ENABLE_REACTION_FILTER = true;
const USE_OR_LOGIC = true; // true = ANY condition, false = ALL conditions

// ===== BUILD FILTER =====
const filters = [];

if (ENABLE_TRUSTED_FILTER) {
  filters.push({"has_trusted": {"$eq": true}});
}

if (ENABLE_REACTION_FILTER) {
  filters.push({"has_recruitment_reaction": {"$eq": true}});
}

let finalFilter = {};

if (filters.length === 0) {
  finalFilter = {}; // No filter
} else if (filters.length === 1) {
  finalFilter = filters[0]; // Single filter
} else {
  finalFilter = USE_OR_LOGIC
    ? {"$or": filters}
    : {"$and": filters};
}

// Output for HTTP Request node body
return [{
  json: {
    vector: $json.embedding,
    filter: finalFilter,
    topK: 20,
    includeMetadata: true,
    namespace: "recruitment-rag"
  }
}];
```

---

## 4. Debugging & Logging Helpers

### Score Distribution Analyzer

Analyze score distributions to tune boost values.

```javascript
const items = $input.all();

const scores = items.map(item => item.json.score || 0);
const boostedScores = items.map(item => item.json.boosted_score || item.json.score || 0);

function stats(arr) {
  const sorted = arr.slice().sort((a, b) => a - b);
  return {
    min: sorted[0],
    max: sorted[sorted.length - 1],
    avg: arr.reduce((sum, v) => sum + v, 0) / arr.length,
    median: sorted[Math.floor(sorted.length / 2)],
    p25: sorted[Math.floor(sorted.length * 0.25)],
    p75: sorted[Math.floor(sorted.length * 0.75)]
  };
}

const originalStats = stats(scores);
const boostedStats = stats(boostedScores);

console.log('[Score Analysis]');
console.log('  Original:', JSON.stringify(originalStats, null, 2));
console.log('  Boosted:', JSON.stringify(boostedStats, null, 2));
console.log(`  Average boost: ${(boostedStats.avg / originalStats.avg).toFixed(2)}x`);

// Pass through items unchanged
return items;
```

---

### Metadata Coverage Report

Check how many results have the boost metadata fields.

```javascript
const items = $input.all();

const coverage = {
  total: items.length,
  has_trusted_count: 0,
  has_reaction_count: 0,
  has_both_count: 0,
  has_neither_count: 0,
  missing_metadata: 0
};

items.forEach(item => {
  const meta = item.json.metadata;

  if (!meta) {
    coverage.missing_metadata++;
    return;
  }

  const hasTrusted = meta.has_trusted === true;
  const hasReaction = meta.has_recruitment_reaction === true;

  if (hasTrusted) coverage.has_trusted_count++;
  if (hasReaction) coverage.has_reaction_count++;

  if (hasTrusted && hasReaction) coverage.has_both_count++;
  else if (!hasTrusted && !hasReaction) coverage.has_neither_count++;
});

console.log('[Metadata Coverage]');
console.log(`  Total results: ${coverage.total}`);
console.log(`  Has trusted: ${coverage.has_trusted_count} (${(coverage.has_trusted_count/coverage.total*100).toFixed(1)}%)`);
console.log(`  Has reaction: ${coverage.has_reaction_count} (${(coverage.has_reaction_count/coverage.total*100).toFixed(1)}%)`);
console.log(`  Has both: ${coverage.has_both_count} (${(coverage.has_both_count/coverage.total*100).toFixed(1)}%)`);
console.log(`  Has neither: ${coverage.has_neither_count} (${(coverage.has_neither_count/coverage.total*100).toFixed(1)}%)`);
console.log(`  Missing metadata: ${coverage.missing_metadata}`);

// Pass through
return items;
```

---

### Ranking Change Tracker

Track how boosting changed result rankings.

```javascript
const items = $input.all();

// Store original ranks
const withRanks = items.map((item, index) => ({
  ...item,
  json: {
    ...item.json,
    original_rank: index + 1
  }
}));

// Apply boosting (example)
const BOOST = 1.5;
const boosted = withRanks.map(item => {
  const meta = item.json.metadata || {};
  const boost = meta.has_trusted === true ? BOOST : 1.0;
  return {
    ...item,
    json: {
      ...item.json,
      boosted_score: (item.json.score || 0) * boost,
      boost_applied: boost
    }
  };
});

// Re-rank
const reranked = boosted.sort((a, b) => b.json.boosted_score - a.json.boosted_score);

// Calculate rank changes
const withChanges = reranked.map((item, newIndex) => {
  const newRank = newIndex + 1;
  const oldRank = item.json.original_rank;
  const change = oldRank - newRank; // Positive = moved up

  return {
    ...item,
    json: {
      ...item.json,
      new_rank: newRank,
      rank_change: change,
      rank_change_label: change > 0 ? `↑${change}` : change < 0 ? `↓${Math.abs(change)}` : '='
    }
  };
});

// Log significant changes
console.log('[Ranking Changes]');
withChanges.forEach(item => {
  if (Math.abs(item.json.rank_change) >= 3) { // Moved 3+ positions
    console.log(`  ${item.json.rank_change_label} Rank ${item.json.original_rank}→${item.json.new_rank} | Boost: ${item.json.boost_applied}x | ID: ${item.json.id}`);
  }
});

return withChanges.slice(0, 5); // Top 5
```

---

## 5. Utility Functions

### Safe Metadata Access

Safely access metadata fields with defaults.

```javascript
function getMetadata(item, field, defaultValue = null) {
  try {
    return item.json?.metadata?.[field] ?? defaultValue;
  } catch {
    return defaultValue;
  }
}

// Usage
const items = $input.all();
items.forEach(item => {
  const trusted = getMetadata(item, 'has_trusted', false);
  const reaction = getMetadata(item, 'has_recruitment_reaction', false);
  console.log(`Trusted: ${trusted}, Reaction: ${reaction}`);
});

return items;
```

---

### Normalize Scores to 0-100

Convert similarity scores to percentage scale.

```javascript
function normalizeScore(score, min = 0, max = 1) {
  return Math.round(((score - min) / (max - min)) * 100);
}

const items = $input.all();

const normalized = items.map(item => ({
  ...item,
  json: {
    ...item.json,
    score_raw: item.json.score,
    score_percent: normalizeScore(item.json.score || 0)
  }
}));

return normalized;
```

---

### Deduplicate Results

Remove duplicate documents based on ID or content hash.

```javascript
function deduplicateById(items, idField = 'id') {
  const seen = new Set();
  const unique = [];

  items.forEach(item => {
    const id = item.json[idField] || item.json.metadata?.[idField];
    if (!id || seen.has(id)) return;

    seen.add(id);
    unique.push(item);
  });

  console.log(`[Dedup] ${items.length} → ${unique.length} (removed ${items.length - unique.length})`);
  return unique;
}

// Usage
const items = $input.all();
const deduplicated = deduplicateById(items, 'thread_ts');
return deduplicated;
```

---

### Batch Items for Processing

Split large result sets into batches.

```javascript
function batchItems(items, batchSize = 5) {
  const batches = [];
  for (let i = 0; i < items.length; i += batchSize) {
    batches.push(items.slice(i, i + batchSize));
  }
  return batches;
}

// Usage
const items = $input.all();
const batches = batchItems(items, 5);

console.log(`[Batch] Split ${items.length} items into ${batches.length} batches of 5`);

// Return first batch (or process each batch differently)
return batches[0];
```

---

## Quick Reference

| Task | Snippet |
|------|---------|
| Basic boosting | [Simple Weighted Boosting](#basic-boosting-copy-paste-ready) |
| RRF merge | [Standard RRF Implementation](#standard-rrf-implementation) |
| Pinecone HTTP query | [Basic HTTP Query Configuration](#basic-http-query-configuration) |
| Debug scores | [Score Distribution Analyzer](#score-distribution-analyzer) |
| Check metadata | [Metadata Coverage Report](#metadata-coverage-report) |
| Track ranking changes | [Ranking Change Tracker](#ranking-change-tracker) |

---

## Tips

1. **Always validate metadata exists** before checking values:
   ```javascript
   const hasTrusted = item.json?.metadata?.has_trusted === true;
   ```

2. **Log liberally during development**:
   ```javascript
   console.log('[Debug]', JSON.stringify(item, null, 2));
   ```

3. **Test with edge cases**:
   - No metadata
   - All results have same score
   - Empty result sets

4. **Version your boost values** in comments:
   ```javascript
   // v1: TRUSTED_BOOST = 1.3
   // v2: TRUSTED_BOOST = 1.5 (improved relevance)
   const TRUSTED_BOOST = 1.5;
   ```

---

For full implementation guide, see [`n8n-ranking-guide.md`](./n8n-ranking-guide.md).
