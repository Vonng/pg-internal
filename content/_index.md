---
title: PostgreSQL 技术内幕
description: 深入理解 PostgreSQL 的进程、查询、并发控制、存储、WAL、备份与复制机制。
type: book
layout: landing
book_kind: book
outputs: [HTML, print, markdown, LLMS]
search_exclude: true
search_keywords: [PostgreSQL 内核, 数据库原理, 技术内幕, PG 内核]
search_boost: 2
breadcrumb: false
navbar_autohide: true
cascade:
  type: book
  upstream_link: https://www.interdb.jp/pg/
  upstream_name: The Internals of PostgreSQL
  upstream_copyright: "© Copyright ALL Right Reserved, Hironobu SUZUKI."
  upstream_license: LicenseRef-InterDB
  upstream_notice: https://www.interdb.jp/pg/
  upstream_modified: true
  params:
    breadcrumb: false
---

《PostgreSQL 技术内幕》由 Hironobu Suzuki 原著，本站收录冯若航、刘阳明和张文升完成的 2018 年中文译稿。

全书从数据库集簇、进程和查询处理出发，依次进入并发控制、VACUUM、缓冲区、WAL、备份、时间点恢复与流复制。

## 阅读本书

- 阅读[完整目录](/toc/)定位十一章内容。
- 从[作者序](/preface/)或[第一章](/ch1/)开始顺序阅读。
- 打开[整书打印视图](/_print/)阅读或另存为 PDF。

## 版本说明

这份中文译稿主要反映 PostgreSQL 9.x 至 11 前后的实现。涉及当前 PostgreSQL 行为时，请同时查阅[持续更新的英文原著](https://www.interdb.jp/pg/)、[PostgreSQL 官方文档](https://www.postgresql.org/docs/current/)与源码。
