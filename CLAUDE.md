# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Hugo-based documentation site for "PostgreSQL 技术内幕" (PostgreSQL Internals), a Chinese technical book about PostgreSQL database internals. The site uses the OINK theme and includes 12 chapters covering various aspects of PostgreSQL architecture and implementation.

## Architecture

- **Hugo Static Site**: Built with Hugo using the OINK theme (github.com/pgsty/oink)
- **Content Structure**: Main content in `content/` directory with chapters (ch1.md through ch12.md), preface files, and index
- **Static Assets**: Images stored in `static/img/` with systematic naming (fig-X-XX.png format)
- **Layouts**: Site overrides in `layouts/_partials/`
- **Multi-format Support**: Supports both Hugo static site generation and legacy Docsify serving

## Development Commands

### Primary Development
- `make dev` or `make` - Preview the current `~/pgsty/oink` checkout with Hugo Extended 0.168.0.
- `make dev OINK_LOCAL=/path/to/oink PORT=1314` - Override checkout and port.
- `make build`, `make serve`, `make check` - Use the published OINK version pinned in `go.mod`.
- `make check-local` - Run strict build, links, site, and Book checks against the current local theme.

Development bypasses OINK vendor files and both workspaces. Production targets clear inherited local replacements.

### Legacy Support
Historical Docsify utilities remain under `temp/`; they are separate from the maintained Hugo Makefile:
- `temp/bin/serve` - Serves with Docsify if available, falls back to Python SimpleHTTPServer on port 3001
- `temp/bin/build-html.sh` - HTML build script
- `temp/bin/build-docx.sh` - DOCX build script

## Configuration

- **Hugo Config**: `hugo.yaml` contains site configuration, theme settings, and Giscus comments integration
- **Go Module**: `go.mod` defines the OINK theme dependency
- **Base URL**: https://pg-internal.vonng.com/
- **Language**: Simplified Chinese (zh) with CJK language support enabled
- **Comments**: Integrated with Giscus for GitHub-based commenting

## Content Guidelines

- Chapters follow consistent naming: ch1.md, ch2.md, etc.
- Images use systematic naming: fig-{chapter}-{number}.png
- Content supports LaTeX math notation with KaTeX
- Search uses OINK offline search and the command palette
- Edit links point to GitHub repository for collaborative editing

## Build Output

- Generated site outputs to `public/` directory
- Includes search indices, CSS/JS assets, and static content
- Supports both light/dark themes with system preference detection
