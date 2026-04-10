# WSI Bench Viewer

Two local websites in one app:

- `/` QC dashboard for slide-feature coverage
- `/sae` SAE atlas for model stats, representative latent tiles, plot-ready analytics, and per-slide support evidence

## Run

```bash
cd /home/w8yi/wsi_slide_viewer
python3 app.py
```

Open:

- `http://127.0.0.1:8080/` for QC
- `http://127.0.0.1:8080/sae` for SAE inspector

For `.svs` thumbnail previews, run with your existing `wsi` env Python:

```bash
cd /home/w8yi/wsi_slide_viewer
/home/w8yi/miniforge3/envs/wsi/bin/python app.py
```

## Configure roots

Defaults:

- Slides: `/mnt/data/wsi_slides`
- Features: `/mnt/data/wsi_features`

Override with env vars:

```bash
WSI_SLIDES_DIR=/mnt/data/wsi_slides \
WSI_FEATURES_DIRS=/mnt/data/wsi_features \
WSI_VIEWER_PORT=8080 \
WSI_THUMB_TIMEOUT_SEC=10 \
WSI_SAE_MANIFEST=/home/w8yi/wsi_slide_viewer/config/sae_models.json \
WSI_SAE_TILE_CACHE_ROOT=/mnt/data/WSI_thumbs/sae_tiles \
python3 app.py
```

## Matching logic

Slide and feature files are matched using a slide ID parsed from filename, for example:

- `TCGA-5C-A9VH-01Z-00-DX1.XXXX.svs` -> `TCGA-5C-A9VH-01Z-00-DX1`
- `TCGA-5C-A9VH-01Z-00-DX1.h5` -> `TCGA-5C-A9VH-01Z-00-DX1`

If naming differs across encoders, adjust naming or update `to_slide_id()` in `app.py`.

## Thumbnail endpoint

- `GET /api/thumbnail?path=<absolute_slide_path>&size=768`
- Path must be inside configured slide roots.
- Supports `.svs` via OpenSlide and image files (`.jpg`, `.jpeg`, `.png`) via Pillow.

## SAE manifest

The SAE site is model-driven by `config/sae_models.json`.

Each model entry supports either the legacy prototype bundle fields or the new representative bundle fields.

Shared fields:

- `model_id`
- `model_name`
- `encoder`
- `dataset`
- `slides_root`
- `tile_size` (optional, default `256`)

Representative bundle fields:

- `representative_latents_csv`
- `representative_support_tiles_csv`
- `latent_summary_csv` (optional)
- `bundle_summary_json` (optional)

Optional analytics fields:

- `plot_manifest_json`
- `analytics_summary_json`
- `all_latent_metrics_csv`
- `selected_latent_slide_stats_csv`
- `cohort_enrichment_csv`
- `latent_umap_csv`
- `selected_latent_histograms_json`
- `case_label_enrichment_csv` (optional)

Optional materialized-output fields:

- `materialized_rows_csv`
- `materialized_contact_sheets_dir`

If these analytics paths are not listed explicitly, `wsi-bench` will automatically look for a sibling
`analytics_<split>/` directory next to the representative bundle and use it when present.

If `materialized_rows_csv` is present, `wsi-bench` will prefer saved local tile images before falling back to on-demand
tile rendering from `slides_root`. If `materialized_contact_sheets_dir` is present, the SAE atlas will also show saved
contact sheets in latent detail.

Legacy prototype bundle fields:

- `prototype_tiles_csv`
- `top_attention_tiles_csv` (optional)

## SAE APIs

- `GET /api/sae/models`
- `GET /api/sae/summary?model_id=...`
- `GET /api/sae/analytics?model_id=...`
- `GET /api/sae/latents?model_id=...&group=...&limit=...`
- `GET /api/sae/representatives?model_id=...&method=max_activation&strategy=top_activation&group=...&limit=...`
- `GET /api/sae/latent?model_id=...&latent_idx=...&strategy=top_activation&method=max_activation`
- `GET /api/sae/contact-sheet?model_id=...&latent_idx=...&strategy=top_activation&method=max_activation&size=768`
- `GET /api/sae/slides?model_id=...&q=...&limit=...`
- `GET /api/sae/slide?model_id=...&slide_key=...&method=max_activation&strategy=top_activation`
- `GET /api/sae/tile?model_id=...&slide_key=...&x=...&y=...&size=...&tile_index=...`

For representative bundles, `representatives` supports:

- `method=max_activation`
- `method=median_activation`
- `method=diverse_support`
- `method=slide_spread`

and can filter by `strategy`, for example:

- `strategy=top_activation`
- `strategy=top_variance`
- `strategy=top_sparsity`
- `strategy=sdf_parent_balanced`

Summary response includes interpretability-oriented fields such as:
- `rep_method`, `rep_latents`, `rep_slide_coverage`, `rep_mean_unique_slides_per_latent`
- `activation_p50`, `activation_p95`, `activation_tail_ratio`
- `latent_concentration_hhi`
