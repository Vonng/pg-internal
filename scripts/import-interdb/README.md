# InterDB importer

The importer mirrors the public book pages listed in
`https://www.interdb.jp/pg/sitemap.xml`, plus the book home page.

It intentionally:

- fetches pages sequentially and assets with three bounded workers by default;
- caches source files under the ignored `.cache/interdb-pg/` directory;
- records source and output hashes in `sources/interdb-pg/manifest.yaml`;
- excludes the generated tags page;
- never commits, pushes, or deploys.

The converter requires Python 3, PyYAML, Pillow, Pandoc, and an extended Hugo
binary. The generated content is organized under the ignored top-level
`en/docs/chXX/` directory, with one chapter directory and one Markdown file per
source section. Deeper source pages such as 3.5.1 are flattened to filenames
such as `05-01.md`.

The repository also ignores `static/images/en/`, so neither the generated
English Markdown nor its downloaded image assets are included in commits.

Run the stages in order:

```bash
python3 scripts/import-interdb/import.py inventory --refresh
python3 scripts/import-interdb/import.py fetch-pages
python3 scripts/import-interdb/import.py fetch-assets --jobs 3
python3 scripts/import-interdb/import.py convert
python3 scripts/import-interdb/import.py validate
```

Use `--chapters 1,2,3` with `fetch-assets` or `convert` for a pilot run. Asset
downloads accept `--jobs 1` through `--jobs 4`.

Use `--refresh` only when deliberately taking a new upstream snapshot.

Run the importer regressions and the production-equivalent Hugo build with:

```bash
python3 -m unittest scripts/import-interdb/test_import.py -v
HUGO_ENVIRONMENT=production HUGO_ENV=production \
  hugo --config hugo.yaml,hugo.en.yaml --gc --minify --panicOnWarning
```

The latest machine-readable validation report is written to
`sources/interdb-pg/validation.yaml`.
