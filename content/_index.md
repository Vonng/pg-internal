---
title: PostgreSQL 技术内幕
description: 深入理解 PostgreSQL 的进程、查询、并发控制、存储、WAL、备份与复制机制。
search_keywords: [PostgreSQL 内核, 数据库原理, 技术内幕, PG 内核]
search_boost: 2
cascade:
  upstream_attribution: https://www.interdb.jp/pg/
  downstream_modified: true
  search_boost: 1.2
  ui:
    breadcrumb_disable: true
---

《PostgreSQL 技术内幕》由 Hironobu Suzuki 原著，本站收录冯若航、刘阳明和张文升完成的
2018 年中文译稿。正文按 PostgreSQL 的内部依赖关系组织，从数据库集簇、进程与查询处理，
延伸到并发控制、缓冲区、WAL、备份和流复制。

## 阅读入口

- [作者序](/preface/)
- [译者序](/preface2/)
- [第一章：数据库集簇、数据库与数据表](/ch1/)
- [全部章节](./#chapters)

## 版本说明

这份中文译稿主要反映 PostgreSQL 9.x 至 11 前后的实现。英文原著仍在持续更新；涉及当前
PostgreSQL 行为时，请同时查阅[英文原著](https://www.interdb.jp/pg/)和
[PostgreSQL 官方文档](https://www.postgresql.org/docs/current/)。

翻译、技术或链接问题可在
[GitHub Issues](https://github.com/Vonng/pg-internal/issues) 中反馈。
