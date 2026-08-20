PYTHON ?= python3
PUBLIC_DIR ?= public
OINK_MODULE := github.com/pgsty/oink
OINK_LOCAL := $(HOME)/pgsty/oink
LOCAL_OINK := HUGO_MODULE_REPLACEMENTS="$(OINK_MODULE) -> $(OINK_LOCAL)"

default: dev

dev:
	$(LOCAL_OINK) hugo serve --disableFastRender

serve:
	hugo serve --environment production --minify --disableFastRender --disableLiveReload

build:
	hugo --gc --minify --printPathWarnings

build-local:
	$(LOCAL_OINK) hugo --gc --minify --printPathWarnings

check: build-check check-book

build-check:
	hugo --gc --minify --printPathWarnings --panicOnWarning --cleanDestinationDir
	$(PYTHON) scripts/check_links.py $(PUBLIC_DIR)
	$(PYTHON) scripts/check_site.py $(PUBLIC_DIR)

check-book:
	oink_dir="$$(go list -m -f '{{.Dir}}' $(OINK_MODULE))"; \
		$(PYTHON) "$$oink_dir/bin/check-book.py" --site-public $(PUBLIC_DIR)

check-local:
	$(LOCAL_OINK) hugo --gc --minify --printPathWarnings --panicOnWarning --cleanDestinationDir
	$(PYTHON) scripts/check_links.py $(PUBLIC_DIR)
	$(PYTHON) scripts/check_site.py $(PUBLIC_DIR)
	$(PYTHON) $(OINK_LOCAL)/bin/check-book.py --site-public $(PUBLIC_DIR)

.PHONY: default dev serve build build-local build-check check check-book check-local
