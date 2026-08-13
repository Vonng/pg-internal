PYTHON ?= python3
PUBLIC_DIR ?= public

default: dev

dev:
	hugo serve --disableFastRender

build:
	hugo --gc --minify --printPathWarnings

check:
	hugo --gc --minify --printPathWarnings --panicOnWarning --cleanDestinationDir
	$(PYTHON) scripts/check_links.py $(PUBLIC_DIR)
	$(PYTHON) scripts/check_site.py $(PUBLIC_DIR)

.PHONY: default dev build check
