---
title: PostgreSQL技术内幕
cascade:
  type: docs
breadcrumbs: false
---


## 概览

PostgreSQL是一个开源的关系型数据库，在世界各地被广泛用于各种目的。它是一个由多个子系统集成而来的巨大系统，每个子系统都包含着特殊的复杂功能，并与其它子系统相互协调工作。
理解其内部原理对于管理和集成PostgreSQL而言至关重要，但其巨大性与复杂性让这一点变得相当困难。本书的主要目的是解释这些子系统是如何工作的，并提供一副关于PostgreSQL的全景图像。

## 地址

- 在线预览地址：[https://pgint.vonng.com](https://pgint.vonng.com)
- GitHub仓库：[https://github.com/Vonng/pg-internal](https://github.com/Vonng/pg-internal)
- GitHub Pages: [https://vonng.github.io/pg-internal](https://vonng.github.io/pg-internal)


##  目录

[序](/preface)

[译者序](/preface2)

[第一章 数据库集簇，数据库，数据表](/ch1)

[第二章 进程与内存体系结构](/ch2)

[第三章 查询处理](/ch3)

[第四章 外部数据源包装与并行查询](/ch4)

[第五章 并发控制](/ch5)

[第六章 清理过程](/ch6)

[第七章 HOT与仅索引扫描](/ch7)

[第八章 缓冲管理器](/ch8)

[第九章 预写式日志（WAL）](/ch9)

[第十章 基础备份与时间点恢复（PITR）](/ch10)

[第十一章 流复制](/ch11)


## 作者

**Hironobu Suzuki**
**日语：鈴木 啓修**


## 译者

[**冯若航**](https://github.com/Vonng)，刘阳明，张文升

探探 PostgreSQL DBA Team