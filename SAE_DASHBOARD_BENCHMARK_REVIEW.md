# SAE Dashboard Benchmark Review (InterPLM-Inspired + Current SAE Literature)

## Why this doc
You asked for a dashboard similar to the InterPLM feature visualization style, plus a review of what to add beyond it for WSI SAE analysis.

This document summarizes:
- what InterPLM-style UI elements are useful,
- what we can map directly to WSI slide/tile SAE data,
- what additional metrics are missing (and worth adding),
- what current SAE research suggests we should adapt.


## 1) What the InterPLM-style page is showing
Based on your screenshot and the InterPLM paper/website description, the page emphasizes:

- Global feature-level metrics and selection:
  - feature activation frequency view,
  - effect-size scatter (structural vs sequential in proteins),
  - UMAP of feature embeddings/values,
  - concept table with precision/recall/F1-like quality indicators.
- Per-feature deep dive:
  - natural-language description,
  - activation distribution histogram,
  - concept evidence table.

For WSI SAE, the analogous pattern is:
- **Global latent map + latent quality table**.
- **Per-latent evidence panel** (top tiles, cohort spread, activation histogram, auto-description).


## 2) What we already have in this viewer
Current SAE Inspector already supports:
- representative latent tiles (`max_activation` method),
- latent group filtering,
- per-latent/slide drill-down,
- model summary stats:
  - `rep_latents`, `rep_slide_coverage`,
  - `activation_p50`, `activation_p95`, `activation_tail_ratio`,
  - `latent_concentration_hhi`.

This is a good base but still missing several InterPLM-like analytical views.


## 3) Recommended stats/views to add (prioritized)

### P0: Add now (works with current prototype CSV)
These are possible without retraining.

1. **Activation frequency curve (latent prevalence)**
- For each latent: fraction of slides with at least one firing tile.
- UI: scatter or sorted line (`x=latent rank`, `y=slide coverage`).

2. **Activation distribution panel**
- Histogram/KDE for selected latent activation values.
- Show median, p90, p99, max.

3. **Cohort enrichment table**
- For each latent: top cohorts by normalized prevalence.
- Include enrichment score vs global cohort baseline.

4. **Latent quality scorecard**
- Composite score with transparent components:
  - coverage, concentration, activation sharpness, cohort specificity.

5. **Representative tile diversity**
- For selected latent: unique cases/slides among top-K tiles.
- Prevent “single-slide dominance” looking falsely strong.

### P1: Add with moderate backend changes
1. **Latent similarity map (UMAP/t-SNE)**
- Build latent embeddings from co-activation patterns or decoder vectors.
- UI: interactive map; click cluster -> latent list.

2. **Concept table (InterPLM-like)**
- Replace protein concepts with:
  - pathology tags, cohort labels, tissue-type labels, QC categories.
- Report precision/recall/F1 per latent-concept pair.

3. **Natural language latent descriptions**
- LLM-generated latent summaries from top-activating tile metadata/text tags.
- Store confidence + supporting exemplars.

### P2: Add when dense activations are available (not just top tiles)
1. **Ablation/sensitivity proxies**
- Estimate downstream effect sparsity or output sensitivity.

2. **Stability across seeds / runs**
- Same-latent overlap across SAE retrains.

3. **Feature splitting / absorption diagnostics**
- Detect hierarchy-like latent fragmentation and parent/child suppression.


## 4) What InterPLM-style page still misses (and we should include)
InterPLM-style dashboards are strong for exploration, but for robust SAE interpretation we should add:

1. **Reliability / uncertainty**
- confidence intervals on precision/recall/enrichment.

2. **Data support counts**
- show N slides/cases/tiles behind each metric to avoid over-reading.

3. **Failure mode flags**
- dead latents, near-duplicate latents, single-source artifacts.

4. **Drift monitoring**
- latent prevalence shifts across cohorts/sites/time.

5. **Intervention readiness panel**
- mark latents that are likely actionable vs descriptive-only.


## 5) Research signals we should adapt

### A) Core SAE interpretability foundation
- **Sparse Autoencoders Find Highly Interpretable Features in Language Models (2023)**: established feature-level interpretability framing over neuron-level analysis.

### B) Scaling + evaluation metrics
- **Scaling and evaluating sparse autoencoders (ICLR 2025)**: recommends evaluating beyond reconstruction with:
  - downstream loss,
  - probe-style recovery metrics,
  - explainability metrics,
  - ablation effect sparsity.
- Practical takeaway: keep dashboard metrics tied to feature usefulness, not just activation magnitude.

### C) Auto-interpretability at scale
- **Automatically Interpreting Millions of Features (2024)**:
  - scalable explanation generation + scoring,
  - intervention-based scoring.
- Practical takeaway: build a latent description + falsification loop, not one-shot captions.

### D) Known failure modes
- **A is for Absorption (NeurIPS 2025 oral)**:
  - warns about feature splitting/absorption,
  - scaling width alone does not guarantee robust monosemanticity.
- Practical takeaway: add diagnostics that detect apparent “good” latents that fail coverage consistency.

### E) Domain-specific precedent (biology)
- **InterPLM (Nature Methods 2025)**:
  - concept-association benchmarking,
  - automatic feature descriptions,
  - interactive feature explorer for biological interpretation.
- Practical takeaway: replicate concept benchmarking pattern for WSI pathology concepts.


## 6) Proposed WSI SAE metric set (v1)
Use this as canonical dashboard metric schema.

### Global model metrics
- `num_latents_alive`
- `dead_latent_ratio`
- `slide_coverage_by_any_latent`
- `median_latent_slide_coverage`
- `activation_p50`, `activation_p95`, `activation_tail_ratio`
- `latent_concentration_hhi`
- `top10_latent_mass_share`

### Per-latent metrics
- `num_tiles`, `num_slides`, `num_cases`
- `activation_mean`, `activation_p90`, `activation_p99`, `activation_max`
- `cohort_entropy` (or Gini)
- `cohort_enrichment_topk`
- `representative_diversity_score`
- `description_confidence` (if auto-interpretation enabled)

### Reliability metrics
- `support_n` (slides/cases/tiles)
- bootstrap CI for key rates
- run-to-run overlap (when multi-run available)


## 7) Implementation plan (pragmatic)

### Phase 1 (quick upgrade, no retraining)
- Add activation histogram + prevalence curve + cohort enrichment table.
- Add latent quality scorecard and support counts.
- Add “data support warning” badges for low-N latents.

### Phase 2 (analysis richness)
- Add latent similarity map (UMAP).
- Add concept metrics table (precision/recall/F1 against available labels).
- Add optional auto-descriptions with evidence cards.

### Phase 3 (research-grade robustness)
- Add absorption/splitting diagnostics.
- Add cross-run stability panel.
- Add intervention/falsification-ready scoring hooks.


## 8) Notes on data prerequisites
- Current toy setup uses top-activation prototype tiles; this is enough for P0 exploration metrics.
- P1/P2 reliability requires denser activation dumps and/or labeled concept metadata.
- For concept precision/recall, we need trusted labels at slide/case/tile level (or weak labels with confidence).


## References
- InterPLM paper: https://www.nature.com/articles/s41592-025-02836-7
- InterPLM site (interactive platform mention): https://interplm.ai
- Sparse Autoencoders Find Highly Interpretable Features in Language Models (2023): https://arxiv.org/abs/2309.08600
- Towards Monosemanticity / decomposition overview (Anthropic): https://www.anthropic.com/research/decomposing-language-models-into-understandable-components
- Scaling and evaluating sparse autoencoders (ICLR 2025): https://proceedings.iclr.cc/paper_files/paper/2025/hash/42ef3308c230942d223c411adf182c88-Abstract-Conference.html
- OpenAI SAE paper PDF: https://cdn.openai.com/papers/sparse-autoencoders.pdf
- Engineering challenges of scaling interpretability (Anthropic): https://www.anthropic.com/research/engineering-challenges-interpretability
- Automatically Interpreting Millions of Features in LLMs (2024): https://arxiv.org/abs/2410.13928
- A is for Absorption (NeurIPS 2025 oral): https://arxiv.org/abs/2409.14507
- Unveiling Language-Specific Features via SAEs (ACL 2025): https://aclanthology.org/2025.acl-long.229/
- SAEs in VLMs (arXiv 2025): https://arxiv.org/abs/2504.02821
