default: dev

d:dev
dev:
	hugo serve

b:build
build:
	hugo build

.PHONY: default d dev b build
