# WSI Slide + Feature Viewer

Simple local web viewer to check:

- Which slides exist under your slide roots
- Which feature files exist under your feature roots
- Which slides are missing features
- Which encoder folders produced matched features

## Run

```bash
cd /home/w8yi/wsi_slide_viewer
python3 app.py
```

Open `http://127.0.0.1:8080`.

For `.svs` thumbnail previews, run with your existing `wsi` env Python:

```bash
cd /home/w8yi/wsi_slide_viewer
/home/w8yi/miniforge3/envs/wsi/bin/python app.py
```

## Configure roots

Defaults:

- Slides: `/mnt/data/WSI_slides`, `/mnt/data/TCGA_slides`
- Features: `/mnt/data/WSI_features`, `/mnt/data/TCGA_features`, `/mnt/data/features-sea`

Override with env vars:

```bash
WSI_SLIDES_DIR=/mnt/data/WSI_slides \
WSI_FEATURES_DIRS=/mnt/data/TCGA_features,/mnt/data/features-sea \
WSI_VIEWER_PORT=8080 \
WSI_THUMB_TIMEOUT_SEC=10 \
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
