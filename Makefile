HUGO ?= hugo
OINK_LOCAL ?= $(HOME)/pgsty/oink
PORT ?= 1313

# Keep local development independent of module workspaces and vendored pins.
export GOWORK := off
export HUGO_MODULE_WORKSPACE := off
PINNED_HUGO = env -u HUGO_MODULE_REPLACEMENTS $(HUGO)
LOCAL_HUGO = HUGO_MODULE_REPLACEMENTS="github.com/pgsty/oink -> $(abspath $(OINK_LOCAL))" $(HUGO) --ignoreVendorPaths=github.com/pgsty/oink

PYTHON ?= python3
PUBLIC_DIR ?= public

default: dev

dev: theme-local
	$(LOCAL_HUGO) serve --disableFastRender --renderToMemory --bind 127.0.0.1 --port "$(PORT)" --noBuildLock

serve:
	$(PINNED_HUGO) serve --environment production --minify --disableFastRender --disableLiveReload

build:
	$(PINNED_HUGO) --gc --minify --printPathWarnings

build-local: theme-local
	$(LOCAL_HUGO) --gc --minify --printPathWarnings

check: build-check check-book

build-check:
	$(PINNED_HUGO) --gc --minify --printPathWarnings --cleanDestinationDir
	$(PYTHON) scripts/check_links.py $(PUBLIC_DIR)
	$(PYTHON) scripts/check_site.py $(PUBLIC_DIR)

check-book:
	oink_dir="$$(env -u HUGO_MODULE_REPLACEMENTS go list -m -f '{{.Dir}}' github.com/pgsty/oink)"; \
		PYTHONDONTWRITEBYTECODE=1 $(PYTHON) "$$oink_dir/bin/check-book.py" --site-public $(PUBLIC_DIR)

check-local: theme-local
	$(LOCAL_HUGO) --gc --minify --printPathWarnings --cleanDestinationDir
	$(PYTHON) scripts/check_links.py $(PUBLIC_DIR)
	$(PYTHON) scripts/check_site.py $(PUBLIC_DIR)
	$(PYTHON) "$(OINK_LOCAL)/bin/check-book.py" --site-public $(PUBLIC_DIR)

.PHONY: default dev serve build build-local build-check check check-book check-local

# Fail explicitly if a local theme was requested but is unavailable.
theme-local:
	@test -f "$(OINK_LOCAL)/hugo.yaml" || { echo "Set OINK_LOCAL to an OINK checkout (default: ~/pgsty/oink)." >&2; exit 1; }

.PHONY: theme-local
